import pytest

from engine.matching.score import Score, score

LAT = 25.05
# 經度 0.01° ≈ 1008 m（緯度 25.05）
SQUARE = [(121.50, LAT), (121.51, LAT), (121.51, LAT + 0.009), (121.50, LAT + 0.009), (121.50, LAT)]


def shifted(points, d_lon, d_lat):
    return [(lon + d_lon, lat + d_lat) for lon, lat in points]


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
