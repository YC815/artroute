"""規劃路徑交給 Valhalla 照著走（planner.follow）：假 router 的單元測試，不需要 Valhalla。

收下的條件是 Valhalla 結果與規劃路徑的雙向 Hausdorff ≤ 10 m；兩個方向各有一個測試（拿掉任一方向會紅）。
"""

import math

import pytest

from engine.matching.deadline import Deadline, MatchTimeout
from engine.planner.follow import FOLLOW_RADIUS_M, FOLLOW_STEP_M, MAX_HAUSDORFF_M, follow, hausdorff_m
from engine.planner.plan import PlannedPiece
from engine.routing.geo import haversine_m
from engine.routing.router import RouteResult, RoutingError
from osm_fixture import meter_point


def line(*xy: tuple[float, float]) -> list[tuple[float, float]]:
    return [meter_point(x, y) for x, y in xy]


PLAN = line((0.0, 0.0), (300.0, 0.0), (300.0, 200.0))  # 東 300 m 再往北 200 m


def piece(points: list[tuple[float, float]] = PLAN) -> PlannedPiece:
    return PlannedPiece(points, [(0, True)], (0, 2), 0.0)


class CannedRouter:
    """trace 回傳固定的結果（或丟 RoutingError），記下收到的點與搜尋半徑。"""

    def __init__(self, result: list[tuple[float, float]] | None) -> None:
        self.result = result
        self.calls: list[tuple[list[tuple[float, float]], float]] = []

    def trace(self, points, *, search_radius_m):
        self.calls.append((list(points), search_radius_m))
        if self.result is None:
            raise RoutingError("走不了")
        length = sum(haversine_m(a, b) for a, b in zip(self.result, self.result[1:]))
        return RouteResult(points=list(self.result), length_km=length / 1000)

    def route(self, start, end, *, shortest=False):
        raise AssertionError("follow 不呼叫 route")


# ---- Hausdorff ----


def test_hausdorff_of_identical_lines_is_at_most_half_a_step() -> None:
    assert hausdorff_m(PLAN, PLAN) <= 0.5 + 1e-9


def test_hausdorff_measures_to_segments_not_vertices() -> None:
    """結果只有頭尾兩點、規劃中間多了頂點：量到線段的距離是 0，不是到最近頂點的 150 m。"""
    straight = line((0.0, 0.0), (300.0, 0.0))
    dense = line((0.0, 0.0), (150.0, 0.0), (300.0, 0.0))
    assert hausdorff_m(straight, dense) <= 0.5 + 1e-9


def test_hausdorff_of_a_parallel_offset_is_the_offset() -> None:
    assert hausdorff_m(line((0.0, 0.0), (300.0, 0.0)), line((0.0, 7.0), (300.0, 7.0))) == pytest.approx(7.0, abs=0.6)


def test_hausdorff_sees_a_detour_away_from_the_plan() -> None:
    """結果在中間繞出去 40 m 又回來（結果 → 規劃的方向才量得到）。"""
    detour = line((0.0, 0.0), (100.0, 0.0), (100.0, 40.0), (200.0, 40.0), (200.0, 0.0), (300.0, 0.0))
    assert hausdorff_m(detour, line((0.0, 0.0), (300.0, 0.0))) == pytest.approx(40.0, abs=0.6)


def test_hausdorff_sees_a_truncated_result() -> None:
    """結果只走了規劃的前一半（規劃 → 結果的方向才量得到）。"""
    assert hausdorff_m(line((0.0, 0.0), (150.0, 0.0)), line((0.0, 0.0), (300.0, 0.0))) == pytest.approx(150.0, abs=0.6)


# ---- follow ----


def test_close_result_is_kept_as_a_stroke_segment_of_valhalla_points() -> None:
    result = line((0.0, 4.0), (296.0, 4.0), (296.0, 200.0))  # 平移 4 m：仍是同一條路的吸附結果
    router = CannedRouter(result)
    segments = follow(router, piece(), Deadline(5), stroke_index=3)
    assert segments is not None and len(segments) == 1
    (segment,) = segments
    assert segment.kind == "stroke" and segment.stroke_index == 3
    assert segment.points == result  # 點全部來自 Valhalla 的結果，一點也沒有加
    assert segment.length_m == pytest.approx(sum(haversine_m(a, b) for a, b in zip(result, result[1:])), abs=1)


def test_trace_gets_the_plan_densified_and_a_small_radius() -> None:
    router = CannedRouter(PLAN)
    follow(router, piece(), Deadline(5), stroke_index=0)
    (points, radius), = router.calls
    assert radius == FOLLOW_RADIUS_M == 10.0
    assert points[0] == PLAN[0] and points[-1] == PLAN[-1] and PLAN[1] in points
    assert max(haversine_m(a, b) for a, b in zip(points, points[1:])) <= FOLLOW_STEP_M + 1e-6


@pytest.mark.parametrize("shift_m", [MAX_HAUSDORFF_M + 1.0, 40.0])
def test_result_farther_than_the_limit_is_rejected(shift_m: float) -> None:
    """平移超過上限的結果（例如 Valhalla 改走旁邊另一條路）不收。拿掉 Hausdorff 檢查時這裡會紅。"""
    router = CannedRouter(line((0.0, shift_m), (300.0 - shift_m, shift_m), (300.0 - shift_m, 200.0)))
    assert follow(router, piece(), Deadline(5), stroke_index=0) is None


def test_result_that_detours_and_comes_back_is_rejected() -> None:
    detour = line((0.0, 0.0), (100.0, 0.0), (100.0, -40.0), (200.0, -40.0), (200.0, 0.0), (300.0, 0.0), (300.0, 200.0))
    assert follow(CannedRouter(detour), piece(), Deadline(5), stroke_index=0) is None


def test_truncated_result_is_rejected() -> None:
    assert follow(CannedRouter(line((0.0, 0.0), (300.0, 0.0))), piece(), Deadline(5), stroke_index=0) is None


def test_routing_error_means_not_followed() -> None:
    assert follow(CannedRouter(None), piece(), Deadline(5), stroke_index=0) is None


def test_expired_deadline_raises_before_calling_valhalla() -> None:
    router = CannedRouter(PLAN)
    with pytest.raises(MatchTimeout):
        follow(router, piece(), Deadline(-1), stroke_index=0)
    assert router.calls == []


def test_long_plan_is_measured_without_blowing_up() -> None:
    """10 km 的路徑（取樣上萬點）也能量：分批算點到線段距離。"""
    long = line(*((100.0 * k, 30.0 * math.sin(k)) for k in range(101)))
    assert follow(CannedRouter(long), piece(long), Deadline(5), stroke_index=0) is not None
