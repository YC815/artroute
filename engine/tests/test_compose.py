import math
import random
from pathlib import Path

import pytest

from engine.matching.compose import ANCHOR_TIE_M, MAX_CLOSED_ENTRIES, Part, _greedy, _options, _plan, _two_opt, compose, rotate
from engine.matching.deadline import Deadline, MatchCancelled, MatchTimeout
from engine.matching.types import JOIN_TOLERANCE_M, Segment
from engine.routing.geo import haversine_m, polyline_length_m
from engine.routing.router import RouteResult, Router, RoutingError

LAT = 25.05


def seg(points, kind="stroke", index=0) -> Segment:
    return Segment(kind=kind, stroke_index=index, points=points, length_m=polyline_length_m(points))


class StraightRouter:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def route(self, start, end, *, shortest=False):
        self.calls.append((start, end))
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
    a = Part([seg([(121.48, LAT), (121.51, LAT)], index=0)], closed=False)
    b = Part([seg([(121.52, LAT), (121.53, LAT)], index=1)], closed=False)
    result = compose(IslandStraightRouter(island=frozenset(b.segments[0].points)), [a, b], Deadline(10))
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
    start = _greedy(options, Deadline(60))
    assert _two_opt(options, list(start), Deadline(60)) == _continue_scan_two_opt(options, list(start))


def test_empty_parts() -> None:
    result = compose(StraightRouter(), [], Deadline(10))
    assert result.segments == [] and result.dropped == []


class CollapsingRouter(StraightRouter):
    """模擬真 Valhalla：起訖吸附到同一處時 route 成功，但只回傳一個點、長度 0。"""

    def route(self, start, end, *, shortest=False):
        self.calls.append((start, end))
        return RouteResult(points=[start, start], length_km=0.0)


class OffsetRouter(StraightRouter):
    """route 的起點或終點沒落在要求的位置（例如被吸附到別處）。"""

    def __init__(self, start_off_m: float = 0.0, end_off_m: float = 0.0) -> None:
        super().__init__()
        self.start_off_m, self.end_off_m = start_off_m, end_off_m

    def route(self, start, end, *, shortest=False):
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
    a = Part([seg([(121.48, LAT), (121.51, LAT)], index=0)], closed=False)
    bad = Part([seg([(121.52, LAT), (121.53, LAT)], index=1)], closed=False)
    c = Part([seg([(121.60, LAT), (121.61, LAT)], index=2)], closed=False)
    result = compose(IslandStraightRouter(island=frozenset(bad.segments[0].points)), [a, bad, c], Deadline(10))
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


# ---- 最終審查 M1：貪婪＋2-opt 是純 Python 的 CPU 計算，也要受期限與取消約束 ----


class CountingDeadline(Deadline):
    def __init__(self) -> None:
        super().__init__(60)
        self.checks = 0

    def check(self) -> None:
        self.checks += 1
        super().check()


def touching_parts(n: int) -> list[Part]:
    """n 個首尾相接的開放部件：串接時不需要任何 route，只剩排序的 CPU 計算。"""
    lons = [121.50 + 0.001 * k for k in range(n + 1)]
    return [Part([seg([(lons[k], LAT), (lons[k + 1], LAT)], index=k)], closed=False) for k in range(n)]


def test_ordering_checks_the_deadline_without_any_route_call() -> None:
    n = 12
    router = StraightRouter()
    deadline = CountingDeadline()
    compose(router, touching_parts(n), deadline)
    assert router.calls == []
    assert deadline.checks >= n - 2  # 2-opt 每一列至少一次


def test_cancelled_caller_stops_ordering() -> None:
    deadline = Deadline(60)
    deadline.cancel()
    with pytest.raises(MatchCancelled):
        compose(StraightRouter(), touching_parts(12), deadline)


def ring(center_lon: float, radius_deg: float, n: int) -> list[tuple[float, float]]:
    pts = [
        (center_lon + radius_deg * math.cos(2 * math.pi * k / n), LAT + radius_deg * math.sin(2 * math.pi * k / n))
        for k in range(n)
    ]
    return pts + pts[:1]


def test_closed_loop_entry_candidates_are_capped() -> None:
    # 周長約 20 km、每 10 m 一點：每 50 m 一個候選會有約 400 個，2-opt 每評估一個順序都要掃過它們
    big = Part([seg(ring(121.50, 0.03, 2000))], closed=True)
    assert len(_options(big)) <= MAX_CLOSED_ENTRIES + 1


def test_small_closed_loop_keeps_fifty_metre_candidates() -> None:
    # 周長約 630 m：每 50 m 一個候選（周長 / 24 只有約 26 m，不能比 50 m 更密）
    small = Part([seg(ring(121.50, 0.001, 120))], closed=True)
    assert 10 <= len(_options(small)) <= 13


# ---- 控制者補充：connector 接不上時，丟掉部件之前先裁切重試 ----


