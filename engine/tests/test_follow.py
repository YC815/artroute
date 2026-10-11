"""規劃路徑交給 Valhalla 照著走（planner.follow）：假 router 的單元測試，不需要 Valhalla。

收下的條件是 Valhalla 結果與規劃路徑的雙向 Hausdorff ≤ 10 m；兩個方向各有一個測試（拿掉任一方向會紅）。
"""

import math
import time

import numpy as np
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
    """結果只有頭尾兩點、規劃中間多了頂點：精確值是 0（不是到最近頂點的 150 m），上界最多多一個取樣間距。"""
    straight = line((0.0, 0.0), (300.0, 0.0))
    dense = line((0.0, 0.0), (150.0, 0.0), (300.0, 0.0))
    assert hausdorff_m(straight, dense) <= 1.0 + 1e-9


def test_hausdorff_of_a_parallel_offset_is_the_offset() -> None:
    assert hausdorff_m(line((0.0, 0.0), (300.0, 0.0)), line((0.0, 7.0), (300.0, 7.0))) == pytest.approx(7.0, abs=0.6)


def test_hausdorff_sees_a_detour_away_from_the_plan() -> None:
    """結果在中間繞出去 40 m 又回來（結果 → 規劃的方向才量得到）。"""
    detour = line((0.0, 0.0), (100.0, 0.0), (100.0, 40.0), (200.0, 40.0), (200.0, 0.0), (300.0, 0.0))
    assert hausdorff_m(detour, line((0.0, 0.0), (300.0, 0.0))) == pytest.approx(40.0, abs=0.6)


def test_hausdorff_sees_a_truncated_result() -> None:
    """結果只走了規劃的前一半（規劃 → 結果的方向才量得到）。"""
    assert hausdorff_m(line((0.0, 0.0), (150.0, 0.0)), line((0.0, 0.0), (300.0, 0.0))) == pytest.approx(150.0, abs=0.6)


def exact_hausdorff(a: list[tuple[float, float]], b: list[tuple[float, float]]) -> float:
    """暴力法（對照用）：每條折線密密取樣（0.05 m），量到另一條折線「線段」的精確距離，取雙向最大。"""
    xa, xb = (np.array(to_meters_local(a, a[0])), np.array(to_meters_local(b, a[0])))

    def directed(p: np.ndarray, q: np.ndarray) -> float:
        samples = [p[0]]
        for u, v in zip(p[:-1], p[1:]):
            k = max(1, math.ceil(math.dist(u, v) / 0.05))
            samples.extend(u + (v - u) * j / k for j in range(1, k + 1))
        s = np.array(samples)
        a0, d = q[:-1], q[1:] - q[:-1]
        rel = s[:, None, :] - a0[None]
        t = np.clip(np.einsum("nij,ij->ni", rel, d) / np.maximum(np.einsum("ij,ij->i", d, d), 1e-12), 0, 1)
        return float(np.hypot(*(rel - d[None] * t[..., None]).transpose(2, 0, 1)).min(axis=1).max())

    return max(directed(xa, xb), directed(xb, xa))


def to_meters_local(points, origin):
    kx = 6_371_008.8 * math.pi / 180 * math.cos(math.radians(origin[1]))
    ky = 6_371_008.8 * math.pi / 180
    return [((lon - origin[0]) * kx, (lat - origin[1]) * ky) for lon, lat in points]


def wiggle(n: int, step: float, dx: float = 0.0, dy: float = 0.0, seed: int = 0) -> list[tuple[float, float]]:
    rng = np.random.default_rng(seed)
    return line(*((k * step * 0.7 + dx + rng.uniform(-0.3, 0.3), 300 * math.sin(k * step / 500) + dy) for k in range(n)))


@pytest.mark.parametrize("seed", range(6))
def test_hausdorff_never_underestimates_and_overshoots_by_at_most_one_step(seed: int) -> None:
    """上界的關係：精確值 ≤ 結果 ≤ 精確值 + 取樣間距（兩條各多半個間距）。"""
    rng = np.random.default_rng(seed)
    a = wiggle(int(rng.integers(3, 12)), float(rng.uniform(3, 30)), seed=seed)
    b = wiggle(int(rng.integers(3, 12)), float(rng.uniform(3, 30)), dx=float(rng.uniform(-8, 8)),
               dy=float(rng.uniform(-8, 8)), seed=seed + 100)
    exact = exact_hausdorff(a, b)
    measured = hausdorff_m(a, b)
    assert exact - 1e-6 <= measured <= exact + 1.0 + 1e-6


def test_hausdorff_of_long_lines_with_tens_of_thousands_of_samples_is_fast() -> None:
    """20 km、各 2000 個頂點（每條取樣兩萬多點）：寬鬆的時間上限（舊的點到線段暴力法要好幾秒）。"""
    a, b = wiggle(2000, 14.3), wiggle(2000, 14.3, dx=1.0, dy=2.0, seed=1)
    started = time.perf_counter()
    measured = hausdorff_m(a, b)
    elapsed = time.perf_counter() - started
    assert measured == pytest.approx(math.hypot(1.0, 2.0), abs=1.5)
    assert elapsed < 2.0


def test_hausdorff_checks_the_deadline_between_batches() -> None:
    a, b = wiggle(200, 14.3), wiggle(200, 14.3, dy=2.0, seed=1)
    with pytest.raises(MatchTimeout):
        hausdorff_m(a, b, deadline=Deadline(-1))


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
