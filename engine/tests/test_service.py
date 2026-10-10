import logging
from pathlib import Path

import pytest

from engine.matching.compose import Composition
from engine.matching.deadline import Deadline, MatchTimeout
from engine.matching.service import (
    AllStrokesFailed,
    DiscontinuousRoute,
    OutsideTaiwan,
    StrokeInput,
    TargetTooSmall,
    _stitch_closed,
    default_search_radius,
    match_shape,
)
from engine.matching.types import JOIN_TOLERANCE_M, Segment
from engine.routing.geo import haversine_m, polyline_length_m
from engine.routing.router import RouteResult, Router, RoutingError
from grid_helpers import densify, rides_westward_on_row
from island_router import IslandRouter, across_island
from osm_fixture import grid_point

LAT = 25.05


def test_default_radius_is_clamped() -> None:
    tiny = [StrokeInput([(121.5, LAT), (121.501, LAT)], closed=False)]
    huge = [StrokeInput([(121.0, LAT), (121.5, LAT)], closed=False)]
    mid = [StrokeInput([(121.50, LAT), (121.52, LAT)], closed=False)]  # 約 2016 m × 0.035 → 約 71 m
    assert default_search_radius(tiny) == 30
    assert default_search_radius(huge) == 150
    assert default_search_radius(mid) == pytest.approx(70.6, abs=1)


def test_outside_taiwan_rejected() -> None:
    with pytest.raises(OutsideTaiwan):
        match_shape(None, [StrokeInput([(130.0, 30.0), (130.1, 30.0)], False)], search_radius_m=None, deadline=Deadline(5))


@pytest.mark.parametrize("outlier", [(130.0, 30.0), (121.5, 21.0), (float("nan"), LAT)])
def test_any_point_outside_taiwan_rejects_whole_shape(outlier: tuple[float, float]) -> None:
    strokes = [StrokeInput([(121.5, LAT), (121.51, LAT)], False), StrokeInput([(121.5, LAT), outlier], False)]
    with pytest.raises(OutsideTaiwan):
        match_shape(None, strokes, search_radius_m=None, deadline=Deadline(5))


def test_tiny_extent_with_long_path_is_too_small() -> None:
    # 在 0.5 m 內來回抖 200 次：總長夠，但對角線太短，score() 無法評分；要在入口擋下，不能變成 ValueError
    jitter = [(121.5 + 0.000005 * (i % 2), LAT) for i in range(201)]
    with pytest.raises(TargetTooSmall):
        match_shape(FakeRouter(), [StrokeInput(jitter, False)], search_radius_m=50, deadline=Deadline(5))


def test_too_small_rejected() -> None:
    with pytest.raises(TargetTooSmall):
        match_shape(None, [StrokeInput([(121.5, LAT), (121.5001, LAT)], False)], search_radius_m=None, deadline=Deadline(5))


@pytest.mark.valhalla
def test_two_strokes_become_one_continuous_route(grid_config: Path) -> None:
    router = Router(grid_config)
    strokes = [
        StrokeInput(densify(grid_point(0, 1), grid_point(2, 1), n=8), closed=False),
        StrokeInput(densify(grid_point(4, 1), grid_point(6, 1), n=8), closed=False),
    ]
    result = match_shape(router, strokes, search_radius_m=50, deadline=Deadline(30))
    for a, b in zip(result.route_points, result.route_points[1:]):
        assert haversine_m(a, b) < 300  # 都是 Valhalla 的節點間距，沒有跨越式直線
    kinds = [s.kind for s in result.segments]
    assert kinds.count("stroke") == 2 and "connector" in kinds
    for a, b in zip(result.segments, result.segments[1:]):
        assert haversine_m(a.points[-1], b.points[0]) <= JOIN_TOLERANCE_M
    assert result.score.coverage >= 90
    assert result.target_length_m == pytest.approx(804, abs=40)
    assert result.warnings == []


@pytest.mark.valhalla
def test_all_strokes_failing_raises_with_warnings(grid_config: Path) -> None:
    router = Router(grid_config)
    strokes = [StrokeInput(densify((121.0, 24.0), (121.003, 24.0), n=8), closed=False)]
    with pytest.raises(AllStrokesFailed) as info:
        match_shape(router, strokes, search_radius_m=50, deadline=Deadline(30))
    assert [w.code for w in info.value.warnings] == ["no_route"]


