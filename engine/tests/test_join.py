"""接點裁切重試（控制者對最終修正的補充）：接點仍只容許 1 m，但放棄之前先沿既有路段裁掉
前段尾巴／後段開頭 20／50／100 m 再接。只裁掉 Valhalla 給的幾何，所以不會多出任何直線或新路段。"""

import pytest

from engine.matching.deadline import Deadline, MatchTimeout
from engine.matching.join import TRIM_STEPS_M, connect, join
from engine.matching.types import JOIN_TOLERANCE_M, Segment
from engine.routing.geo import haversine_m, polyline_length_m
from engine.routing.router import RouteResult, RoutingError
from turn_router import TurnBlockRouter, east, every_junction_rideable, north

LAT = 25.05
M_PER_DEG_LON = 100_900  # 緯度 25° 附近


def row(lon_from: float, lon_to: float, n: int, lat: float = LAT) -> list[tuple[float, float]]:
    return [(lon_from + (lon_to - lon_from) * i / n, lat) for i in range(n + 1)]


def seg(points, kind="stroke") -> Segment:
    return Segment(kind, 0, points, polyline_length_m(points))


BEFORE = seg(row(121.500, 121.505, 50))  # 約 505 m，每 10 m 一點
AFTER = seg(row(121.507, 121.512, 50))


class BlockingRouter:
    """route 的起點離 blocked_start 在 radius_m 內（或終點離 blocked_end 在 radius_m 內）時，
    該端會被吸到 5 m 外：模擬端點落在 route 用不了的 edge 上。其他情況直接走直線。"""

    def __init__(self, blocked_start=None, blocked_end=None, radius_m: float = 5.0) -> None:
        self.blocked_start, self.blocked_end, self.radius_m = blocked_start, blocked_end, radius_m
        self.calls: list[tuple] = []

    def _off(self, point, blocked):
        if blocked is not None and haversine_m(point, blocked) <= self.radius_m:
            return (point[0], point[1] + 5 / 110_574)
        return point

    def route(self, start, end, *, shortest=False):
        self.calls.append((start, end))
        a, b = self._off(start, self.blocked_start), self._off(end, self.blocked_end)
        return RouteResult(points=[a, b], length_km=haversine_m(a, b) / 1000)

    def trace(self, points, *, search_radius_m):
        raise AssertionError("join 不呼叫 trace")


def is_prefix(part: list, whole: list) -> bool:
    return whole[: len(part)] == part


def is_suffix(part: list, whole: list) -> bool:
    return whole[len(whole) - len(part) :] == part


def assert_joined(joined) -> None:
    assert haversine_m(joined.before.points[-1], joined.link.points[0]) <= JOIN_TOLERANCE_M
    assert haversine_m(joined.link.points[-1], joined.after.points[0]) <= JOIN_TOLERANCE_M


def test_direct_join_keeps_both_pieces_whole() -> None:
    router = BlockingRouter()
    joined = join(router, Deadline(10), BEFORE, AFTER, "detour", 0)
    assert joined is not None
    assert joined.before == BEFORE and joined.after == AFTER
    assert joined.link.kind == "detour" and joined.link.stroke_index == 0
    assert_joined(joined)
    assert len(router.calls) == 1


def test_head_of_next_piece_is_trimmed_when_its_start_is_unreachable() -> None:
    router = BlockingRouter(blocked_end=AFTER.points[0])
    joined = join(router, Deadline(10), BEFORE, AFTER, "connector", None)
    assert joined is not None
    assert_joined(joined)
    assert joined.before == BEFORE
    assert is_suffix(joined.after.points, AFTER.points)  # 只裁掉開頭，剩下的是原本路段的一部分
    trimmed_m = AFTER.length_m - joined.after.length_m
    assert TRIM_STEPS_M[0] <= trimmed_m < TRIM_STEPS_M[0] + 11
    assert joined.after.length_m == pytest.approx(polyline_length_m(joined.after.points))


def test_tail_of_previous_piece_is_trimmed_when_its_end_cannot_be_left() -> None:
    router = BlockingRouter(blocked_start=BEFORE.points[-1])
    joined = join(router, Deadline(10), BEFORE, AFTER, "connector", None)
    assert joined is not None
    assert_joined(joined)
    assert joined.after == AFTER
    assert is_prefix(joined.before.points, BEFORE.points)
    assert TRIM_STEPS_M[0] <= BEFORE.length_m - joined.before.length_m < TRIM_STEPS_M[0] + 11


