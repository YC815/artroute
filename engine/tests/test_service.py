from pathlib import Path

import pytest

from engine.matching.deadline import Deadline, MatchTimeout
from engine.matching.service import (
    AllStrokesFailed,
    OutsideTaiwan,
    StrokeInput,
    TargetTooSmall,
    default_search_radius,
    match_shape,
)
from engine.matching.types import JOIN_TOLERANCE_M
from engine.routing.geo import haversine_m, polyline_length_m
from engine.routing.router import RouteResult, Router, RoutingError
from grid_helpers import densify
from osm_fixture import grid_point

LAT = 25.05


def test_default_radius_is_clamped() -> None:
    tiny = [StrokeInput([(121.5, LAT), (121.501, LAT)], closed=False)]
    huge = [StrokeInput([(121.0, LAT), (121.5, LAT)], closed=False)]
    mid = [StrokeInput([(121.50, LAT), (121.52, LAT)], closed=False)]  # 約 2016 m → 40 m
    assert default_search_radius(tiny) == 30
    assert default_search_radius(huge) == 150
    assert default_search_radius(mid) == pytest.approx(40, abs=1)


def test_outside_taiwan_rejected() -> None:
    with pytest.raises(OutsideTaiwan):
        match_shape(None, [StrokeInput([(130.0, 30.0), (130.1, 30.0)], False)], search_radius_m=None, deadline=Deadline(5))


@pytest.mark.parametrize("outlier", [(130.0, 30.0), (121.5, 21.0), (float("nan"), LAT)])
def test_any_point_outside_taiwan_rejects_whole_shape(outlier: tuple[float, float]) -> None:
    strokes = [StrokeInput([(121.5, LAT), (121.51, LAT)], False), StrokeInput([(121.5, LAT), outlier], False)]
    with pytest.raises(OutsideTaiwan):
        match_shape(None, strokes, search_radius_m=None, deadline=Deadline(5))


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
    """trace：落在 bad 經度區間的點就失敗；route：直接回兩端點，但終點落在 dead 區間就失敗。"""

    def __init__(self, bad: tuple[float, float] | None = None, dead: tuple[float, float] | None = None) -> None:
        self.bad, self.dead = bad, dead
        self.radii: list[float] = []

    def trace(self, points, *, search_radius_m):
        self.radii.append(search_radius_m)
        if self.bad and any(self.bad[0] <= lon <= self.bad[1] for lon, _ in points):
            raise RoutingError("bad")
        return RouteResult(points=list(points), length_km=polyline_length_m(points) / 1000)

    def route(self, start, end):
        if self.dead and self.dead[0] <= end[0] <= self.dead[1]:
            raise RoutingError("dead")
        return RouteResult(points=[start, end], length_km=haversine_m(start, end) / 1000)


def row(lon_from: float, lon_to: float, lat: float = LAT, n: int = 8) -> list[tuple[float, float]]:
    return [(lon_from + (lon_to - lon_from) * i / n, lat) for i in range(n + 1)]


def test_dropped_stroke_warns_no_route_with_its_own_index() -> None:
    # 第 1 筆畫（121.52 一帶）trace 成功，但串接器的 route 到不了它：整筆畫被丟掉
    strokes = [StrokeInput(row(121.50, 121.51), False), StrokeInput(row(121.52, 121.53), False)]
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
    assert haversine_m(loop[0], (121.525, LAT)) < 60  # 不是從角落 (121.52, LAT) 進
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
    strokes = [StrokeInput(row(121.50, 121.51), False), StrokeInput(row(121.52, 121.56, n=40), False)]
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
