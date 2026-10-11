"""matching.safety.unsafe_segments：/verify 與 bench 共用的逐段檢查（細節的行為由 test_bench、test_wrong_way、
test_replay 透過 safety_problem 覆蓋，這裡只測 /verify 用到的「有問題的段落索引」）。"""

import pytest

from engine.matching.deadline import Deadline, MatchTimeout
from engine.matching.safety import unsafe_segments
from engine.matching.types import Segment
from engine.routing.router import EdgeInfo
from edit_router import FakeRouter
from turn_router import east

A = (121.5, 25.05)


def seg(x0: float, x1: float) -> Segment:
    return Segment("stroke", 0, [east(A, x0), east(A, (x0 + x1) / 2), east(A, x1)], x1 - x0)


class CleanRouter(FakeRouter):
    def edges(self, points):
        return [EdgeInfo(1, 0.0, begin_shape_index=0, end_shape_index=len(points) - 1)]


def test_clean_route_has_no_unsafe_segments() -> None:
    assert unsafe_segments(CleanRouter(), [seg(0, 100), seg(100, 200)], frozenset()) is None


def test_gap_lists_both_sides_only() -> None:
    assert unsafe_segments(CleanRouter(), [seg(0, 100), seg(100, 200), seg(300, 400)], frozenset()) == [1, 2]


def test_a_route_with_nothing_to_check_lists_everything() -> None:
    """零長度的段落沒有經過任何路段：沒有任何可檢查的路段就是判斷不了。"""
    point = Segment("stroke", 0, [A, A], 0.0)
    assert unsafe_segments(CleanRouter(), [point, point], frozenset()) == [0, 1]


def test_expired_deadline_stops_the_check() -> None:
    with pytest.raises(MatchTimeout):
        unsafe_segments(CleanRouter(), [seg(0, 100)], frozenset(), Deadline(-1.0))
