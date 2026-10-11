"""吸附與串接共用的 route 規則（最終審查 I-1）：塌縮、端點偏離、找不到路都回 None。"""

import pytest

from engine.matching.deadline import Deadline, MatchTimeout
from engine.matching.types import legal_route
from engine.routing.geo import haversine_m
from engine.routing.router import RouteResult, RoutingError

LAT = 25.05
START, END = (121.50, LAT), (121.51, LAT)
M_PER_DEG_LAT = 110_574


class ShiftRouter:
    """route 的起點往北移 start_m、終點往北移 end_m；collapse=True 時整條塌成一點；fail=True 時找不到路。"""

    def __init__(self, start_m: float = 0.0, end_m: float = 0.0, *, collapse: bool = False, fail: bool = False) -> None:
        self.start_m, self.end_m, self.collapse, self.fail = start_m, end_m, collapse, fail
        self.calls = 0
        self.shortest: list[bool] = []

    def route(self, start, end, *, shortest=False):
        self.shortest.append(shortest)
        self.calls += 1
        if self.fail:
            raise RoutingError("no path")
        if self.collapse:
            return RouteResult(points=[start, start], length_km=0.0)
        a = (start[0], start[1] + self.start_m / M_PER_DEG_LAT)
        b = (end[0], end[1] + self.end_m / M_PER_DEG_LAT)
        return RouteResult(points=[a, b], length_km=haversine_m(a, b) / 1000)

    def trace(self, points, *, search_radius_m):
        raise AssertionError("legal_route 不呼叫 trace")


def call(router, tolerance_m: float = 1.0, deadline: Deadline | None = None):
    return legal_route(router, deadline or Deadline(10), START, END, tolerance_m)


@pytest.mark.parametrize(
    "start_m, end_m, tolerance_m, accepted",
    [
        (0.5, 0.5, 1.0, True),
        (3.0, 0.0, 1.0, False),  # 接兩段路（JOIN_TOLERANCE_M）：差 3 m 就是缺口
        (0.0, 3.0, 1.0, False),
        (90.0, 90.0, 100.0, True),  # 繞路候選：原始輸入點可以吸附到 2r 內
        (101.0, 0.0, 100.0, False),
        (0.0, 101.0, 100.0, False),
    ],
)
def test_endpoints_must_be_within_tolerance(start_m: float, end_m: float, tolerance_m: float, accepted: bool) -> None:
    assert (call(ShiftRouter(start_m, end_m), tolerance_m) is not None) == accepted


def test_collapsed_route_is_rejected() -> None:
    assert call(ShiftRouter(collapse=True), 100.0) is None


def test_routing_error_is_none() -> None:
    assert call(ShiftRouter(fail=True)) is None


def test_deadline_checked_before_calling_valhalla() -> None:
    router = ShiftRouter()
    expired = Deadline(1, clock=iter([0.0, 5.0]).__next__)
    with pytest.raises(MatchTimeout):
        call(router, deadline=expired)
    assert router.calls == 0


def test_matching_routes_ask_for_the_shortest_path() -> None:
    # 繞路、橋接、connector 都經過 try_route：一律最短，只換成本、不換通行規則
    router = ShiftRouter()
    assert call(router) is not None
    assert router.shortest == [True]
