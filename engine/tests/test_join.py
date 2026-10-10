"""接點裁切重試（控制者對最終修正的補充）：接點仍只容許 1 m，但放棄之前先沿既有路段裁掉
前段尾巴／後段開頭 20／50／100 m 再接。只裁掉 Valhalla 給的幾何，所以不會多出任何直線或新路段。"""

import pytest

from engine.matching.deadline import Deadline, MatchTimeout
from engine.matching.join import TRIM_STEPS_M, join
from engine.matching.types import JOIN_TOLERANCE_M, Segment
from engine.routing.geo import haversine_m, polyline_length_m
from engine.routing.router import RouteResult, RoutingError

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

    def route(self, start, end):
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
    def route(self, start, end):
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