def test_both_ends_are_trimmed_when_both_are_blocked() -> None:
    router = BlockingRouter(blocked_start=BEFORE.points[-1], blocked_end=AFTER.points[0])
    joined = join(router, Deadline(10), BEFORE, AFTER, "connector", None)
    assert joined is not None
    assert_joined(joined)
    assert is_prefix(joined.before.points, BEFORE.points) and joined.before != BEFORE
    assert is_suffix(joined.after.points, AFTER.points) and joined.after != AFTER


def test_gives_up_after_trimming_the_largest_step() -> None:
    # 被擋的範圍超過最大裁切長度：放棄，交給呼叫端斷開或丟部件，絕不硬接
    router = BlockingRouter(blocked_end=AFTER.points[0], radius_m=TRIM_STEPS_M[-1] + 30)
    assert join(router, Deadline(10), BEFORE, AFTER, "connector", None) is None


def test_short_pieces_are_not_trimmed_away() -> None:
    # 兩點的短段沒有可以裁的位置（裁掉就沒了）：只試一次直接接
    short_before, short_after = seg([(121.500, LAT), (121.5001, LAT)]), seg([(121.507, LAT), (121.5071, LAT)])
    router = BlockingRouter(blocked_end=short_after.points[0])
    assert join(router, Deadline(10), short_before, short_after, "connector", None) is None
    assert len(router.calls) == 1


class FailingRouter(BlockingRouter):
    def route(self, start, end, *, shortest=False):
        self.calls.append((start, end))
        raise RoutingError("no path")


def test_only_the_side_that_missed_is_trimmed() -> None:
    # 起點沒接上：只裁前段尾巴，不去試後段開頭（每次 route 都要時間，接不上的部件會把所有組合試完）
    router = BlockingRouter(blocked_start=BEFORE.points[-1])
    joined = join(router, Deadline(10), BEFORE, AFTER, "connector", None)
    assert joined is not None and joined.after == AFTER
    assert len(router.calls) == 2
    assert all(end == AFTER.points[0] for _, end in router.calls)


def test_no_path_trims_both_sides_one_step_at_a_time() -> None:
    # 找不到路時看不出是哪一端的問題：兩端一起往內裁，裁到底就放棄
    router = FailingRouter()
    assert join(router, Deadline(10), BEFORE, AFTER, "connector", None) is None
    assert len(router.calls) == 1 + len(TRIM_STEPS_M)
    assert len(set(router.calls)) == len(router.calls)


def test_deadline_is_checked_before_each_retry() -> None:
    router = FailingRouter()
    ticks = iter([0.0] + [0.0] * 3 + [99.0] * 100)  # 建立、前三次檢查還沒到期，之後到期
    with pytest.raises(MatchTimeout):
        join(router, Deadline(1, clock=lambda: next(ticks)), BEFORE, AFTER, "connector", None)
    assert len(router.calls) == 3


# ---- P1.5 Task 10：接點轉彎 Valhalla 不會直接騎，視同沒接上 ----

J_END = (121.507, LAT)
AFTER_NORTH = seg([(121.507, LAT + k * 10 / 110_574) for k in range(51)])  # 從 J_END 往北 500 m
J_START = (121.505, LAT)
BEFORE_NORTH = seg([(121.505, LAT - (50 - k) * 10 / 110_574) for k in range(51)])  # 從南邊往北騎到 J_START


def test_link_whose_far_end_turn_cannot_be_ridden_trims_the_next_piece() -> None:
    # link 由西往東到 J_END，後段在 J_END 往北：那個左轉不能騎 → 裁後段開頭，從別處接
    router = TurnBlockRouter(blocked=J_END)
    joined = join(router, Deadline(10), BEFORE, AFTER_NORTH, "connector", None)
    assert joined is not None
    assert_joined(joined)
    assert joined.before == BEFORE
    assert is_suffix(joined.after.points, AFTER_NORTH.points) and joined.after != AFTER_NORTH


def test_link_whose_near_end_turn_cannot_be_ridden_trims_the_previous_piece() -> None:
    router = TurnBlockRouter(blocked=J_START)
    joined = join(router, Deadline(10), BEFORE_NORTH, AFTER, "connector", None)
    assert joined is not None
    assert_joined(joined)
    assert joined.after == AFTER
    assert is_prefix(joined.before.points, BEFORE_NORTH.points) and joined.before != BEFORE_NORTH


# ---- P1.5 Task 10 fix 1：link 旁邊不到 1 m 的片段，不能把轉彎藏起來 ----


