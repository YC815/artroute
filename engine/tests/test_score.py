import math

import numpy as np
import pytest

from engine.matching import score as score_module
from engine.matching.score import ZERO_SCORE_MIN_M, ZERO_SCORE_RATIO, Score, score, score_v1

LAT = 25.05
# 經度 0.01° ≈ 1008 m（緯度 25.05）
SQUARE = [(121.50, LAT), (121.51, LAT), (121.51, LAT + 0.009), (121.50, LAT + 0.009), (121.50, LAT)]


def shifted(points, d_lon, d_lat):
    return [(lon + d_lon, lat + d_lat) for lon, lat in points]


def densify(points, step_m):
    """沿每段線性內插，使頂點間距約 step_m（不改變幾何）。"""
    out = [points[0]]
    for (lon_a, lat_a), (lon_b, lat_b) in zip(points, points[1:]):
        length = math.hypot((lon_b - lon_a) * 100_900, (lat_b - lat_a) * 110_574)
        n = max(1, math.ceil(length / step_m))
        out += [(lon_a + (lon_b - lon_a) * k / n, lat_a + (lat_b - lat_a) * k / n) for k in range(1, n + 1)]
    return out


# 兩版共同的行為對 score（v2）與 score_v1 都測；v1 必須維持原本的數字（bench 拿它和 v1 baseline 比）
BOTH = pytest.mark.parametrize("scorer", [score, score_v1], ids=["v2", "v1"])


@BOTH
def test_identical_route_scores_100(scorer) -> None:
    assert scorer([SQUARE], SQUARE) == Score(100, 100, 100)


@BOTH
def test_direction_does_not_matter(scorer) -> None:
    assert scorer([SQUARE], SQUARE[::-1]) == Score(100, 100, 100)


@BOTH
def test_offset_of_quarter_threshold_scores_about_75(scorer) -> None:
    # 對角線約 1.42 km，5% 為 71 m（> 60 m 下限，兩版同一個 Z）；東、北各平移約 17.7 m → e ≈ 1.25% → 約 75 分
    result = scorer([SQUARE], shifted(SQUARE, 0.000176, 0.00016))
    assert 70 <= result.coverage <= 80
    assert 70 <= result.cleanliness <= 80


@BOTH
def test_extra_spur_lowers_cleanliness_only(scorer) -> None:
    spur = [(121.51, LAT), (121.53, LAT), (121.51, LAT)]
    route = SQUARE[:2] + spur[1:] + SQUARE[2:]
    result = scorer([SQUARE], route)
    assert result.coverage == 100
    assert result.cleanliness < 60
    assert result.overall == round((result.coverage + result.cleanliness) / 2)


@BOTH
def test_missing_half_lowers_coverage_only(scorer) -> None:
    result = scorer([SQUARE], SQUARE[:3])
    assert result.cleanliness == 100
    assert result.coverage < 60


@BOTH
def test_empty_route_scores_zero(scorer) -> None:
    assert scorer([SQUARE], []) == Score(0, 0, 0)


@BOTH
def test_degenerate_target_raises(scorer) -> None:
    with pytest.raises(ValueError):
        scorer([[(121.5, LAT), (121.5, LAT)]], SQUARE)


@BOTH
def test_vertex_density_does_not_change_score(scorer) -> None:
    # 正方形加一條約 300 m 的往返支線；取樣須依弧長而非頂點數，同一幾何不論頂點疏密分數都一樣
    route = SQUARE[:2] + [(121.513, LAT), (121.51, LAT)] + SQUARE[2:]
    sparse = scorer([SQUARE], route)
    assert 0 < sparse.cleanliness < 100  # 避免落在 0 或 100 的飽和區而測不出差異
    for step in (0.5, 2.0, 10.0):
        dense = scorer([SQUARE], densify(route, step))
        assert abs(dense.coverage - sparse.coverage) <= 1
        assert abs(dense.cleanliness - sparse.cleanliness) <= 1


@BOTH
def test_mixed_vertex_density_does_not_change_score(scorer) -> None:
    # Valhalla 的路線在彎道密、直線疏：只把正方形部分加密、支線維持稀疏
    spur = [(121.51, LAT), (121.513, LAT), (121.51, LAT)]
    route = SQUARE[:2] + spur[1:] + SQUARE[2:]
    mixed = densify(SQUARE[:2], 0.5) + spur[1:] + densify(SQUARE[2:], 0.5)[1:]
    a, b = scorer([SQUARE], route), scorer([SQUARE], mixed)
    assert abs(a.coverage - b.coverage) <= 1
    assert abs(a.cleanliness - b.cleanliness) <= 1


@BOTH
def test_many_segments_chunking_matches_unchunked(scorer, monkeypatch: pytest.MonkeyPatch) -> None:
    route = shifted(densify(SQUARE, 20.0), 0.0004, 0.0002)  # 約 200 段，分數落在中間值
    expected = scorer([SQUARE], route)
    assert 0 < expected.coverage < 100 and 0 < expected.cleanliness < 100
    monkeypatch.setattr(score_module, "_BUDGET", 50)  # 每批只剩 1 個取樣點
    assert scorer([SQUARE], route) == expected


def test_score_v1_keeps_its_exact_numbers() -> None:
    # 改成 v2 之前用原本的 score() 算出來的數字：bench 拿 score_v1 和現有的 v1 baseline 比，必須一字不差
    spur = SQUARE[:2] + [(121.513, LAT), (121.51, LAT)] + SQUARE[2:]
    assert score_v1([SQUARE], shifted(SQUARE, 0.000176, 0.00016)) == Score(75, 75, 75)
    assert score_v1([SQUARE], spur) == Score(100, 37, 68)
    assert score_v1([SQUARE], shifted(densify(SQUARE, 20.0), 0.0004, 0.0002)) == Score(50, 50, 50)
    assert score_v1([SQUARE], SQUARE[:3]) == Score(0, 100, 50)


