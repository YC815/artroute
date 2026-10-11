"""接點轉彎檢查（不需要 Valhalla）。"""

import pytest

from engine.matching.deadline import Deadline, MatchTimeout
from engine.matching.turns import (
    TURN_PROBE_M,
    head_points,
    rideable,
    rideable_strict,
    strict_needs_no_route_to_reject,
    tail_points,
    turn_deg,
)
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


# ---- P2.5 A4：嚴格版（剪小刺與小圈的新接點）：量不出來的不放行，其餘與 rideable 相同 ----


def test_strict_rejects_a_u_turn_that_rideable_lets_through() -> None:
    router = TurnBlockRouter(blocked=None)
    assert rideable(router, Deadline(10), INCOMING, BACK)
    assert not rideable_strict(router, Deadline(10), INCOMING, BACK)
    assert strict_needs_no_route_to_reject(INCOMING, BACK)
    assert router.route_calls == []  # 原地迴轉 route 量不出來：不靠 route 決定


def test_strict_rejects_an_arm_too_short_to_measure() -> None:
    router = TurnBlockRouter(blocked=None)
    short_in = [east(J, -0.5), J]
    assert rideable(router, Deadline(10), short_in, STRAIGHT)
    assert not rideable_strict(router, Deadline(10), short_in, STRAIGHT)
    assert not rideable_strict(router, Deadline(10), INCOMING, [J, east(J, 0.5)])
    assert router.route_calls == []


def test_strict_still_lets_a_gentle_turn_through_without_route() -> None:
    router = TurnBlockRouter(blocked=J)
    assert rideable_strict(router, Deadline(10), INCOMING, STRAIGHT)
    assert not strict_needs_no_route_to_reject(INCOMING, STRAIGHT)
    assert router.route_calls == []


def test_strict_checks_a_sharp_turn_with_route_like_rideable() -> None:
    assert rideable_strict(TurnBlockRouter(blocked=None), Deadline(10), INCOMING, LEFT)
    assert not rideable_strict(TurnBlockRouter(blocked=J), Deadline(10), INCOMING, LEFT)
    assert not strict_needs_no_route_to_reject(INCOMING, LEFT)  # 要 route 才知道


def test_strict_rejects_a_fold_back_like_rideable() -> None:
    spur_in = [east(J, -200), J, east(J, 4), J]
    assert not rideable_strict(TurnBlockRouter(blocked=None), Deadline(10), spur_in, LEFT)
    assert strict_needs_no_route_to_reject(spur_in, LEFT)


def test_strict_rejects_a_short_dead_end_u_turn_the_probes_wrap_around() -> None:
    # 往北 6 m 的死巷尖端原地迴轉：兩個探針繞過尖端，落在 J 的西邊、東邊各 2 m（不是同一處），轉角約 143°，
    # rideable 交給 route（直線 4 m）就放行了；嚴格版看得出接點前後的頂點是同一個（沿原路折回），不放行
    tip = north(J, 6)
    incoming, outgoing = [east(J, -200), J, tip], [tip, J, east(J, 200)]
    router = TurnBlockRouter(blocked=None)
    assert rideable(router, Deadline(10), incoming, outgoing)
    router.route_calls.clear()
    assert not rideable_strict(router, Deadline(10), incoming, outgoing)
    assert strict_needs_no_route_to_reject(incoming, outgoing)
    assert router.route_calls == []


def test_strict_rejects_a_u_turn_whose_two_sides_have_different_vertices() -> None:
    # 審查 B1：J 西邊 5 m 是路口 N、3 m 是段落邊界 M（只有往回那一側有這個頂點）。出了 J 沿原路往西折回，
    # 再在 N 往北：兩側第一個超過 1 m 的頂點（N、M）相距 2 m，探針繞過 J、落在 N 西 3 m 與 N 北 3 m，
    # route 量到 N 的合法轉彎就放行了。嚴格版在 1 m、2 m 的尺度看得出兩條臂重疊：原地迴轉
    n, m = east(J, -5), east(J, -3)
    incoming, outgoing = [east(J, -200), n, J], [J, m, n, north(n, 400)]
    router = TurnBlockRouter(blocked=None)
    assert rideable(router, Deadline(10), incoming, outgoing)  # 非嚴格版照舊
    assert not rideable_strict(router, Deadline(10), incoming, outgoing)
    assert strict_needs_no_route_to_reject(incoming, outgoing)


def test_strict_still_measures_sharp_turns_that_are_not_u_turns() -> None:
    # 對照：120° 的急轉（不是沿原路折回）照樣交給 route 量，不當成原地迴轉
    sharp = [J, (J[0] - 100 * 0.5 / 100_900, J[1] + 100 * 0.866 / 110_574)]
    assert not strict_needs_no_route_to_reject(INCOMING, sharp)
    assert rideable_strict(TurnBlockRouter(blocked=None), Deadline(10), INCOMING, sharp)
    assert not rideable_strict(TurnBlockRouter(blocked=J), Deadline(10), INCOMING, sharp)


def test_strict_catches_a_retrace_seen_only_from_the_incoming_side() -> None:
    # 複審 nit 2：迴轉後只走 1.5 m 路線就結束（outgoing 不到 2 m，只能從 incoming 那側量）：
    # incoming 往回 2 m 的點落在 outgoing 上，是原路折回；rideable 交給 route（直線 6.5 m）就放行了
    incoming, outgoing = [east(J, -200), J], [J, east(J, -1.5)]
    router = TurnBlockRouter(blocked=None)
    assert rideable(router, Deadline(10), incoming, outgoing)
    assert not rideable_strict(router, Deadline(10), incoming, outgoing)


def test_strict_catches_a_retrace_offset_just_under_the_join_tolerance() -> None:
    # 複審 nit 3：折回的路與來的路平行、相距 0.9 m（例如同一條路的對向車道畫成兩條線）：仍是原地迴轉
    incoming = [east(J, -200), J]
    outgoing = [J, north(J, 0.9), north(east(J, -100), 0.9)]
    router = TurnBlockRouter(blocked=None)
    assert rideable(router, Deadline(10), incoming, outgoing)
    assert not rideable_strict(router, Deadline(10), incoming, outgoing)
