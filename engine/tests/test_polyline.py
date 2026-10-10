import pytest

from engine.routing.polyline import decode, encode

# Google 官方範例（precision 5）
GOOGLE_EXAMPLE = "_p~iF~ps|U_ulLnnqC_mqNvxq`@"
GOOGLE_POINTS = [(-120.2, 38.5), (-120.95, 40.7), (-126.453, 43.252)]


def test_decode_known_vector_precision5() -> None:
    assert decode(GOOGLE_EXAMPLE, precision=5) == pytest.approx(GOOGLE_POINTS)


def test_encode_known_vector_precision5() -> None:
    assert encode(GOOGLE_POINTS, precision=5) == GOOGLE_EXAMPLE


def test_round_trip_precision6() -> None:
    points = [(121.501234, 25.051234), (121.502, 25.05), (121.499999, 25.049999)]
    assert decode(encode(points)) == pytest.approx(points, abs=1e-6)


def test_decode_empty() -> None:
    assert decode("") == []
