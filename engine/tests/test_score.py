import math

import numpy as np
import pytest

from engine.matching import score as score_module
from engine.matching.score import ZERO_SCORE_MIN_M, ZERO_SCORE_RATIO, Score, rank, score, score_v1, score_v2

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


# v2、v1 共同的行為對 score_v2 與 score_v1 都測；兩版都必須維持原本的數字（bench 拿它們和舊 baseline 比）
BOTH = pytest.mark.parametrize("scorer", [score_v2, score_v1], ids=["v2", "v1"])


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


def test_score_v2_keeps_its_exact_numbers() -> None:
    # 改成 v3 之前用 score()（當時是 v2）算出來的數字：bench 拿 score_v2 和現有的 v2 baseline 比，必須一字不差
    spur = SQUARE[:2] + [(121.513, LAT), (121.51, LAT)] + SQUARE[2:]
    assert score_v2([SQUARE], shifted(SQUARE, 0.000176, 0.00016)) == Score(75, 75, 75)
    assert score_v2([SQUARE], spur) == Score(100, 92, 96)
    assert score_v2([SQUARE], shifted(densify(SQUARE, 20.0), 0.0004, 0.0002)) == Score(50, 50, 50)
    assert score_v2([SQUARE], SQUARE[:3]) == Score(0, 100, 50)


# ---- v2：乾淨度量「地圖上看得到的線」（路線的聯集），小圖形的 0 分距離有下限 ----

CENTER = (121.50, LAT)


def local(points_m, center=CENTER):
    """以 center 為原點的局部平面（公尺，東、北）→ 經緯度；與 score 的投影同一組常數。
    目標圖形的外框中心要是 center，score 的局部平面才會和這裡一致。"""
    lon0, lat0 = center
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
    v2_once, v2_twice = score_v2([STREET], once), score_v2([STREET], twice)
    assert 0 < v2_once.cleanliness < 100  # 不在飽和區，差異才量得出來
    assert v2_twice.cleanliness == v2_once.cleanliness  # 地圖上看到的線一樣
    assert v2_twice.coverage == v2_once.coverage == 100
    v1_once, v1_twice = score_v1([STREET], once), score_v1([STREET], twice)
    assert v1_twice.cleanliness >= v1_once.cleanliness + 20  # v1：貼著目標多騎的部分把支線稀釋掉


def test_an_off_target_spur_lowers_v2_cleanliness() -> None:
    plain = score_v2([STREET], STREET[::-1])
    spurred = score_v2([STREET], SPUR_TIP + STREET[::-1])
    assert plain == Score(100, 100, 100)
    assert spurred.cleanliness < plain.cleanliness - 10
    assert spurred.coverage == 100


# 偏離目標約 25 m 的正方形再加一條往外的支線：分數落在中間值，方向的影響才不會被飽和區藏起來
OFFSET_ROUTE = local([(-325, -325), (330, -325), (330, -200), (420, -200), (330, -200), (330, 325), (-325, 325), (-325, -325)])
SMALL_SQUARE = local(square_m(350.0))


@BOTH
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
    v1, v2 = score_v1([SMALL_SQUARE], route), score_v2([SMALL_SQUARE], route)
    assert ZERO_SCORE_MIN_M == 60.0
    assert v2.coverage == round(100 * (1 - 20 / ZERO_SCORE_MIN_M)) == 67
    assert v1.coverage == round(100 * (1 - 20 / (ZERO_SCORE_RATIO * 2 * half * math.sqrt(2)))) == 60
    assert v2.cleanliness > v1.cleanliness and v2.overall > v1.overall


def test_large_shape_keeps_the_ratio_zero_score_distance() -> None:
    # 邊長 3 km（對角線 4243 m，5% 為 212 m > 60 m）：下限不生效，同樣偏 20 m 的覆蓋分數兩版相同
    half = 1500.0
    target, route = local(square_m(half)), local(square_m(half + 20.0))
    expected = round(100 * (1 - 20 / (ZERO_SCORE_RATIO * 2 * half * math.sqrt(2))))
    assert score_v2([target], route).coverage == score_v1([target], route).coverage == expected == 91


