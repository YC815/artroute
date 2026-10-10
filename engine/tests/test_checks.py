from engine.data.checks import ZHOUMEI_EAST, ZHOUMEI_ONEWAY_WAY_ID, ZHOUMEI_WEST, taiwan_checks
from engine.routing.router import EdgeInfo, RouteResult

WEST_TO_EAST = RouteResult(points=[ZHOUMEI_WEST, ZHOUMEI_EAST], length_km=0.4)
EAST_TO_WEST = RouteResult(points=[ZHOUMEI_EAST, (121.5, 25.102), ZHOUMEI_WEST], length_km=0.9)


class FakeRouter:
    """順向路線回 forward_edges，逆向路線回 backward_edges。"""

    def __init__(self, forward_edges: list[EdgeInfo], backward_edges: list[EdgeInfo]) -> None:
        self._forward_edges = forward_edges
        self._backward_edges = backward_edges

    def route(self, start, end) -> RouteResult:
        return WEST_TO_EAST if start == ZHOUMEI_WEST else EAST_TO_WEST

    def edges(self, points) -> list[EdgeInfo]:
        return self._forward_edges if points == WEST_TO_EAST.points else self._backward_edges


def test_passes_when_forward_uses_oneway_and_backward_avoids_it() -> None:
    router = FakeRouter([EdgeInfo(ZHOUMEI_ONEWAY_WAY_ID, 90.0)], [EdgeInfo(1, 0.0), EdgeInfo(2, 270.0)])
    assert taiwan_checks(router) == []


def test_fails_when_backward_rides_oneway_westward() -> None:
    router = FakeRouter([EdgeInfo(ZHOUMEI_ONEWAY_WAY_ID, 90.0)], [EdgeInfo(ZHOUMEI_ONEWAY_WAY_ID, 270.0)])
    assert any("逆向" in f for f in taiwan_checks(router))


def test_fails_loudly_when_way_disappears_from_osm() -> None:
    router = FakeRouter([EdgeInfo(1, 90.0)], [EdgeInfo(2, 270.0)])
    assert any("OSM" in f for f in taiwan_checks(router))
