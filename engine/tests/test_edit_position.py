"""路線上的位置（route editing spec §3.1）：{segment, offset_m} 換算成切點、沿路線的公尺數，以及在兩個切點把路線切成三段。
切開的部分只能是原本的幾何：點全是原本的點，加上落在原折線上的內插切點。"""

import math

import pytest

from engine.edit.position import Cut, NotOnRoute, RoutePos, arc_m, order, pos_at_arc, resolve, split
from engine.matching.types import Segment
from engine.routing.geo import EARTH_RADIUS_M, haversine_m, polyline_length_m

LAT = 25.05
STEP = 0.001  # 約 100 m


def seg(points, kind="stroke", stroke_index: int | None = 0) -> Segment:
    return Segment(kind, stroke_index, points, round(polyline_length_m(points), 3))


A, B, C, D = [(121.500 + STEP * i, LAT) for i in range(4)]
E, F = (121.503, LAT + STEP), (121.503, LAT + 2 * STEP)
S0 = seg([A, B, C])  # 約 200 m
S1 = seg([C, D], kind="detour")
S2 = seg([D, E, F], kind="connector", stroke_index=None)
ROUTE = [S0, S1, S2]
ORIGINAL = {p for s in ROUTE for p in s.points}


def _xy(p: tuple[float, float]) -> tuple[float, float]:
    k = math.radians(1) * EARTH_RADIUS_M
    return p[0] * k * math.cos(math.radians(LAT)), p[1] * k


def distance_to_polyline_m(point, polyline) -> float:
    px, py = _xy(point)
    best = math.inf
    for a, b in zip(polyline, polyline[1:]):
        (ax, ay), (bx, by) = _xy(a), _xy(b)
        dx, dy = bx - ax, by - ay
        t = 0.0 if dx == dy == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
        best = min(best, math.hypot(px - ax - t * dx, py - ay - t * dy))
    return best


def test_resolve_inside_a_segment_interpolates_on_the_polyline() -> None:
    ab = haversine_m(A, B)
    cut = resolve(ROUTE, RoutePos(0, ab + 30.0))
    assert (cut.segment, cut.index) == (0, 1)
    assert haversine_m(B, cut.point) == pytest.approx(30.0, abs=1e-3)
    assert distance_to_polyline_m(cut.point, S0.points) < 1e-6


def test_resolve_on_a_vertex_returns_that_vertex() -> None:
    assert resolve(ROUTE, RoutePos(0, haversine_m(A, B))) == Cut(0, 1, B)
    assert resolve(ROUTE, RoutePos(1, 0.0)) == Cut(1, 0, C)


def test_resolve_at_the_segment_end_and_within_1m_past_it_clamps_to_the_last_point() -> None:
    length = polyline_length_m(S0.points)
    assert resolve(ROUTE, RoutePos(0, length)) == Cut(0, 1, C)
    assert resolve(ROUTE, RoutePos(0, length + 0.9)) == Cut(0, 1, C)


@pytest.mark.parametrize("pos", [
    RoutePos(-1, 0.0), RoutePos(3, 0.0), RoutePos(0, -0.1), RoutePos(0, math.nan),
    RoutePos(0, polyline_length_m(S0.points) + 1.1),
])
def test_resolve_rejects_positions_off_the_route(pos: RoutePos) -> None:
    with pytest.raises(NotOnRoute):
        resolve(ROUTE, pos)


def test_not_on_route_is_a_value_error() -> None:
    assert issubclass(NotOnRoute, ValueError)


ZERO = seg([D, D], kind="connector", stroke_index=None)
WITH_ZERO = [S0, S1, ZERO, S2]
TOTAL = sum(polyline_length_m(s.points) for s in WITH_ZERO)


@pytest.mark.parametrize("arc", [0.0, 50.0, 199.0, polyline_length_m(S0.points), 250.0, 300.0, 350.0, TOTAL])
def test_arc_m_undoes_pos_at_arc_across_segments(arc: float) -> None:
    assert arc_m(WITH_ZERO, pos_at_arc(WITH_ZERO, arc)) == pytest.approx(arc, abs=1e-9)


@pytest.mark.parametrize("pos", [RoutePos(0, 10.0), RoutePos(0, 150.0), RoutePos(1, 40.0), RoutePos(3, 120.0)])
def test_pos_at_arc_undoes_arc_m_inside_a_segment(pos: RoutePos) -> None:
    back = pos_at_arc(WITH_ZERO, arc_m(WITH_ZERO, pos))
    assert back.segment == pos.segment and back.offset_m == pytest.approx(pos.offset_m, abs=1e-9)


def test_arc_m_counts_geometric_lengths_of_earlier_segments() -> None:
    before = polyline_length_m(S0.points) + polyline_length_m(S1.points)
    assert arc_m(WITH_ZERO, RoutePos(2, 0.0)) == pytest.approx(before)
    assert arc_m(WITH_ZERO, RoutePos(3, 5.0)) == pytest.approx(before + 5.0)


