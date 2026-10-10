"""分治吸附的流程，用假的 router 驗證：不需要 Valhalla。"""

import pytest

from engine.matching.deadline import Deadline, MatchTimeout
from engine.matching.stroke import densify, match_stroke
from engine.matching.types import JOIN_TOLERANCE_M
from engine.routing.geo import haversine_m, polyline_length_m
from engine.routing.router import RouteResult, RoutingError
from island_router import IslandRouter, across_island

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


class FarSnapRouter(FakeRouter):
    """route 的起點、終點分別被吸附到往北 start_m、end_m 之外：模擬附近沒有可騎道路，Valhalla 抓到遠處的路。"""

    def __init__(self, start_m: float, end_m: float, **kwargs) -> None:
        super().__init__(**kwargs)
        self.start_deg, self.end_deg = start_m / 110_574, end_m / 110_574

    def route(self, start, end):
        result = super().route(start, end)
        (lon0, lat0), (lon1, lat1) = result.points[0], result.points[-1]
        return RouteResult(
            points=[(lon0, lat0 + self.start_deg), (lon1, lat1 + self.end_deg)], length_km=result.length_km
        )


@pytest.mark.parametrize(
    "start_m, end_m, accepted",
    [(90, 90, True), (110, 0, False), (0, 110, False), (110, 110, False)],
)
def test_route_snapped_beyond_twice_the_search_radius_is_a_failure(start_m: float, end_m: float, accepted: bool) -> None:
    # search_radius_m=50 → 容許 100 m；起點或終點離要求的位置更遠，代表附近沒有路，不能收下遠處的繞路
    result = run(FarSnapRouter(start_m, end_m, bad=(121.0, 122.0)), line(121.50, 121.51))
    if accepted:
        assert [s.kind for c in result.chains for s in c] == ["detour"]
        assert result.warnings == []
    else:
        assert result.chains == []
        assert [w.code for w in result.warnings] == ["no_route"]


# ---- 最終審查 I-1：橋接線與繞路的端點必須真的接在相鄰路段上，否則接點會變成一條直線 ----


def test_island_endpoint_never_leaves_a_straight_gap_inside_a_chain() -> None:
    # 島上那段 trace 的終點 route 不到：route 從主路上另一點出發（約 36 m 外，仍在 2r 內）。
    # 舊版把這條 route 當橋接收下，chain 內就有一個 36 m 的缺口，_route_points 再把它畫成直線
    router = IslandRouter()
    result = run(router, across_island())
    assert result.chains
    for chain in result.chains:
        assert_chain_continuous(chain)
    assert [w.code for w in result.warnings] == ["partial"]


class ShortcutRouter(FakeRouter):
    """route 回傳的長度只有 1 m：任何繞路都會被 MIN_LENGTH_RATIO 拒絕（模擬塌縮、切角）。"""

    def route(self, start, end):
        result = super().route(start, end)
        return RouteResult(points=result.points, length_km=0.001)


def test_rejected_detour_is_not_readmitted_as_a_bridge() -> None:
    # ledger L34：繞路被 MIN_LENGTH_RATIO 拒絕後，舊版 _link 用同一條 route 橋接同一個缺口並收下，
    # 等於繞過「切角／塌縮不能收」的規則。被拒絕的那一段要讓 chain 斷開
    router = ShortcutRouter(bad=(121.509, 121.511))
    result = run(router, line(121.50, 121.52))
    assert len(result.chains) == 2
    assert all(s.kind == "stroke" for chain in result.chains for s in chain)
    assert [w.code for w in result.warnings] == ["partial"]


def test_split_point_of_a_failed_run_is_an_input_point_and_may_snap() -> None:
    # 整串繞不過去時從中間的輸入點切開重試：切點是原始輸入點（不在路上），route 可以把它吸附到 2r 內；
    # 兩半從同一個輸入點出發，吸附結果一致，chain 照樣連續
    router = FarSnapRouter(40, 40, bad=(121.0, 122.0), max_route_m=600)
    result = run(router, line(121.50, 121.51))
    assert len(result.chains) == 1
    assert_chain_continuous(result.chains[0])
    assert [s.kind for s in result.chains[0]] == ["detour", "detour"]
    assert result.warnings == []


class SplitSnapRouter(FakeRouter):
    """整條 trace 失敗、左右兩半各自成功，但兩半在切點各自吸到不同的位置（北 2 m、南 2 m）；
    route 的終點若是右半的起點，會被吸到 5 m 外（右半開頭落在 route 用不了的 edge 上）。"""

    MID = 121.51
    SHIFT = 2 / 110_574

    def trace(self, points, *, search_radius_m):
        self.trace_calls += 1
        lons = [lon for lon, _ in points]
        if min(lons) < self.MID - 1e-9 and max(lons) > self.MID + 1e-9:  # 容許切點本身的浮點誤差
            raise RoutingError("跨過切點")
        shift = self.SHIFT if sum(lons) / len(lons) < self.MID else -self.SHIFT
        snapped = [(lon, lat + shift) for lon, lat in points]
        return RouteResult(points=snapped, length_km=polyline_length_m(snapped) / 1000)

    def route(self, start, end):
        self.route_calls += 1
        right_start = (self.MID, LAT - self.SHIFT)
        if haversine_m(end, right_start) <= 5:
            end = (end[0], end[1] - 5 / 110_574)
        return RouteResult(points=[start, end], length_km=haversine_m(start, end) / 1000)


def test_bridge_retries_with_a_trimmed_head_before_breaking_the_chain() -> None:
    # 補橋到不了右半的起點：先把右半的開頭沿吸附結果裁掉 20 m 再接，接得上就不必斷開
    result = run(SplitSnapRouter(), line(121.50, 121.52))
    assert len(result.chains) == 1
    chain = result.chains[0]
    assert_chain_continuous(chain)
    assert [s.kind for s in chain] == ["stroke", "detour", "stroke"]
    assert chain[2].points[0][0] > SplitSnapRouter.MID  # 右半的開頭被裁掉了
    assert result.warnings == []


def test_detour_that_cannot_join_the_next_piece_still_ends_the_chain() -> None:
    # 繞路的終點落在下一段起點 5 m 外（仍在 2r 內），補橋也接不上：繞路照樣收下、當這條 chain 的結尾，
    # 下一段另起一條 chain。只有接點要 ≤ 1 m；把繞路本身丟掉只會少掉一段合法的路線（hi@taipei_riverside 實測 16 → 10）
    router = FarSnapRouter(0, 5, bad=(121.509, 121.511))
    result = run(router, line(121.50, 121.52))
    assert len(result.chains) == 2
    for chain in result.chains:
        assert_chain_continuous(chain)
    assert result.chains[0][-1].kind == "detour"
    assert [w.code for w in result.warnings] == ["partial"]