# 以下用假 router 驗證串接與警告的流程：不需要 Valhalla。


class FakeRouter:
    """trace：落在 bad 經度區間的點就失敗；route：直接回兩端點，但起點或終點落在 dead 區間就失敗（孤島：出不去也進不來）。"""

    def __init__(self, bad: tuple[float, float] | None = None, dead: tuple[float, float] | None = None) -> None:
        self.bad, self.dead = bad, dead
        self.radii: list[float] = []

    def trace(self, points, *, search_radius_m):
        self.radii.append(search_radius_m)
        if self.bad and any(self.bad[0] <= lon <= self.bad[1] for lon, _ in points):
            raise RoutingError("bad")
        return RouteResult(points=list(points), length_km=polyline_length_m(points) / 1000)

    def route(self, start, end, *, shortest=False):
        if self.dead and any(self.dead[0] <= lon <= self.dead[1] for lon, _ in (start, end)):
            raise RoutingError("dead")
        return RouteResult(points=[start, end], length_km=haversine_m(start, end) / 1000)


def row(lon_from: float, lon_to: float, lat: float = LAT, n: int = 8) -> list[tuple[float, float]]:
    return [(lon_from + (lon_to - lon_from) * i / n, lat) for i in range(n + 1)]


def test_dropped_stroke_warns_no_route_with_its_own_index() -> None:
    # 第 1 筆畫（121.52 一帶）trace 成功，但串接器的 route 到不了它：整筆畫被丟掉
    strokes = [StrokeInput(row(121.46, 121.51), False), StrokeInput(row(121.52, 121.53), False)]
    result = match_shape(FakeRouter(dead=(121.52, 121.53)), strokes, search_radius_m=50, deadline=Deadline(30))
    assert [(w.stroke_index, w.code) for w in result.warnings] == [(1, "no_route")]
    assert {s.stroke_index for s in result.segments} == {0}


def test_partly_dropped_stroke_warns_partial_once() -> None:
    # 筆畫 1 的中間有一段 trace 與 route 都失敗，斷成兩條 chain；串接器只丟掉後一條
    strokes = [StrokeInput(row(121.50, 121.51), False), StrokeInput(row(121.52, 121.56, n=40), False)]
    router = FakeRouter(bad=(121.535, 121.545), dead=(121.545, 121.56))
    result = match_shape(router, strokes, search_radius_m=50, deadline=Deadline(30))
    assert [(w.stroke_index, w.code) for w in result.warnings] == [(1, "partial")]
    assert 1 in {s.stroke_index for s in result.segments}  # 前一條 chain 還在路線上


def test_closed_stroke_is_entered_near_previous_end_and_returns_there() -> None:
    square = [(121.52, LAT), (121.53, LAT), (121.53, LAT + 0.01), (121.52, LAT + 0.01)]
    approach = row(121.50, 121.525, LAT - 0.005)  # 終點正對方形下緣中點
    strokes = [StrokeInput(approach, False), StrokeInput(square, True)]
    result = match_shape(FakeRouter(), strokes, search_radius_m=50, deadline=Deadline(30))
    assert result.warnings == []
    loop = [p for s in result.segments if s.stroke_index == 1 for p in s.points]
    # 不是從角落 (121.52, LAT)（約 505 m 外）進。周長約 4 km，進入點每 max(50 m, 周長 / 24) ≈ 175 m 一個，
    # 離前一段終點最近的那個在半個間隔內
    assert haversine_m(loop[0], (121.525, LAT)) < 100
    assert haversine_m(loop[0], loop[-1]) <= JOIN_TOLERANCE_M  # 繞一圈回到進入點


def test_result_numbers_are_consistent() -> None:
    strokes = [StrokeInput(row(121.50, 121.51), False), StrokeInput(row(121.52, 121.53), False)]
    result = match_shape(FakeRouter(), strokes, search_radius_m=50, deadline=Deadline(30))
    assert result.route_length_m == pytest.approx(sum(s.length_m for s in result.segments))
    assert result.route_points[0] == result.segments[0].points[0]
    assert result.route_points[-1] == result.segments[-1].points[-1]
    assert result.target_length_m == pytest.approx(polyline_length_m(row(121.50, 121.51)) * 2, rel=0.01)
    assert result.warnings == []