# ---- v3：縮圖相似度（P2.5 spec §4.1），目標外框長邊縮放到 300 px ----
# 目標外框長邊 300 m 時 1 m = 1 px，下面的數字直接是畫布上的像素

SQUARE_300 = local(square_m(150.0))
STREET_300 = local([(-150.0, 0.0), (150.0, 0.0)])
DIAGONAL_300 = local([(-150.0, -150.0), (150.0, 150.0)])


def stairs(step: float):
    """棋盤路網上近似對角線的階梯：東、北交替各走 step，最遠偏離對角線 step / √2。"""
    points, x, y = [(-150.0, -150.0)], -150.0, -150.0
    while x < 150.0:
        x += step
        points.append((x, y))
        y += step
        points.append((x, y))
    return local(points)


def scaled(points_m, k: float):
    return [(x * k, y * k) for x, y in points_m]


def test_v3_constants() -> None:
    assert score_module.SCORE_VERSION == 3
    assert (score_module.CANVAS_PX, score_module.SIMPLIFY_PX) == (300.0, 1.5)
    assert (score_module.FULL_CREDIT_PX, score_module.ZERO_CREDIT_PX, score_module.DIRECTION_ZERO_DEG) == (3.0, 15.0, 45.0)
    assert score_module.WEIGHTS == (0.4, 0.35, 0.25)


@pytest.mark.parametrize(
    "target", [SQUARE_300, SQUARE, STREET_300, DIAGONAL_300], ids=["square", "big", "street", "diagonal"]
)
def test_v3_identical_route_scores_100(target) -> None:
    result = score([target], target)
    assert (result.coverage, result.cleanliness, result.direction, result.overall) == (100, 100, 100, 100)
    # 剛好在目標角上的取樣點離兩側的段一樣近：兩段都算、取比較像的那段，才會是整整 100
    assert result.exact == pytest.approx(100.0, abs=1e-9)


def test_v3_empty_route_scores_zero() -> None:
    assert score([SQUARE_300], []) == Score(0, 0, 0, 0, 0.0)
    assert score([SQUARE_300], SQUARE_300[:1]) == Score(0, 0, 0, 0, 0.0)


def test_v3_degenerate_target_raises() -> None:
    with pytest.raises(ValueError):
        score([[(121.5, LAT), (121.5, LAT)]], SQUARE)


@pytest.mark.parametrize("offset", [(0.0, 2.9), (0.0, -2.9)])
def test_v3_offset_within_full_credit_still_scores_100(offset) -> None:
    dx, dy = offset
    route = local([(-150.0 + dx, dy), (150.0 + dx, dy)])
    assert score([STREET_300], route) == Score(100, 100, 100, 100, 100.0)


def test_v3_diagonal_offset_of_the_square_within_full_credit_scores_100() -> None:
    # 東、北各 2 px：邊上偏 2 px，角上偏 2√2 ≈ 2.83 px，都 ≤ 3 px
    route = local([(x + 2.0, y + 2.0) for x, y in square_m(150.0)])
    result = score([SQUARE_300], route)
    assert (result.coverage, result.cleanliness, result.overall) == (100, 100, 100)
    assert result.direction >= 99  # 角上 2 px 內的取樣點離另一邊比較近（方向差 90°），每個角約 4 點


@pytest.mark.parametrize("offset", [15.5, -16.0, 40.0])
def test_v3_offset_beyond_zero_credit_scores_0(offset: float) -> None:
    route = local([(-150.0, offset), (150.0, offset)])
    assert score([STREET_300], route) == Score(0, 0, 0, 0, 0.0)  # 方向：沒有 15 px 內的點 → 0


def test_v3_partial_offset_is_linear() -> None:
    # 偏 9 px：credit = (15 − 9) / (15 − 3) = 0.5；方向平行仍是 100
    result = score([STREET_300], local([(-150.0, 9.0), (150.0, 9.0)]))
    assert (result.coverage, result.cleanliness, result.direction) == (50, 50, 100)
    assert result.exact == pytest.approx(0.4 * 50 + 0.35 * 50 + 0.25 * 100, abs=0.1)


