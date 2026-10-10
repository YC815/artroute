"""分治吸附的流程，用假的 router 驗證：不需要 Valhalla。"""

import pytest

from engine.matching.deadline import Deadline, MatchTimeout
from engine.matching.stroke import densify, match_stroke
from engine.matching.types import JOIN_TOLERANCE_M
from engine.routing.geo import haversine_m, polyline_length_m
from engine.routing.router import RouteResult, RoutingError

LAT = 25.05


def line(lon_from: float, lon_to: float, n: int = 40) -> list[tuple[float, float]]:
    return [(lon_from + (lon_to - lon_from) * i / n, LAT) for i in range(n + 1)]


class FakeRouter:
    """trace：只要點落在 bad 區間就失敗；route：直接回兩端點，但直線經過 dead 區間，
    或兩端相距超過 max_route_m 就失敗。"""

    def __init__(
        self,
        bad: tuple[float, float] | None = None,
        dead: tuple[float, float] | None = None,
        max_route_m: float | None = None,
    ) -> None:
        self.bad, self.dead, self.max_route_m = bad, dead, max_route_m
        self.trace_calls = 0
        self.route_calls = 0

    @staticmethod
    def _inside(lon: float, zone: tuple[float, float] | None) -> bool:
        return zone is not None and zone[0] <= lon <= zone[1]

    def trace(self, points, *, search_radius_m):
        self.trace_calls += 1
        if any(self._inside(lon, self.bad) for lon, _ in points):
            raise RoutingError("bad")
        return RouteResult(points=list(points), length_km=polyline_length_m(points) / 1000)

    def route(self, start, end):
        self.route_calls += 1
        if self.max_route_m is not None and haversine_m(start, end) > self.max_route_m:
            raise RoutingError("too long")
        lo, hi = sorted((start[0], end[0]))
        if self.dead is not None and lo <= self.dead[1] and hi >= self.dead[0]:  # 直線經過 dead 區間就找不到路
            raise RoutingError("dead")
        return RouteResult(points=[start, end], length_km=haversine_m(start, end) / 1000)


class SnapRouter(FakeRouter):
    """trace 回傳的點整體往北偏 snap_lat：模擬吸附到道路後，端點不等於輸入點。"""

    def __init__(self, snap_lat: float, **kwargs) -> None:
        super().__init__(**kwargs)
        self.snap_lat = snap_lat

    def trace(self, points, *, search_radius_m):
        result = super().trace(points, search_radius_m=search_radius_m)
        shifted = [(lon, lat + self.snap_lat) for lon, lat in result.points]
        return RouteResult(points=shifted, length_km=result.length_km)


def run(router, points, deadline=None):
    return match_stroke(
        router, points, stroke_index=3, search_radius_m=50, min_piece_m=100,
        deadline=deadline or Deadline(60),
    )


def assert_chain_continuous(chain) -> None:
    for a, b in zip(chain, chain[1:]):
        assert haversine_m(a.points[-1], b.points[0]) <= JOIN_TOLERANCE_M


def test_densify_spacing_and_endpoints() -> None:
    pts = densify([(121.5, LAT), (121.51, LAT)], step_m=25)
    assert pts[0] == (121.5, LAT) and pts[-1] == (121.51, LAT)
    assert max(haversine_m(a, b) for a, b in zip(pts, pts[1:])) <= 25.0001


def test_legal_stroke_is_one_trace_segment() -> None:
    router = FakeRouter()
    result = run(router, line(121.50, 121.52))
    assert [[s.kind for s in c] for c in result.chains] == [["stroke"]]
    assert result.chains[0][0].stroke_index == 3
    assert router.trace_calls == 1
    assert result.warnings == []


def test_bad_middle_becomes_detour_and_shape_elsewhere_is_kept() -> None:
    router = FakeRouter(bad=(121.509, 121.511))
    result = run(router, line(121.50, 121.52))
    assert len(result.chains) == 1
    chain = result.chains[0]
    assert_chain_continuous(chain)
    kinds = {s.kind for s in chain}
    assert kinds == {"stroke", "detour"}
    stroke_m = sum(s.length_m for s in chain if s.kind == "stroke")
    assert stroke_m > 0.7 * polyline_length_m(line(121.50, 121.52))
    assert router.trace_calls < 40  # 分治只在壞的地方細切
    assert [s.kind for s in chain].count("detour") == 1  # 連續失敗的小段合併成一次繞路
    assert router.route_calls == 1
    assert result.warnings == []