def test_expired_deadline_propagates_match_timeout() -> None:
    strokes = [StrokeInput(row(121.50, 121.51), False)]
    with pytest.raises(MatchTimeout):
        match_shape(FakeRouter(), strokes, search_radius_m=50, deadline=Deadline(-1))


def test_stroke_with_skipped_piece_and_all_chains_dropped_reports_only_no_route() -> None:
    # 筆畫 1 中間有一段找不到路（吸附階段記 partial），剩下的 chain 又全被串接器丟掉：只留最嚴重的 no_route
    strokes = [StrokeInput(row(121.46, 121.51), False), StrokeInput(row(121.52, 121.56, n=40), False)]
    router = FakeRouter(bad=(121.535, 121.545), dead=(121.52, 121.56))
    result = match_shape(router, strokes, search_radius_m=50, deadline=Deadline(30))
    assert [(w.stroke_index, w.code) for w in result.warnings] == [(1, "no_route")]
    assert {s.stroke_index for s in result.segments} == {0}


def test_search_radius_is_explicit_or_derived_from_shape() -> None:
    strokes = [StrokeInput(row(121.50, 121.51), False)]
    explicit, derived = FakeRouter(), FakeRouter()
    match_shape(explicit, strokes, search_radius_m=77, deadline=Deadline(30))
    match_shape(derived, strokes, search_radius_m=None, deadline=Deadline(30))
    assert set(explicit.radii) == {77}
    assert set(derived.radii) == {default_search_radius(strokes)}


@pytest.mark.parametrize("radius", [0, -5, 29.9, 150.1, float("nan"), float("inf")])
def test_invalid_search_radius_is_rejected(radius: float) -> None:
    strokes = [StrokeInput(row(121.50, 121.51), False)]
    with pytest.raises(ValueError):
        match_shape(FakeRouter(), strokes, search_radius_m=radius, deadline=Deadline(30))


@pytest.mark.parametrize("radius", [30, 150])
def test_search_radius_bounds_are_inclusive(radius: float) -> None:
    router = FakeRouter()
    match_shape(router, [StrokeInput(row(121.50, 121.51), False)], search_radius_m=radius, deadline=Deadline(30))
    assert set(router.radii) == {radius}


class ThreePointConnectorRouter(FakeRouter):
    """route 回傳 [start, start, end]：3 個點、2 個不同的點（Valhalla 的 connector 可能長這樣）。"""

    def route(self, start, end, *, shortest=False):
        return RouteResult(points=[start, start, end], length_km=haversine_m(start, end) / 1000)


def test_route_points_have_no_consecutive_duplicates() -> None:
    strokes = [StrokeInput(row(121.50, 121.51), False), StrokeInput(row(121.52, 121.53), False)]
    result = match_shape(ThreePointConnectorRouter(), strokes, search_radius_m=50, deadline=Deadline(30))
    assert "connector" in [s.kind for s in result.segments]
    assert all(a != b for a, b in zip(result.route_points, result.route_points[1:]))


@pytest.mark.valhalla
def test_stroke_far_from_any_road_is_reported_and_left_out_of_route(grid_config: Path) -> None:
    # 第 6 列離最近的可騎道路約 660 m：不能被 Valhalla 抓到遠處的路當成一條繞路
    strokes = [
        StrokeInput(densify(grid_point(0, 1), grid_point(2, 1), n=8), closed=False),
        StrokeInput(densify(grid_point(3, 6), grid_point(5, 6), n=8), closed=False),
    ]
    result = match_shape(Router(grid_config), strokes, search_radius_m=50, deadline=Deadline(30))
    assert [(w.stroke_index, w.code) for w in result.warnings] == [(1, "no_route")]
    assert {s.stroke_index for s in result.segments} == {0}
    assert result.route_length_m == pytest.approx(404, abs=40)