def test_v3_stairs_score_much_lower_direction_than_a_real_diagonal() -> None:
    diagonal = score([DIAGONAL_300], DIAGONAL_300)
    stair = score([DIAGONAL_300], stairs(10.0))  # 每階 10 px，最遠偏 7.07 px：都在 15 px 內，但方向都差 45°
    assert diagonal.direction == 100
    assert stair.direction <= 5
    assert stair.overall < diagonal.overall - 25


def test_v3_tiny_wiggles_below_simplify_tolerance_are_invisible() -> None:
    # 每階 1 px（偏 0.71 px < 簡化容差 1.5 px）：縮圖上看不出鋸齒，簡化後就是一條斜線
    assert score([DIAGONAL_300], stairs(1.0)) == Score(100, 100, 100, 100, 100.0)


def test_v3_extra_spur_lowers_cleanliness_not_coverage() -> None:
    spurred = local([(-150, -150), (0, -150), (0, -300), (0, -150), (150, -150), (150, 150), (-150, 150), (-150, -150)])
    plain = score([SQUARE_300], SQUARE_300)
    result = score([SQUARE_300], spurred)
    assert result.coverage == 100
    assert result.cleanliness <= plain.cleanliness - 5


def test_v3_missing_half_lowers_coverage_not_cleanliness() -> None:
    result = score([SQUARE_300], SQUARE_300[:3])
    assert result.cleanliness == 100 and result.direction == 100
    assert 40 <= result.coverage <= 60


V3_SPUR_TIP = local([(150.0, 40.3)])


def test_v3_riding_the_same_street_twice_does_not_change_cleanliness() -> None:
    once = V3_SPUR_TIP + STREET_300[::-1]
    twice = V3_SPUR_TIP + STREET_300[::-1] + STREET_300 + STREET_300[::-1]
    a, b = score([STREET_300], once), score([STREET_300], twice)
    assert 0 < a.cleanliness < 100  # 不在飽和區，差異才量得出來
    assert (b.coverage, b.cleanliness, b.direction) == (a.coverage, a.cleanliness, a.direction)
    assert b.exact == pytest.approx(a.exact, abs=0.2)


# 偏離目標的正方形加一條往外的支線（同 v2 的 OFFSET_ROUTE，但偏得少一點）：三項都落在中間值，不變性才測得出來
V3_TARGET_M = square_m(350.0)
V3_ROUTE_M = [(-335.3, -337.1), (341.2, -337.1), (341.2, -200.7), (395.9, -200.7), (341.2, -200.7), (338.4, 336.6),
              (-80.0, 300.2), (-336.8, 334.5), (-335.3, -337.1)]  # fmt: skip


def assert_same_score(a: Score, b: Score) -> None:
    assert (a.coverage, a.cleanliness, a.direction, a.overall) == (b.coverage, b.cleanliness, b.direction, b.overall)
    assert a.exact == pytest.approx(b.exact, abs=0.05)


def test_v3_mid_scores_for_invariance_tests() -> None:
    result = score([local(V3_TARGET_M)], local(V3_ROUTE_M))
    assert 0 < result.coverage < 100 and 0 < result.cleanliness < 100 and 0 < result.direction < 100


def test_v3_reversing_the_route_changes_no_score() -> None:
    target, route = local(V3_TARGET_M), local(V3_ROUTE_M)
    assert_same_score(score([target], route[::-1]), score([target], route))


def test_v3_reversing_random_routes_changes_nothing() -> None:
    # 亂數的目標與路線（會自己交叉、繞過目標的角外側）：頂點上的方向不能因為浮點誤差隨騎的方向改變
    rng = np.random.default_rng(7)
    for i in range(40):
        target = local([tuple(p) for p in rng.uniform(-300, 300, (6, 2))])
        points = rng.uniform(-320, 320, (30, 2))
        if i % 2:
            points = np.vstack([points, points[:1]])  # 繞一圈回到起點
        route = local([tuple(p) for p in points])
        forward, backward = score([target], route), score([target], route[::-1])
        assert backward.exact == pytest.approx(forward.exact, abs=1e-9)
        assert (backward.coverage, backward.cleanliness, backward.direction, backward.overall) == (
            forward.coverage, forward.cleanliness, forward.direction, forward.overall,
        )


