import math
import random
from pathlib import Path

import pytest

from engine.matching.compose import Part, _greedy, _options, _plan, _two_opt, compose, rotate
from engine.matching.deadline import Deadline, MatchTimeout
from engine.matching.types import JOIN_TOLERANCE_M, Segment
from engine.routing.geo import haversine_m, polyline_length_m
from engine.routing.router import RouteResult, Router, RoutingError

LAT = 25.05


def seg(points, kind="stroke", index=0) -> Segment:
    return Segment(kind=kind, stroke_index=index, points=points, length_m=polyline_length_m(points))


class StraightRouter:
    def __init__(self, fail_to=None) -> None:
        self.fail_to = fail_to
        self.calls: list[tuple] = []

    def route(self, start, end):
        self.calls.append((start, end))
        if self.fail_to is not None and end == self.fail_to:
            raise RoutingError("no path")
        return RouteResult(points=[start, end], length_km=haversine_m(start, end) / 1000)

    def trace(self, points, *, search_radius_m):
        raise AssertionError("compose 不呼叫 trace")


def assert_continuous(segments) -> None:
    for a, b in zip(segments, segments[1:]):
        assert haversine_m(a.points[-1], b.points[0]) <= JOIN_TOLERANCE_M


def test_single_part_has_no_connector() -> None:
    part = Part([seg([(121.50, LAT), (121.51, LAT)])], closed=False)
    result = compose(StraightRouter(), [part], Deadline(10))
    assert [s.kind for s in result.segments] == ["stroke"]
    assert result.dropped == []


def test_far_parts_are_joined_by_connector_in_nearest_order() -> None:
    a = Part([seg([(121.50, LAT), (121.51, LAT)], index=0)], closed=False)
    far = Part([seg([(121.60, LAT), (121.61, LAT)], index=1)], closed=False)
    near = Part([seg([(121.52, LAT), (121.53, LAT)], index=2)], closed=False)
    result = compose(StraightRouter(), [a, far, near], Deadline(10))
    assert_continuous(result.segments)
    strokes = [s.stroke_index for s in result.segments if s.kind == "stroke"]
    assert strokes == [0, 2, 1]
    assert [s.kind for s in result.segments].count("connector") == 2


def test_closed_part_entered_at_point_nearest_previous_end() -> None:
    square = [(121.52, LAT), (121.53, LAT), (121.53, LAT + 0.01), (121.52, LAT + 0.01), (121.52, LAT)]
    first = Part([seg([(121.50, LAT + 0.01), (121.515, LAT + 0.01)], index=0)], closed=False)
    loop = Part([seg(square, index=1)], closed=True)
    result = compose(StraightRouter(), [first, loop], Deadline(10))
    loop_points = [p for s in result.segments if s.stroke_index == 1 and s.kind == "stroke" for p in s.points]
    assert haversine_m(loop_points[0], (121.52, LAT + 0.01)) < 60
    assert haversine_m(loop_points[0], loop_points[-1]) <= JOIN_TOLERANCE_M  # 繞完一圈回到進入點


def test_unconnectable_part_is_dropped() -> None:
    a = Part([seg([(121.50, LAT), (121.51, LAT)], index=0)], closed=False)
    b = Part([seg([(121.52, LAT), (121.53, LAT)], index=1)], closed=False)
    result = compose(StraightRouter(fail_to=(121.52, LAT)), [a, b], Deadline(10))
    assert result.dropped == [1]
    assert [s.stroke_index for s in result.segments] == [0]


def test_rotate_splits_segment_and_keeps_points() -> None:
    ring = [seg([(0.0, 0.0), (1.0, 0.0), (1.0, 1.0)]), seg([(1.0, 1.0), (0.0, 1.0), (0.0, 0.0)])]
    rotated = rotate(ring, 1)
    flat = [p for s in rotated for p in s.points]
    assert flat[0] == (1.0, 0.0) and flat[-1] == (1.0, 0.0)
    assert_continuous(rotated)
    assert sum(s.length_m for s in rotated) == pytest.approx(sum(s.length_m for s in ring))


def test_rotate_at_segment_junction_does_not_create_empty_segment() -> None:
    ring = [seg([(0.0, 0.0), (1.0, 0.0), (1.0, 1.0)]), seg([(1.0, 1.0), (0.0, 1.0), (0.0, 0.0)])]
    rotated = rotate(ring, 2)  # 全域第 2 個點正好是兩段的共用接點
    assert [s.points for s in rotated] == [ring[1].points, ring[0].points]
    assert_continuous(rotated)


