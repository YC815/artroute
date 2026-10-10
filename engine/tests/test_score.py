import math

import pytest

from engine.matching import score as score_module
from engine.matching.score import Score, score

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


def test_identical_route_scores_100() -> None:
    assert score([SQUARE], SQUARE) == Score(100, 100, 100)


def test_direction_does_not_matter() -> None:
    assert score([SQUARE], SQUARE[::-1]) == Score(100, 100, 100)


def test_offset_of_quarter_threshold_scores_about_75() -> None:
    # 對角線約 1.42 km，5% 為 71 m；東、北各平移約 17.7 m，每條邊都偏離約 17.7 m → e ≈ 1.25% → 約 75 分
    result = score([SQUARE], shifted(SQUARE, 0.000176, 0.00016))
    assert 70 <= result.coverage <= 80
    assert 70 <= result.cleanliness <= 80


def test_extra_spur_lowers_cleanliness_only() -> None:
    spur = [(121.51, LAT), (121.53, LAT), (121.51, LAT)]
    route = SQUARE[:2] + spur[1:] + SQUARE[2:]
    result = score([SQUARE], route)
    assert result.coverage == 100
    assert result.cleanliness < 60
    assert result.overall == round((result.coverage + result.cleanliness) / 2)


def test_missing_half_lowers_coverage_only() -> None:
    result = score([SQUARE], SQUARE[:3])
    assert result.cleanliness == 100
    assert result.coverage < 60


def test_empty_route_scores_zero() -> None:
    assert score([SQUARE], []) == Score(0, 0, 0)


def test_degenerate_target_raises() -> None:
    with pytest.raises(ValueError):
        score([[(121.5, LAT), (121.5, LAT)]], SQUARE)


def test_vertex_density_does_not_change_score() -> None:
    # 正方形加一條約 300 m 的往返支線；取樣須依弧長而非頂點數，同一幾何不論頂點疏密分數都一樣
    route = SQUARE[:2] + [(121.513, LAT), (121.51, LAT)] + SQUARE[2:]
    sparse = score([SQUARE], route)
    assert 0 < sparse.cleanliness < 100  # 避免落在 0 或 100 的飽和區而測不出差異
    for step in (0.5, 2.0, 10.0):
        dense = score([SQUARE], densify(route, step))
        assert abs(dense.coverage - sparse.coverage) <= 1
        assert abs(dense.cleanliness - sparse.cleanliness) <= 1


def test_mixed_vertex_density_does_not_change_score() -> None:
    # Valhalla 的路線在彎道密、直線疏：只把正方形部分加密、支線維持稀疏
    spur = [(121.51, LAT), (121.513, LAT), (121.51, LAT)]
    route = SQUARE[:2] + spur[1:] + SQUARE[2:]
    mixed = densify(SQUARE[:2], 0.5) + spur[1:] + densify(SQUARE[2:], 0.5)[1:]
    a, b = score([SQUARE], route), score([SQUARE], mixed)
    assert abs(a.coverage - b.coverage) <= 1
    assert abs(a.cleanliness - b.cleanliness) <= 1


def test_many_segments_chunking_matches_unchunked(monkeypatch: pytest.MonkeyPatch) -> None:
    route = shifted(densify(SQUARE, 20.0), 0.0004, 0.0002)  # 約 200 段，分數落在中間值
    expected = score([SQUARE], route)
    assert 0 < expected.coverage < 100
    monkeypatch.setattr(score_module, "_BUDGET", 50)  # 每批只剩 1 個取樣點
    assert score([SQUARE], route) == expected
