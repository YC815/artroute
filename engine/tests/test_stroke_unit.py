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
    """trace：只要點落在 bad 區間就失敗；route：直接回兩端點，但端點落在 dead 區間就失敗。"""

    def __init__(self, bad: tuple[float, float] | None = None, dead: tuple[float, float] | None = None) -> None:
        self.bad, self.dead = bad, dead
        self.trace_calls = 0

    @staticmethod
    def _inside(lon: float, zone: tuple[float, float] | None) -> bool:
        return zone is not None and zone[0] <= lon <= zone[1]

    def trace(self, points, *, search_radius_m):
        self.trace_calls += 1
        if any(self._inside(lon, self.bad) for lon, _ in points):
            raise RoutingError("bad")
        return RouteResult(points=list(points), length_km=polyline_length_m(points) / 1000)

    def route(self, start, end):
        lo, hi = sorted((start[0], end[0]))
        if self.dead is not None and lo <= self.dead[1] and hi >= self.dead[0]:  # 直線經過 dead 區間就找不到路
            raise RoutingError("dead")
        return RouteResult(points=[start, end], length_km=haversine_m(start, end) / 1000)


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