class BlockedStartRouter(StraightRouter):
    """終點落在 blocked 5 m 內的 route，終點會被吸到 5 m 外（那個部件的開頭落在 route 用不了的 edge 上）。"""

    def __init__(self, blocked) -> None:
        super().__init__()
        self.blocked = blocked

    def route(self, start, end, *, shortest=False):
        self.calls.append((start, end))
        if haversine_m(end, self.blocked) <= 5:
            end = (end[0], end[1] + 5 / 110_574)
        return RouteResult(points=[start, end], length_km=haversine_m(start, end) / 1000)


def test_connector_retries_with_trimmed_head_before_dropping_a_part() -> None:
    a = Part([seg([(121.50 + 0.0001 * k, LAT) for k in range(101)], index=0)], closed=False)  # 約 1 km
    b_points = [(121.52 + 0.0001 * k, LAT) for k in range(101)]
    b = Part([seg(b_points, index=1)], closed=False)
    result = compose(BlockedStartRouter(blocked=b_points[0]), [a, b], Deadline(10))
    assert result.dropped == []
    assert_continuous(result.segments)
    assert [s.kind for s in result.segments] == ["stroke", "connector", "stroke"]
    tail = result.segments[2].points
    assert b_points[len(b_points) - len(tail):] == tail and len(tail) < len(b_points)  # 只裁掉開頭


# ---- P1.5 Task 3：錨點改成最長的部件、兩端往外接、接不上原定那一端就試另一端（ledger L64） ----


class IslandStraightRouter(StraightRouter):
    """起點或終點在 island 裡的 route 一律找不到路（那個部件兩頭都出不去、也進不來）；其餘走直線。"""

    def __init__(self, island=frozenset()) -> None:
        super().__init__()
        self.island = island

    def route(self, start, end, *, shortest=False):
        self.calls.append((start, end))
        if start in self.island or end in self.island:
            raise RoutingError("island")
        return RouteResult(points=[start, end], length_km=haversine_m(start, end) / 1000)


class NoExitRouter(StraightRouter):
    """從 stuck 出發的 route 一律找不到路（像 star@taipei_daan@r30 那條 chain 的尾巴）；其餘走直線。"""

    def __init__(self, stuck) -> None:
        super().__init__()
        self.stuck = stuck

    def route(self, start, end, *, shortest=False):
        self.calls.append((start, end))
        if start == self.stuck:
            raise RoutingError("no exit")
        return RouteResult(points=[start, end], length_km=haversine_m(start, end) / 1000)


def strokes_in(result) -> list[int]:
    return [s.stroke_index for s in result.segments if s.kind == "stroke"]


def test_longest_part_is_kept_when_part_zero_is_an_island() -> None:
    small = seg([(121.500, LAT), (121.505, LAT)], index=0)
    big = seg([(121.51, LAT), (121.53, LAT)], index=1)
    router = IslandStraightRouter(island=frozenset(small.points))
    result = compose(router, [Part([small], False), Part([big], False)], Deadline(10))
    assert result.dropped == [0]  # 舊版從第 0 個部件往後接：丟掉的是 2 km 的 big
    assert result.segments == [big]


def test_part_that_cannot_be_reached_on_its_planned_side_is_attached_at_the_other_end() -> None:
    # 第 0 個部件的尾巴出不去（star@taipei_daan@r30）：舊版會把其餘部件全部丟掉
    first = seg([(121.500, LAT), (121.505, LAT)], index=0)
    rest = seg([(121.51, LAT), (121.53, LAT)], index=1)
    result = compose(NoExitRouter(stuck=first.points[-1]), [Part([first], False), Part([rest], False)], Deadline(10))
    assert result.dropped == []
    assert strokes_in(result) == [1, 0]  # 錨點（最長）先騎，接不上它開頭的部件改接在它後面
    assert_continuous(result.segments)


def test_a_stuck_tail_after_the_anchor_does_not_drop_the_parts_after_it() -> None:
    anchor = seg([(121.50, LAT), (121.53, LAT)], index=0)
    stuck = seg([(121.54, LAT), (121.55, LAT)], index=1)
    after = seg([(121.56, LAT), (121.57, LAT)], index=2)
    parts = [Part([anchor], False), Part([stuck], False), Part([after], False)]
    result = compose(NoExitRouter(stuck=stuck.points[-1]), parts, Deadline(10))
    assert result.dropped == []
    assert strokes_in(result) == [2, 0, 1]
    assert_continuous(result.segments)