# ---- v2：乾淨度量「地圖上看得到的線」（路線的聯集），小圖形的 0 分距離有下限 ----

CENTER = (121.50, LAT)


def local(points_m):
    """以 CENTER 為原點的局部平面（公尺，東、北）→ 經緯度；與 score 的投影同一組常數。
    目標圖形的外框中心要是 CENTER，score 的局部平面才會和這裡一致。"""
    lon0, lat0 = CENTER
    kx, ky = 111_320.0 * math.cos(math.radians(lat0)), 110_574.0
    return [(lon0 + x / kx, lat0 + y / ky) for x, y in points_m]


def square_m(half: float):
    return [(-half, -half), (half, -half), (half, half), (-half, half), (-half, -half)]


# 目標：一條東西向約 1 km 的街（外框中心在 CENTER）；支線從路線起點的北方 145.5 m 垂直騎到街的東端
# 145.5 m 讓 v2 乾淨度（約 65.7）離四捨五入的邊界夠遠：兩條路線總長不同，支線上的取樣相位差 < 0.1 m
STREET = local([(-499.5, 0.0), (499.5, 0.0)])
SPUR_TIP = local([(499.5, 145.5)])


def test_riding_the_same_street_twice_does_not_change_v2_cleanliness() -> None:
    once = SPUR_TIP + STREET[::-1]  # 支線 → 街騎一次
    twice = SPUR_TIP + STREET[::-1] + STREET + STREET[::-1]  # 同一條街多來回一趟（兩個方向都騎）
    v2_once, v2_twice = score([STREET], once), score([STREET], twice)
    assert 0 < v2_once.cleanliness < 100  # 不在飽和區，差異才量得出來
    assert v2_twice.cleanliness == v2_once.cleanliness  # 地圖上看到的線一樣
    assert v2_twice.coverage == v2_once.coverage == 100
    v1_once, v1_twice = score_v1([STREET], once), score_v1([STREET], twice)
    assert v1_twice.cleanliness >= v1_once.cleanliness + 20  # v1：貼著目標多騎的部分把支線稀釋掉


def test_an_off_target_spur_lowers_v2_cleanliness() -> None:
    plain = score([STREET], STREET[::-1])
    spurred = score([STREET], SPUR_TIP + STREET[::-1])
    assert plain == Score(100, 100, 100)
    assert spurred.cleanliness < plain.cleanliness - 10
    assert spurred.coverage == 100


# 偏離目標約 25 m 的正方形再加一條往外的支線：分數落在中間值，方向的影響才不會被飽和區藏起來
OFFSET_ROUTE = local([(-325, -325), (330, -325), (330, -200), (420, -200), (330, -200), (330, 325), (-325, 325), (-325, -325)])
SMALL_SQUARE = local(square_m(350.0))


@pytest.mark.parametrize("scorer", [score, score_v1], ids=["v2", "v1"])
def test_reversing_a_route_changes_no_score(scorer) -> None:
    forward = scorer([SMALL_SQUARE], OFFSET_ROUTE)
    assert 0 < forward.coverage < 100 and 0 < forward.cleanliness < 100
    assert scorer([SMALL_SQUARE], OFFSET_ROUTE[::-1]) == forward


def test_visible_samples_do_not_depend_on_direction() -> None:
    # 每格留下的代表點不能取決於騎的方向（「第一個經過的點」會隨方向改變）
    project = score_module._projector([SMALL_SQUARE])
    line = project(OFFSET_ROUTE)
    forward = score_module._visible(line)
    backward = score_module._visible(line[::-1])
    assert len(forward) == len(backward) > 0
    assert np.allclose(np.unique(np.round(forward, 6), axis=0), np.unique(np.round(backward, 6), axis=0))


def test_visible_samples_keep_one_per_cell() -> None:
    project = score_module._projector([STREET])
    once = score_module._visible(project(STREET))
    twice = score_module._visible(project(STREET + STREET[::-1] + STREET))
    assert len(twice) == len(once) == pytest.approx(999 / score_module.VISIBLE_CELL_M, abs=2)
    cells = np.floor(twice / score_module.VISIBLE_CELL_M)
    assert len(np.unique(cells, axis=0)) == len(cells)


def test_small_shape_uses_the_zero_score_floor() -> None:
    # 邊長 700 m（對角線 990 m，5% 為 49.5 m < 60 m）；路線是往外均勻偏 20 m 的正方形：每個目標取樣點都偏 20 m
    half = 350.0
    route = local(square_m(half + 20.0))
    v1, v2 = score_v1([SMALL_SQUARE], route), score([SMALL_SQUARE], route)
    assert ZERO_SCORE_MIN_M == 60.0
    assert v2.coverage == round(100 * (1 - 20 / ZERO_SCORE_MIN_M)) == 67
    assert v1.coverage == round(100 * (1 - 20 / (ZERO_SCORE_RATIO * 2 * half * math.sqrt(2)))) == 60
    assert v2.cleanliness > v1.cleanliness and v2.overall > v1.overall


def test_large_shape_keeps_the_ratio_zero_score_distance() -> None:
    # 邊長 3 km（對角線 4243 m，5% 為 212 m > 60 m）：下限不生效，同樣偏 20 m 的覆蓋分數兩版相同
    half = 1500.0
    target, route = local(square_m(half)), local(square_m(half + 20.0))
    expected = round(100 * (1 - 20 / (ZERO_SCORE_RATIO * 2 * half * math.sqrt(2))))
    assert score([target], route).coverage == score_v1([target], route).coverage == expected == 91