@pytest.mark.parametrize("center", [(121.8, LAT), (120.3, 22.7), (121.55, 25.3)])
def test_v3_moving_the_whole_picture_changes_no_score(center) -> None:
    # 在地面上平移（以新的中心重建同一張圖；直接加減經緯度在不同緯度會改變東西向的公尺數，圖形就不一樣了）
    expected = score([local(V3_TARGET_M)], local(V3_ROUTE_M))
    assert_same_score(score([local(V3_TARGET_M, center)], local(V3_ROUTE_M, center)), expected)


@pytest.mark.parametrize("k", [0.25, 3.0, 10.0])
def test_v3_scaling_the_whole_picture_changes_no_score(k: float) -> None:
    expected = score([local(V3_TARGET_M)], local(V3_ROUTE_M))
    assert_same_score(score([local(scaled(V3_TARGET_M, k))], local(scaled(V3_ROUTE_M, k))), expected)


def test_v3_vertex_density_does_not_change_score() -> None:
    target, route = local(V3_TARGET_M), local(V3_ROUTE_M)
    expected = score([target], route)
    for step in (0.5, 3.0):
        assert_same_score(score([target], densify(route, step)), expected)


def test_v3_exact_is_the_unrounded_weighted_sum() -> None:
    target, route = [local(V3_TARGET_M)], local(V3_ROUTE_M)
    result = score(target, route)
    coverage, cleanliness, direction = score_module._v3_components(target, route, score_module.V3_DEFAULT)
    assert result.exact == pytest.approx(0.4 * coverage + 0.35 * cleanliness + 0.25 * direction, abs=1e-9)
    assert (result.coverage, result.cleanliness, result.direction) == (round(coverage), round(cleanliness), round(direction))
    assert result.overall == round(result.exact)
    rounded = 0.4 * result.coverage + 0.35 * result.cleanliness + 0.25 * result.direction
    assert result.exact != pytest.approx(rounded, abs=1e-6)  # 這個例子用取整數後的三項重算會不一樣


def test_v3_many_segments_chunking_matches_unchunked(monkeypatch: pytest.MonkeyPatch) -> None:
    target, route = local(V3_TARGET_M), local(V3_ROUTE_M)
    expected = score([target], route)
    monkeypatch.setattr(score_module, "_BUDGET", 50)  # 每批只剩 1 個取樣點
    assert score([target], route) == expected


def test_v3_direction_of_the_ride_does_not_matter_for_direction() -> None:
    # 目標由西往東，路線由東往西：方向不分正反
    assert score([STREET_300], STREET_300[::-1]).direction == 100


def test_v3_direction_is_zero_without_nearby_route() -> None:
    assert score([STREET_300], local([(-150.0, 100.0), (150.0, 100.0)])).direction == 0


# ---- 搜尋排名：rank = exact − 5 × (θ / 15°)² ----

RANKED = Score(80, 70, 75, 60, 74.6)


@pytest.mark.parametrize(
    ("rotation", "penalty"),
    [(0.0, 0.0), (15.0, 5.0), (-15.0, 5.0), (350.0, 5 * (10 / 15) ** 2), (370.0, 5 * (10 / 15) ** 2), (-345.0, 5.0),
     (30.0, 20.0), (180.0, 5 * 12**2)],
)  # fmt: skip
def test_rank_penalises_rotation(rotation: float, penalty: float) -> None:
    assert rank(RANKED, rotation) == pytest.approx(74.6 - penalty)


def test_rank_uses_the_unrounded_score() -> None:
    assert rank(Score(80, 70, 75, 60, 75.4), 0.0) > rank(Score(80, 70, 75, 60, 74.6), 0.0)
