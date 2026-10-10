"""接點轉彎檢查（不需要 Valhalla）。"""

import pytest

from engine.matching.deadline import Deadline, MatchTimeout
from engine.matching.turns import TURN_PROBE_M, head_points, rideable, tail_points, turn_deg
from engine.matching.types import Segment
from engine.routing.geo import haversine_m, polyline_length_m
from engine.routing.router import RouteResult, RoutingError
from turn_router import TurnBlockRouter, east, north

LAT = 25.05
J = (121.507, LAT)
INCOMING = [east(J, -200), east(J, -100), J]  # 由西往東到 J
STRAIGHT = [J, east(J, 100), east(J, 200)]  # 繼續往東
LEFT = [J, north(J, 100), north(J, 200)]  # 在 J 左轉往北
BACK = [J, east(J, -100)]  # 原地迴轉


def seg(points) -> Segment:
    return Segment("stroke", 0, points, polyline_length_m(points))


def test_turn_deg() -> None:
    assert turn_deg(east(J, -10), J, east(J, 10)) == pytest.approx(0, abs=0.01)
    assert turn_deg(east(J, -10), J, north(J, 10)) == pytest.approx(90, abs=0.5)
    assert turn_deg(east(J, -10), J, east(J, -20)) == pytest.approx(180, abs=0.01)


def test_going_straight_needs_no_route() -> None:
    router = TurnBlockRouter(blocked=J)
    assert rideable(router, Deadline(10), INCOMING, STRAIGHT)
    assert router.route_calls == []


def test_turn_valhalla_rides_directly_is_rideable() -> None:
    router = TurnBlockRouter(blocked=None)
    assert rideable(router, Deadline(10), INCOMING, LEFT)
    ((p, q),) = router.route_calls
    assert haversine_m(p, J) == pytest.approx(TURN_PROBE_M, abs=0.1)
    assert haversine_m(q, J) == pytest.approx(TURN_PROBE_M, abs=0.1)


def test_turn_valhalla_would_not_ride_is_rejected() -> None:
    assert not rideable(TurnBlockRouter(blocked=J), Deadline(10), INCOMING, LEFT)


def test_u_turn_is_not_checked() -> None:
    router = TurnBlockRouter(blocked=J)
    assert rideable(router, Deadline(10), INCOMING, BACK)
    assert router.route_calls == []


def test_too_short_to_measure_is_not_checked() -> None:
    router = TurnBlockRouter(blocked=J)
    assert rideable(router, Deadline(10), [east(J, -0.5), J], LEFT)
    assert router.route_calls == []


class FailingRouter:
    def route(self, start, end, *, shortest=False):
        raise RoutingError("no path")


class ElsewhereRouter:
    """route 的起點被吸到 5 m 外：長度再短也不能當證據。"""

    def route(self, start, end, *, shortest=False):
        moved = north(start, 5)
        return RouteResult(points=[moved, end], length_km=haversine_m(moved, end) / 1000)


def test_no_route_or_route_from_elsewhere_is_not_rideable() -> None:
    assert not rideable(FailingRouter(), Deadline(10), INCOMING, LEFT)
    assert not rideable(ElsewhereRouter(), Deadline(10), INCOMING, LEFT)


def test_expired_deadline_is_not_swallowed() -> None:
    expired = Deadline(1, clock=iter([0.0, 5.0, 5.0]).__next__)
    with pytest.raises(MatchTimeout):
        rideable(TurnBlockRouter(blocked=None), expired, INCOMING, LEFT)


def test_probes_walk_across_a_tiny_segment() -> None:
    # 0.5 m 的 link 不能把轉彎藏起來：探針要跨過它
    tiny = seg([J, north(J, 0.5)])
    up = seg([north(J, 0.5), north(J, 200)])
    assert haversine_m(head_points([tiny, up])[-1], J) >= TURN_PROBE_M
    assert not rideable(TurnBlockRouter(blocked=J), Deadline(10), INCOMING, head_points([tiny, up]))


def test_tail_points_stop_once_long_enough() -> None:
    far, near = seg([east(J, -300), east(J, -100)]), seg([east(J, -100), J])
    assert tail_points([far, near]) == near.points
