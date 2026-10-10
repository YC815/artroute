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


def test_zero_length_arm_is_not_straight() -> None:
    assert turn_deg(J, J, north(J, 10)) == 180
    assert turn_deg(east(J, -10), J, J) == 180


def test_probe_that_folds_back_onto_the_junction_is_not_rideable() -> None:
    # incoming 最後 8 m 是 J→東 4 m→J 的來回：往回走 8 m 剛好落在 J，量不出通過 J 的方向，不是迴轉，不放行
    spur_in = [east(J, -200), J, east(J, 4), J]
    router = TurnBlockRouter(blocked=None)
    assert not rideable(router, Deadline(10), spur_in, LEFT)
    # outgoing 一開始也來回 4 m
    spur_out = [J, east(J, 4), J, north(J, 100)]
    assert not rideable(router, Deadline(10), INCOMING, spur_out)
    assert router.route_calls == []  # 量不出來就不放行，不靠 route


def test_head_points_stop_once_long_enough() -> None:
    near, far = seg([J, east(J, 100)]), seg([east(J, 100), east(J, 300)])
    assert head_points([near, far]) == near.points


# ---- P1.5 Task 10 fix 2：折回接點的判斷看走的路，不只看直線距離 ----


def test_short_bent_arm_is_not_a_fold_back() -> None:
    # 0.6 m 向北再 0.6 m 向東：走了 1.2 m，直線只有 0.85 m。多走的路不到 JOIN_TOLERANCE_M，不是折回，照一般轉角量
    start = J
    at = east(north(start, 0.6), 0.6)
    assert rideable(TurnBlockRouter(), Deadline(10), [start, north(start, 0.6), at], [at, east(at, 100)])
    assert rideable(TurnBlockRouter(), Deadline(10), [east(at, -100), at], [at, east(at, 0.6), north(east(at, 0.6), 0.6)])


def test_both_arms_folding_back_is_not_a_u_turn() -> None:
    # 兩邊都是 4 m 的來回：p、q 都落回 J，看起來像迴轉（p≈q），其實兩條臂都量不出方向
    router = TurnBlockRouter(blocked=J)
    assert not rideable(router, Deadline(10), [east(J, -200), J, east(J, 4), J], [J, north(J, -4), J, north(J, 100)])
    assert router.route_calls == []


def test_outgoing_that_folds_onto_its_own_start_is_not_rideable() -> None:
    # outgoing 從離 J 0.9 m 的地方出發，來回 4 m 後落回自己的起點：折回
    start = north(J, 0.9)
    router = TurnBlockRouter(blocked=J)
    assert not rideable(router, Deadline(10), INCOMING, [start, east(start, -4), start, east(start, 100)])
    assert router.route_calls == []