def test_tiny_piece_next_to_the_link_does_not_hide_the_turn() -> None:
    # 接在 J 的 right[0] 只有 0.4 m，東→北的轉彎（J 不能轉）在它後面的 right[1]：探針要跨過它才量得到
    left = [seg([east(J_END, -250), east(J_END, -50)])]  # 終點離 J 西邊 50 m
    right = [seg([J_END, north(J_END, 0.4)]), seg([north(J_END, 0.4), north(J_END, 200)])]
    assert connect(TurnBlockRouter(blocked=J_END), Deadline(10), left, right, "connector", None) is None
    free = connect(TurnBlockRouter(), Deadline(10), left, right, "connector", None)  # 對照：J 能轉彎就接得上
    assert free is not None and every_junction_rideable(TurnBlockRouter(), free)


def test_trim_that_leaves_a_stub_does_not_hide_the_turn() -> None:
    # 裁掉 before 的尾巴會留下 0.4 m 的殘段：殘段與 link 之間的轉彎（J 不能轉）仍要量到
    k = east(J_END, 21)
    left = [seg([east(J_END, -200), J_END]), seg([J_END, east(J_END, 0.4), k])]
    right = [seg([north(k, 25), north(k, 200)])]
    router = TurnBlockRouter(blocked=k, also_blocked=(J_END,))  # k 讓第一次嘗試失敗而裁切，J 擋住裁切後的轉彎
    assert connect(router, Deadline(10), left, right, "connector", None) is None


# ---- P1.5 Task 10 fix 2：新接上的幾何也落在鄰近既有接點的探針裡，那些接點要一併重量 ----

NODE = (121.507, LAT)
WEST = seg([east(NODE, -100), NODE])  # 由西往東到 NODE
SOUTH_4M = seg([NODE, north(NODE, -4)])  # 在 NODE 轉向南走 4 m（單獨量：探針只有這 4 m，轉彎能騎）
BACK_NORTH = seg([north(NODE, -4), north(NODE, 50)])  # 在那裡折回，沿同一條路往北經過 NODE


def test_neighbour_junction_whose_probe_now_folds_back_is_rechecked_by_adjacent() -> None:
    # 接縫（南端）的轉彎沒問題，但 WEST|SOUTH_4M 的往後 8 m 探針現在走 4 m 又折回 NODE：量不出方向，不能直接串
    router = TurnBlockRouter()
    assert every_junction_rideable(router, [WEST, SOUTH_4M])  # 前提：原本這條鏈的接點都能騎
    assert connect(router, Deadline(10), [WEST, SOUTH_4M], [BACK_NORTH], "connector", None) is None


def test_neighbour_junction_after_the_link_is_rechecked_too() -> None:
    # 反過來：右邊的鏈由 NODE 北端出發往南 4 m 折回、再往西，接縫前面的探針折回 NODE
    left = [seg([north(NODE, 50), north(NODE, -4)])]
    right = [seg([north(NODE, -4), NODE]), seg([NODE, east(NODE, -100)])]
    router = TurnBlockRouter()
    assert every_junction_rideable(router, right)
    assert connect(router, Deadline(10), left, right, "connector", None) is None


def test_recheck_walks_past_several_tiny_pieces() -> None:
    # 壞的接點在兩片之外：WEST|第一片（NODE）離接縫 4 m，中間還隔著一個接點
    pieces = [seg([NODE, north(NODE, -1.5)]), seg([north(NODE, -1.5), north(NODE, -4)])]
    router = TurnBlockRouter()
    assert every_junction_rideable(router, [WEST, *pieces])
    assert connect(router, Deadline(10), [WEST, *pieces], [BACK_NORTH], "connector", None) is None


def test_neighbour_beyond_the_probe_length_is_not_rechecked() -> None:
    # 對照：南行片段 12 m，鄰近接點離接縫超過 8 m，探針碰不到新的幾何，照舊直接串，不多呼叫 route
    south = seg([NODE, north(NODE, -12)])
    back = seg([north(NODE, -12), north(NODE, 50)])
    router = TurnBlockRouter()
    connected = connect(router, Deadline(10), [WEST, south], [back], "connector", None)
    assert connected == [WEST, south, back]
    assert every_junction_rideable(TurnBlockRouter(), connected)


def test_connect_accepts_a_short_bent_arm_that_is_not_a_fold_back() -> None:
    start = NODE
    corner = east(north(start, 0.6), 0.6)
    left = [seg([start, north(start, 0.6), corner])]
    right = [seg([corner, east(corner, 100)])]
    connected = connect(TurnBlockRouter(), Deadline(10), left, right, "connector", None)
    assert connected == [*left, *right]