def test_pos_at_arc_clamps_to_the_route() -> None:
    assert arc_m(WITH_ZERO, pos_at_arc(WITH_ZERO, -5.0)) == 0.0
    assert arc_m(WITH_ZERO, pos_at_arc(WITH_ZERO, TOTAL + 100.0)) == pytest.approx(TOTAL)


def test_order_puts_the_earlier_position_first() -> None:
    early, late = RoutePos(0, 150.0), RoutePos(2, 10.0)
    assert order(ROUTE, late, early) == (early, late)
    assert order(ROUTE, early, late) == (early, late)


def _check_split(route: list[Segment], start: Cut, end: Cut) -> tuple[list[Segment], list[Segment], list[Segment]]:
    """三段首尾相接、點都是原本的點或切點、切點落在原折線上、切出來的片段至少 2 個不同的點。"""
    parts = split(route, start, end)
    pieces = [s for part in parts for s in part]
    for left, right in zip(pieces, pieces[1:]):
        assert haversine_m(left.points[-1], right.points[0]) <= 1e-6
    original = {p for s in route for p in s.points}
    assert all(p in original or p in (start.point, end.point) for s in pieces for p in s.points)
    for cut in (start, end):
        assert distance_to_polyline_m(cut.point, route[cut.segment].points) < 1e-6
    assert all(len(set(s.points)) >= 2 for s in pieces)
    before, middle, after = parts
    if before:
        assert before[-1].points[-1] == start.point
    if middle:
        assert middle[0].points[0] == start.point and middle[-1].points[-1] == end.point
    if after:
        assert after[0].points[0] == end.point
    return parts


def test_split_across_segments_keeps_the_rest_untouched() -> None:
    start, end = resolve(ROUTE, RoutePos(0, 150.0)), resolve(ROUTE, RoutePos(2, 40.0))
    before, middle, after = _check_split(ROUTE, start, end)
    assert [s.points for s in before] == [[A, B, start.point]]
    assert [s.points for s in middle] == [[start.point, C], S1.points, [D, end.point]]
    assert middle[1] is S1
    assert [s.points for s in after] == [[end.point, E, F]]
    assert [(s.kind, s.stroke_index) for s in [*before, *middle, *after]] == [
        ("stroke", 0), ("stroke", 0), ("detour", 0), ("connector", None), ("connector", None),
    ]
    assert before[0].length_m == pytest.approx(150.0, abs=1e-3)  # 切開的片段用幾何長度


def test_split_twice_inside_one_segment() -> None:
    start, end = resolve(ROUTE, RoutePos(0, 20.0)), resolve(ROUTE, RoutePos(0, 180.0))
    before, middle, after = _check_split(ROUTE, start, end)
    assert [s.points for s in before] == [[A, start.point]]
    assert [s.points for s in middle] == [[start.point, B, end.point]]
    assert [s.points for s in after] == [[end.point, C], S1.points, S2.points]


def test_split_between_two_points_of_the_same_edge() -> None:
    start, end = resolve(ROUTE, RoutePos(0, 20.0)), resolve(ROUTE, RoutePos(0, 40.0))
    _, middle, _ = _check_split(ROUTE, start, end)
    assert [s.points for s in middle] == [[start.point, end.point]]


def test_split_on_vertices_does_not_repeat_them() -> None:
    start, end = resolve(ROUTE, RoutePos(0, haversine_m(A, B))), resolve(ROUTE, RoutePos(2, 0.0))
    before, middle, after = _check_split(ROUTE, start, end)
    assert [s.points for s in before] == [[A, B]]
    assert [s.points for s in middle] == [[B, C], S1.points]
    assert [s.points for s in after] == [S2.points]
    assert after[0] is S2


def test_split_drops_pieces_with_fewer_than_two_distinct_points() -> None:
    start = resolve(ROUTE, RoutePos(0, polyline_length_m(S0.points)))  # S0 的終點
    end = resolve(ROUTE, RoutePos(2, polyline_length_m(S2.points)))  # 路線終點
    before, middle, after = _check_split(ROUTE, start, end)
    assert before == [S0] and before[0] is S0
    assert middle == [S1, S2]
    assert after == []


def test_split_from_the_route_start() -> None:
    before, middle, _ = _check_split(ROUTE, resolve(ROUTE, RoutePos(0, 0.0)), resolve(ROUTE, RoutePos(1, 10.0)))
    assert before == []
    assert middle[0] is S0


def test_split_rejects_start_after_end() -> None:
    early, late = resolve(ROUTE, RoutePos(0, 20.0)), resolve(ROUTE, RoutePos(0, 40.0))
    with pytest.raises(ValueError):
        split(ROUTE, late, early)
    with pytest.raises(ValueError):
        split(ROUTE, resolve(ROUTE, RoutePos(1, 0.0)), early)