@pytest.mark.valhalla
def test_mixed_shape_legal_wrong_way_and_closed_loop(grid_config: Path) -> None:
    strokes = [
        StrokeInput(densify(grid_point(0, 1), grid_point(2, 1), n=8), closed=False),  # 合法
        StrokeInput(densify(grid_point(2, 0), grid_point(0, 0), n=8), closed=False),  # 逆向單行道
        StrokeInput(  # 右下街區的封閉圈，起點在遠離其他筆畫的右下角
            [grid_point(5, 0), grid_point(4, 0), grid_point(4, 1), grid_point(5, 1)], closed=True
        ),
    ]
    result = match_shape(Router(grid_config), strokes, search_radius_m=50, deadline=Deadline(30))
    assert result.warnings == []
    for a, b in zip(result.segments, result.segments[1:]):
        assert haversine_m(a.points[-1], b.points[0]) <= JOIN_TOLERANCE_M
    assert not rides_westward_on_row(result.route_points, row=0, col_min=0, col_max=2)
    wrong_way = [s for s in result.segments if s.stroke_index == 1]
    assert "detour" in {s.kind for s in wrong_way}  # 逆向那筆改用合法繞路
    assert "connector" in {s.kind for s in result.segments}
    loop = [p for s in result.segments if s.stroke_index == 2 for p in s.points]
    assert haversine_m(loop[0], loop[-1]) <= JOIN_TOLERANCE_M  # 封閉圈繞一圈回到進入點
    assert haversine_m(loop[0], grid_point(5, 0)) > 100  # 已旋轉：從離前一段最近的地方進，不是畫的起點


# ---- 最終審查 I-1：輸出前檢查連續性；任何接點超過 JOIN_TOLERANCE_M 都不能變成直線輸出 ----


def assert_joins_within_tolerance(segments) -> None:
    for a, b in zip(segments, segments[1:]):
        assert haversine_m(a.points[-1], b.points[0]) <= JOIN_TOLERANCE_M


def test_island_shape_route_has_no_straight_gap() -> None:
    result = match_shape(IslandRouter(), [StrokeInput(across_island(), False)], search_radius_m=30, deadline=Deadline(30))
    assert result.segments
    assert_joins_within_tolerance(result.segments)
    assert [w.code for w in result.warnings] == ["partial"]