def test_parts_before_the_anchor_are_joined_in_riding_order() -> None:
    # 特性測試（舊版也會過）：沒有部件被丟掉時，順序與接點都跟舊版相同
    a = seg([(121.50, LAT), (121.51, LAT)], index=0)
    b = seg([(121.52, LAT), (121.55, LAT)], index=1)  # 最長：錨點
    c = seg([(121.56, LAT), (121.57, LAT)], index=2)
    result = compose(StraightRouter(), [Part([a], False), Part([b], False), Part([c], False)], Deadline(10))
    assert [s.kind for s in result.segments] == ["stroke", "connector", "stroke", "connector", "stroke"]
    assert strokes_in(result) == [0, 1, 2]
    connector = result.segments[1]
    assert connector.points[0] == a.points[-1] and connector.points[-1] == b.points[0]  # 方向仍是 a → b


def test_parts_of_equal_length_keep_part_zero_as_anchor() -> None:
    # b 比 a 長一點點，但差距不到 ANCHOR_TIE_M：視為一樣長，取索引小的（拿掉容差，b 會變成錨點、a 被丟掉）
    a = seg([(121.50, LAT), (121.51, LAT)], index=0)
    b = seg([(121.60, LAT), (121.61 + 5e-7, LAT)], index=1)  # 比 a 長約 0.05 m
    assert 0 < b.length_m - a.length_m < ANCHOR_TIE_M
    router = IslandStraightRouter(island=frozenset(b.points))
    result = compose(router, [Part([a], False), Part([b], False)], Deadline(10))
    assert result.dropped == [1]


def test_a_single_part_is_returned_as_a_new_list() -> None:
    # 沒有其他部件可接時，輸出不能與輸入共用同一個 list
    a = seg([(121.50, LAT), (121.51, LAT)], index=0)
    part = Part([a], False)
    result = compose(StraightRouter(), [part], Deadline(10))
    assert result.segments == [a] and result.segments is not part.segments


def test_anchor_counts_matched_stroke_length_not_detour_length() -> None:
    # letter_l@taipei_riverside：part1 因為一段繞路總長比較長，但貼著畫線的部分比 part0 短；
    # 錨點要選最能代表圖形的 part0，不是被繞路灌水的 part1
    clean = seg([(121.50, LAT), (121.51, LAT)], index=0)  # 約 1 km，全是 stroke
    short_stroke = seg([(121.60, LAT), (121.603, LAT)], index=1)  # 約 0.3 km
    detour = seg([(121.603, LAT), (121.603, LAT + 0.009), (121.6031, LAT)], kind="detour", index=1)  # 約 2 km，兩端相距約 10 m
    inflated = Part([short_stroke, detour], False)
    assert sum(s.length_m for s in inflated.segments) > clean.length_m > short_stroke.length_m
    router = IslandStraightRouter(island=frozenset(p for s in inflated.segments for p in s.points))
    result = compose(router, [Part([clean], False), inflated], Deadline(10))
    assert result.dropped == [1]  # 總長當錨點會留下 part1、丟掉乾淨的 part0
    assert result.segments == [clean]


# ---- P1.5 Task 9：開放筆畫可以反向走（以反向的點重新吸附，不是把路線倒過來） ----


class RematchSpy:
    def __init__(self, result) -> None:
        self.result = result
        self.calls: list[int] = []

    def __call__(self, index: int):
        self.calls.append(index)
        return self.result


A_DRAWN = seg([(121.50, LAT), (121.51, LAT)], index=0)
B_DRAWN = seg([(121.53, LAT), (121.52, LAT)], index=1)  # 畫的方向由東往西
B_BACK = seg([(121.52, LAT), (121.53, LAT)], index=1)  # 反向重新吸附的結果


def test_open_part_is_ridden_backwards_when_that_is_closer() -> None:
    spy = RematchSpy([B_BACK])
    parts = [Part([A_DRAWN], False), Part([B_DRAWN], False, reversible=True)]
    result = compose(StraightRouter(), parts, Deadline(10), spy)
    assert spy.calls == [1]
    assert result.segments[-1] == B_BACK
    assert result.segments[1].kind == "connector"
    assert result.segments[1].length_m == pytest.approx(haversine_m((121.51, LAT), (121.52, LAT)))


def test_reverse_falls_back_to_the_drawn_direction_when_rematch_fails() -> None:
    parts = [Part([A_DRAWN], False), Part([B_DRAWN], False, reversible=True)]
    result = compose(StraightRouter(), parts, Deadline(10), RematchSpy(None))
    assert result.segments[-1] == B_DRAWN
    assert_continuous(result.segments)


def test_without_rematch_parts_keep_their_drawn_direction() -> None:
    parts = [Part([A_DRAWN], False), Part([B_DRAWN], False, reversible=True)]
    assert compose(StraightRouter(), parts, Deadline(10)).segments[-1] == B_DRAWN


def test_only_reversible_open_parts_get_a_reverse_option() -> None:
    assert [o.reverse for o in _options(Part([B_DRAWN], False))] == [False]
    assert [o.reverse for o in _options(Part([B_DRAWN], False, reversible=True))] == [False, True]
    loop = Part([seg(ring(121.50, 0.001, 120))], closed=True, reversible=True)
    assert not any(o.reverse for o in _options(loop))
