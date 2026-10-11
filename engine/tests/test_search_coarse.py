"""粗篩：在合成道路上找得回圖形的位置、大小、角度；起點條件、NMS、範圍都要守住。"""

import math

import numpy as np
import pytest

from engine.matching.deadline import Deadline
from engine.search.coarse import (
    MAX_SIZE_M,
    MIN_SIZE_M,
    Placed,
    angle_levels,
    coarse_search,
    field_half_extent,
    shape_model,
    size_bounds,
    size_levels,
    suppress,
)
from engine.search.field import build_field
from engine.search.geometry import rotate_scale
from engine.search.types import NormStroke, SearchParams

SQUARE = [NormStroke([(-0.5, -0.5), (0.5, -0.5), (0.5, 0.5), (-0.5, 0.5)], closed=True)]
CENTER = (121.5, 25.05)


def ring(cx: float, cy: float, side: float, rotation: float = 0.0) -> np.ndarray:
    """正方形環狀道路（局部平面的 4 條線段），可旋轉。"""
    corners = rotate_scale(np.array([(-0.5, -0.5), (0.5, -0.5), (0.5, 0.5), (-0.5, 0.5)]), side, rotation)
    corners += (cx, cy)
    return np.array([[*corners[k], *corners[(k + 1) % 4]] for k in range(4)])


def clutter(rng: np.random.Generator, count: int, half: float) -> np.ndarray:
    """隨機方向的短路段（100–300 m），模擬和圖形無關的道路。"""
    start = rng.uniform(-half, half, size=(count, 2))
    angle = rng.uniform(0, np.pi, size=count)
    length = rng.uniform(100, 300, size=count)
    end = start + np.column_stack((np.cos(angle), np.sin(angle))) * length[:, None]
    return np.hstack((start, end))


def params(**overrides) -> SearchParams:
    base = dict(
        strokes=SQUARE, center=CENTER, radius_m=1500.0, min_km=4.0, max_km=8.0,
        rotation_min_deg=-15.0, rotation_max_deg=15.0,
    )
    return SearchParams(**{**base, **overrides})


def search(p: SearchParams, segments: np.ndarray):
    shape = shape_model(p.strokes)
    field = build_field(segments, field_half_extent(p, shape))
    return coarse_search(p, shape, field, Deadline(60))


def test_finds_the_ring_among_clutter() -> None:
    rng = np.random.default_rng(7)
    p = params()
    segments = np.vstack([ring(300, -200, 1000, rotation=8), clutter(rng, 400, 3000)])
    best = search(p, segments).candidates[0]
    assert math.hypot(best.east - 300, best.north + 200) < 40
    assert best.size == pytest.approx(1000, rel=0.06)
    assert best.rotation == pytest.approx(8, abs=3)


def test_without_the_ring_the_best_cost_is_worse() -> None:
    rng = np.random.default_rng(7)
    p = params()
    noise = clutter(rng, 400, 3000)
    with_ring = search(p, np.vstack([ring(300, -200, 1000, rotation=8), noise])).candidates[0]
    without = search(p, noise).candidates[0]
    assert with_ring.cost < without.cost - 0.2


def test_rotation_range_is_respected() -> None:
    # 環轉了 40°，但只准 ±15°：結果的角度必須在範圍內（正方形 90° 對稱，-50° 也算不上）
    p = params()
    result = search(p, ring(0, 0, 1000, rotation=40))
    assert result.candidates
    assert all(-15 - 1e-9 <= c.rotation <= 15 + 1e-9 for c in result.candidates)


def test_start_must_be_inside_the_circle() -> None:
    # 環在圓外 4 km：圖形的任何一點都進不了 R=1500 的圈，不能選它
    p = params()
    far = ring(4000, 0, 1000)
    result = search(p, far)
    shape = shape_model(p.strokes)
    for c in result.candidates:
        starts = rotate_scale(shape.start_points, c.size, c.rotation) + (c.east, c.north)
        assert np.hypot(*starts.T).min() <= p.radius_m + 1e-6
    assert all(math.hypot(c.east - 4000, c.north) > 500 for c in result.candidates)


def test_sizes_follow_km_range() -> None:
    shape = shape_model(SQUARE)
    low, high = size_bounds(shape, 4, 8)
    assert low == pytest.approx(4000 / (4 * 1.8))
    assert high == pytest.approx(8000 / (4 * 1.15))
    assert size_bounds(shape, 1, 1)[0] >= MIN_SIZE_M
    assert size_bounds(shape, 300, 300)[1] <= MAX_SIZE_M
    levels = size_levels(low, high)
    assert len(levels) == 6 and levels[0] == pytest.approx(low) and levels[-1] == pytest.approx(high)


def test_gap_between_strokes_shrinks_the_size() -> None:
    apart = [NormStroke([(-0.5, 0), (-0.4, 0)], False), NormStroke([(0.4, 0), (0.5, 0)], False)]
    together = [NormStroke([(-0.5, 0), (-0.4, 0)], False), NormStroke([(-0.4, 0), (-0.3, 0)], False)]
    assert shape_model(apart).gap_unit == pytest.approx(0.8)
    assert shape_model(together).gap_unit == pytest.approx(0.0)
    assert size_bounds(shape_model(apart), 4, 8)[1] < size_bounds(shape_model(together), 4, 8)[1]


def test_angle_levels() -> None:
    assert list(angle_levels(-15, 15)) == [-15, -5, 5, 15]
    assert list(angle_levels(0, 0)) == [0]
    full = angle_levels(-180, 180)
    assert len(full) == 36 and full[0] == -180 and full[-1] == 170


def test_suppress_keeps_distinct_candidates() -> None:
    a = Placed(0, 0, 1000, 0, 0.1)
    near = Placed(50, 0, 1050, 5, 0.2)  # 差不多：被抑制
    rotated = Placed(0, 0, 1000, 40, 0.3)
    bigger = Placed(0, 0, 1300, 0, 0.4)
    moved = Placed(600, 0, 1000, 0, 0.5)
    kept = suppress([moved, near, a, rotated, bigger], keep=10)
    assert kept == [a, rotated, bigger, moved]
    assert suppress([a, rotated, bigger], keep=2) == [a, rotated]


def test_no_roads_returns_far_costs_not_crash() -> None:
    result = search(params(), np.empty((0, 4)))
    assert all(c.cost >= 1.0 - 1e-6 for c in result.candidates)


def test_deadline_is_checked() -> None:
    from engine.matching.deadline import MatchCancelled

    deadline = Deadline(60)
    deadline.cancel()
    p = params()
    shape = shape_model(p.strokes)
    with pytest.raises(MatchCancelled):
        coarse_search(p, shape, build_field(ring(0, 0, 1000), field_half_extent(p, shape)), deadline)