def test_gap_between_segments_is_an_internal_error_not_a_straight_line(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # 吸附或串接若有漏洞留下缺口，輸出一條直線比回 500 更糟：在 _route_points 之前擋下並記錄
    def gappy(router, parts, deadline) -> Composition:
        a = Segment("stroke", 0, [(121.50, LAT), (121.51, LAT)], 1000.0)
        b = Segment("detour", 0, [(121.5101, LAT), (121.52, LAT)], 1000.0)  # 與 a 相距約 10 m
        return Composition(segments=[a, b], dropped=[])

    monkeypatch.setattr("engine.matching.service.compose", gappy)
    with caplog.at_level(logging.ERROR, logger="engine.matching.service"), pytest.raises(DiscontinuousRoute):
        match_shape(FakeRouter(), [StrokeInput(row(121.50, 121.51), False)], search_radius_m=50, deadline=Deadline(30))
    assert [r.levelno for r in caplog.records] == [logging.ERROR]


# ---- P1.5 Task 4：封閉筆畫斷成多條 chain 時，照筆畫順序接回 ----


def seg(points, kind="stroke", index=0) -> Segment:
    return Segment(kind, index, points, polyline_length_m(points))


class BlockedRouter:
    """route：起點或終點在 blocked 裡就找不到路，其餘走直線。"""

    def __init__(self, blocked=frozenset()) -> None:
        self.blocked = blocked
        self.calls = 0

    def trace(self, points, *, search_radius_m):
        raise AssertionError("_stitch_closed 不呼叫 trace")

    def route(self, start, end, *, shortest=False):
        self.calls += 1
        if start in self.blocked or end in self.blocked:
            raise RoutingError("blocked")
        return RouteResult(points=[start, end], length_km=haversine_m(start, end) / 1000)


SW, SE, NE, NW = (121.52, LAT), (121.53, LAT), (121.53, LAT + 0.01), (121.52, LAT + 0.01)
BOTTOM = seg([SW, SE])  # 第一條 chain：下緣
REST = seg([NE, NW, SW])  # 最後一條 chain：上緣與左緣，回到起點（右緣沒吸附到）


def test_stitch_merges_the_last_chain_into_the_first_when_the_gap_cannot_be_bridged() -> None:
    chains, closed = _stitch_closed(BlockedRouter(blocked=frozenset({SE})), Deadline(10), [[BOTTOM], [REST]])
    assert (chains, closed) == ([[REST, BOTTOM]], False)


def test_stitch_bridges_the_gap_and_closes_the_loop() -> None:
    chains, closed = _stitch_closed(BlockedRouter(), Deadline(10), [[BOTTOM], [REST]])
    assert closed and len(chains) == 1
    assert [s.kind for s in chains[0]] == ["stroke", "connector", "stroke"]
    assert chains[0][1].points == [SE, NE] and chains[0][1].stroke_index is None


def test_stitch_leaves_chains_apart_when_nothing_connects() -> None:
    rest_off = seg([NE, NW, (121.52, LAT + 0.0001)])  # 終點離起點約 11 m：收尾不相鄰
    chains, closed = _stitch_closed(BlockedRouter(blocked=frozenset({SE})), Deadline(10), [[BOTTOM], [rest_off]])
    assert (chains, closed) == ([[BOTTOM], [rest_off]], False)


def test_stitch_never_adds_a_closing_route_to_a_single_chain() -> None:
    router = BlockedRouter()
    loop_off = seg([SW, SE, NE, NW, (121.52, LAT + 0.0001)])
    assert _stitch_closed(router, Deadline(10), [[loop_off]]) == ([[loop_off]], False)
    assert router.calls == 0  # 收尾不相鄰時不補 route
    loop = seg([SW, SE, NE, NW, SW])
    assert _stitch_closed(router, Deadline(10), [[loop]]) == ([[loop]], True)


def test_stitch_merges_only_the_breaks_that_connect() -> None:
    first, middle, last = seg([SW, SE]), seg([NE, NW]), seg([NW, SW])  # 下緣、上緣、左緣
    # 右緣（SE → NE）接不上；上緣 → 左緣相鄰；左緣回到起點相鄰
    chains, closed = _stitch_closed(BlockedRouter(blocked=frozenset({SE})), Deadline(10), [[first], [middle], [last]])
    assert (chains, closed) == ([[middle, last, first]], False)


class RejectingDetourRouter:
    """trace：有點落在 box（經緯度範圍）裡就失敗，其餘原樣回傳；route：直線，長度一律回 1 m。
    所以 box 那段的繞路都被 MIN_LENGTH_RATIO 拒絕、筆畫在那裡斷開（ledger L34）；connector 不看長度，接得上。"""

    def __init__(self, box: tuple[float, float, float, float]) -> None:
        self.box = box

    def _inside(self, point) -> bool:
        lon0, lat0, lon1, lat1 = self.box
        return lon0 <= point[0] <= lon1 and lat0 <= point[1] <= lat1

    def trace(self, points, *, search_radius_m):
        if any(self._inside(p) for p in points):
            raise RoutingError("box")
        return RouteResult(points=list(points), length_km=polyline_length_m(points) / 1000)

    def route(self, start, end, *, shortest=False):
        return RouteResult(points=[start, end], length_km=0.001)


def test_closed_stroke_broken_by_a_rejected_detour_is_still_a_loop() -> None:
    square = [SW, SE, NE, NW]
    approach = row(121.50, 121.525, LAT - 0.005)  # 終點正對方形下緣中點
    router = RejectingDetourRouter(box=(121.529, LAT + 0.004, 121.531, LAT + 0.006))  # 右緣中段
    strokes = [StrokeInput(approach, False), StrokeInput(square, True)]
    result = match_shape(router, strokes, search_radius_m=50, deadline=Deadline(30))
    assert [(w.stroke_index, w.code) for w in result.warnings] == [(1, "partial")]
    assert_joins_within_tolerance(result.segments)
    loop = [p for s in result.segments if s.stroke_index == 1 for p in s.points]
    # 接回成圈之後可以旋轉：從離前一段最近的地方進（舊版是兩個開放部件，只能從角落 SW 進，約 505 m 外）
    assert haversine_m(loop[0], (121.525, LAT)) < 100