def test_failed_run_falls_back_to_individual_routes() -> None:
    # 壞區約 300 m 寬，但單次 route 最多 150 m：整串繞不過去，要退回逐段繞
    router = FakeRouter(bad=(121.5085, 121.5115), max_route_m=150)
    result = run(router, line(121.50, 121.52))
    assert len(result.chains) == 1
    chain = result.chains[0]
    assert_chain_continuous(chain)
    detours = [s for s in chain if s.kind == "detour"]
    assert len(detours) > 1
    assert all(s.length_m <= 150.5 for s in detours)
    assert {s.kind for s in chain} == {"stroke", "detour"}
    assert result.warnings == []


def test_unroutable_piece_splits_chain_and_warns_partial() -> None:
    router = FakeRouter(bad=(121.509, 121.511), dead=(121.509, 121.511))
    result = run(router, line(121.50, 121.52))
    assert len(result.chains) == 2
    for chain in result.chains:
        assert_chain_continuous(chain)
    assert [w.code for w in result.warnings] == ["partial"]
    assert result.warnings[0].stroke_index == 3


def test_nothing_routable_warns_no_route() -> None:
    router = FakeRouter(bad=(121.0, 122.0), dead=(121.0, 122.0))
    result = run(router, line(121.50, 121.52))
    assert result.chains == []
    assert [w.code for w in result.warnings] == ["no_route"]


def test_deadline_exceeded_raises_timeout() -> None:
    ticks = iter([0.0, 10.0, 10.0, 10.0])
    deadline = Deadline(5, clock=lambda: next(ticks))
    with pytest.raises(MatchTimeout):
        run(FakeRouter(), line(121.50, 121.52), deadline=deadline)


def test_merged_detour_joins_the_neighbours_snapped_endpoints() -> None:
    # trace 的端點會被吸附到道路上，跟輸入點不同；繞路必須從前一段的實際終點接到後一段的實際起點，
    # 否則 _link 得再補橋，而在單行道上往回補橋就是再繞一整圈街區
    router = SnapRouter(snap_lat=0.0001, bad=(121.509, 121.511))  # 偏移約 11 m，超過 JOIN_TOLERANCE_M
    result = run(router, line(121.50, 121.52))
    assert len(result.chains) == 1
    chain = result.chains[0]
    assert_chain_continuous(chain)
    kinds = [s.kind for s in chain]
    assert kinds.count("detour") == 1
    i = kinds.index("detour")
    assert chain[i].points[0] == chain[i - 1].points[-1]
    assert chain[i].points[-1] == chain[i + 1].points[0]
    assert router.route_calls == 1  # 沒有補橋
    assert result.warnings == []


def test_closed_stroke_with_failing_trace_is_not_collapsed_to_zero_length() -> None:
    out = line(121.50, 121.51)
    closed = out + out[::-1][1:]  # 起點與終點相同的封閉筆畫
    result = run(FakeRouter(bad=(121.0, 122.0)), closed)
    segments = [s for chain in result.chains for s in chain]
    assert segments, "不能整段消失"
    assert all(s.length_m > 0 for s in segments)  # route(p, p) 塌縮成 0 m 的繞路不算數
    assert sum(s.length_m for s in segments) >= 0.7 * polyline_length_m(closed)
    for chain in result.chains:
        assert_chain_continuous(chain)


def test_corner_cutting_run_is_not_replaced_by_one_straight_detour() -> None:
    # U 形：起訖直線只有 330 m，畫出來的有 3.3 km；整串繞路太短代表在切角，要拆開重繞
    hairpin = [(121.50, LAT), (121.515, LAT), (121.515, LAT + 0.003), (121.50, LAT + 0.003)]
    result = run(FakeRouter(bad=(121.0, 122.0)), hairpin)
    assert len(result.chains) == 1
    chain = result.chains[0]
    assert_chain_continuous(chain)
    detours = [s for s in chain if s.kind == "detour"]
    assert len(detours) > 1
    assert not any(d.points[0] == hairpin[0] and d.points[-1] == hairpin[-1] for d in detours)
    assert sum(d.length_m for d in detours) >= 0.7 * polyline_length_m(hairpin)


def test_single_point_stroke_warns_no_route() -> None:
    # 只有一個點：route(p, p) 回傳同一點，長度 0，不能當成成功
    result = run(FakeRouter(bad=(121.0, 122.0)), [(121.5, LAT)])
    assert result.chains == []
    assert [w.code for w in result.warnings] == ["no_route"]