def test_rotate_cuts_at_requested_visit_when_coordinate_repeats() -> None:
    # 八字形：(1,0) 在第 1 與第 3 個點各經過一次
    pts = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (1.0, 0.0), (1.0, -1.0), (0.0, 0.0)]
    rotated = rotate([seg(pts)], 3)
    flat = [p for s in rotated for p in s.points]
    assert flat == [(1.0, 0.0), (1.0, -1.0), (0.0, 0.0), (0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (1.0, 0.0)]
    assert_continuous(rotated)


def test_rotate_repeated_coordinate_in_later_segment() -> None:
    first = seg([(0.0, 0.0), (1.0, 0.0)])
    second = seg([(1.0, 0.0), (1.0, 1.0), (1.0, 0.0), (1.0, -1.0), (0.0, 0.0)])
    rotated = rotate([first, second], 3)  # 全域：0:(0,0) 1:(1,0) 2:(1,1) 3:(1,0) 4:(1,-1) 5:(0,0)
    flat = [p for s in rotated for p in s.points]
    assert flat == [(1.0, 0.0), (1.0, -1.0), (0.0, 0.0), (0.0, 0.0), (1.0, 0.0), (1.0, 0.0), (1.0, 1.0), (1.0, 0.0)]
    assert_continuous(rotated)


def _random_parts(seed: int) -> list[Part]:
    rng = random.Random(seed)
    parts = []
    for i in range(rng.randint(3, 9)):
        cx, cy = 121.5 + rng.random() * 0.05, 25.0 + rng.random() * 0.05
        if rng.random() < 0.5:
            ring = [(cx + 0.002 * math.cos(2 * math.pi * k / 60), cy + 0.002 * math.sin(2 * math.pi * k / 60)) for k in range(60)]
            parts.append(Part([seg(ring + ring[:1], index=i)], closed=True))
        else:
            parts.append(Part([seg([(cx, cy), (cx + 0.003, cy + 0.001 * rng.random())], index=i)], closed=False))
    return parts


def _continue_scan_two_opt(options, order):
    """2-opt 的參考實作：找到改善就換掉 order，掃描接著從原本的 (i, j) 往下走，掃完一輪沒有改善才停。"""
    best_cost, _ = _plan(options, order)
    improved = True
    while improved:
        improved = False
        for i in range(1, len(order) - 1):
            for j in range(i + 1, len(order)):
                candidate = order[:i] + order[i : j + 1][::-1] + order[j + 1 :]
                cost, _ = _plan(options, candidate)
                if cost < best_cost - 1e-6:
                    order, best_cost, improved = candidate, cost, True
    return order


@pytest.mark.parametrize("seed", range(300))
def test_two_opt_matches_continue_scan_reference(seed: int) -> None:
    options = [_options(part) for part in _random_parts(seed)]
    start = _greedy(options)
    assert _two_opt(options, list(start)) == _continue_scan_two_opt(options, list(start))


def test_empty_parts() -> None:
    result = compose(StraightRouter(), [], Deadline(10))
    assert result.segments == [] and result.dropped == []


class CollapsingRouter(StraightRouter):
    """模擬真 Valhalla：起訖吸附到同一處時 route 成功，但只回傳一個點、長度 0。"""

    def route(self, start, end):
        self.calls.append((start, end))
        return RouteResult(points=[start, start], length_km=0.0)


class OffsetRouter(StraightRouter):
    """route 的起點或終點沒落在要求的位置（例如被吸附到別處）。"""

    def __init__(self, start_off_m: float = 0.0, end_off_m: float = 0.0) -> None:
        super().__init__()
        self.start_off_m, self.end_off_m = start_off_m, end_off_m

    def route(self, start, end):
        self.calls.append((start, end))
        moved_start = (start[0], start[1] + self.start_off_m / 110_574)
        moved_end = (end[0], end[1] + self.end_off_m / 110_574)
        points = [moved_start, moved_end]
        return RouteResult(points=points, length_km=polyline_length_m(points) / 1000)


@pytest.mark.parametrize("start_off_m,end_off_m", [(3.0, 0.0), (0.0, 3.0)])
def test_connector_not_reaching_requested_ends_is_failed_connection(start_off_m, end_off_m) -> None:
    a = Part([seg([(121.50, LAT), (121.51, LAT)], index=0)], closed=False)
    b = Part([seg([(121.52, LAT), (121.53, LAT)], index=1)], closed=False)
    result = compose(OffsetRouter(start_off_m, end_off_m), [a, b], Deadline(10))
    assert result.dropped == [1]
    assert [s.kind for s in result.segments] == ["stroke"]


def test_connector_within_tolerance_of_requested_ends_is_accepted() -> None:
    a = Part([seg([(121.50, LAT), (121.51, LAT)], index=0)], closed=False)
    b = Part([seg([(121.52, LAT), (121.53, LAT)], index=1)], closed=False)
    result = compose(OffsetRouter(0.5, 0.5), [a, b], Deadline(10))
    assert result.dropped == []
    assert [s.kind for s in result.segments] == ["stroke", "connector", "stroke"]


def test_adjacent_parts_need_no_route_call() -> None:
    a = Part([seg([(121.50, LAT), (121.51, LAT)], index=0)], closed=False)
    b = Part([seg([(121.51, LAT), (121.52, LAT)], index=1)], closed=False)  # 起點與 a 終點重合
    router = StraightRouter()
    result = compose(router, [a, b], Deadline(10))
    assert router.calls == []
    assert [s.kind for s in result.segments] == ["stroke", "stroke"]
    assert result.dropped == []


def test_collapsed_route_is_failed_connection_not_zero_length_connector() -> None:
    a = Part([seg([(121.50, LAT), (121.51, LAT)], index=0)], closed=False)
    b = Part([seg([(121.52, LAT), (121.53, LAT)], index=1)], closed=False)  # 與 a 相距約 1 km
    result = compose(CollapsingRouter(), [a, b], Deadline(10))
    assert result.dropped == [1]
    assert [s.kind for s in result.segments] == ["stroke"]
    assert all(s.length_m > 0 for s in result.segments)


def test_dropped_part_does_not_break_following_connections() -> None:
    a = Part([seg([(121.50, LAT), (121.51, LAT)], index=0)], closed=False)
    bad = Part([seg([(121.52, LAT), (121.53, LAT)], index=1)], closed=False)
    c = Part([seg([(121.60, LAT), (121.61, LAT)], index=2)], closed=False)
    result = compose(StraightRouter(fail_to=(121.52, LAT)), [a, bad, c], Deadline(10))
    assert result.dropped == [1]
    assert_continuous(result.segments)
    assert [s.stroke_index for s in result.segments if s.kind == "stroke"] == [0, 2]


def test_deadline_checked_before_every_route_call() -> None:
    a = Part([seg([(121.50, LAT), (121.51, LAT)], index=0)], closed=False)
    b = Part([seg([(121.52, LAT), (121.53, LAT)], index=1)], closed=False)
    router = StraightRouter()
    expired = Deadline(1, clock=iter([0.0, 5.0, 5.0, 5.0]).__next__)  # 建立時 0 秒，第一次 check 已過 5 秒
    with pytest.raises(MatchTimeout):
        compose(router, [a, b], expired)
    assert router.calls == []


@pytest.mark.valhalla
def test_grid_two_strokes_connected_along_roads(grid_config: Path) -> None:
    from osm_fixture import grid_point

    router = Router(grid_config)
    left = Part([seg([grid_point(0, 1), grid_point(1, 1)], index=0)], closed=False)
    right = Part([seg([grid_point(5, 1), grid_point(6, 1)], index=1)], closed=False)
    result = compose(router, [left, right], Deadline(10))
    assert_continuous(result.segments)
    connector = [s for s in result.segments if s.kind == "connector"]
    assert len(connector) == 1
    assert connector[0].length_m == pytest.approx(804, abs=60)


M_PER_DEG_LAT = 110_574
M_PER_DEG_LON = 111_320 * math.cos(math.radians(LAT))


def east(point, meters):
    return (point[0] + meters / M_PER_DEG_LON, point[1])


def west(point, meters):
    return east(point, -meters)


def south(point, meters):
    return (point[0], point[1] - meters / M_PER_DEG_LAT)


@pytest.fixture(scope="module")
def grid_router(grid_config: Path) -> Router:
    return Router(grid_config)


@pytest.mark.valhalla
@pytest.mark.parametrize("d", [0.5, 2.5, 4.5])
def test_grid_endpoints_near_intersection_still_join_at_junctions(grid_router: Router, d: float) -> None:
    """trace 吸附出來的端點常落在離路口 1–5 m 的路上；route 預設會把端點拉到路口，接點就裂開。"""
    from osm_fixture import grid_point

    left = Part([seg([grid_point(0, 1), west(grid_point(1, 1), d)], index=0)], closed=False)
    right = Part([seg([east(grid_point(5, 1), d), grid_point(6, 1)], index=1)], closed=False)
    result = compose(grid_router, [left, right], Deadline(10))
    assert result.dropped == []
    assert [s.kind for s in result.segments] == ["stroke", "connector", "stroke"]
    assert_continuous(result.segments)


@pytest.mark.valhalla
@pytest.mark.parametrize("a_off,b_off", [(2.5, 2.0), (4.0, 0.5)])
def test_grid_corner_adjacent_parts_are_not_dropped(grid_router: Router, a_off: float, b_off: float) -> None:
    """L 形轉角：A 停在橫街上離路口 a_off m，B 從直街上離路口 b_off m 開始；兩端都會被拉到同一個路口。"""
    from osm_fixture import grid_point

    corner = grid_point(1, 1)
    a = Part([seg([grid_point(1, 0), south(corner, a_off)], index=0)], closed=False)
    b = Part([seg([east(corner, b_off), grid_point(3, 1)], index=1)], closed=False)
    result = compose(grid_router, [a, b], Deadline(10))
    assert result.dropped == []
    assert [s.stroke_index for s in result.segments if s.kind == "stroke"] == [0, 1]
    assert_continuous(result.segments)
