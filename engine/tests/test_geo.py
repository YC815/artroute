import pytest

from engine.routing.geo import haversine_m, polyline_length_m


def test_haversine_one_millidegree_of_latitude() -> None:
    assert haversine_m((121.5, 25.05), (121.5, 25.051)) == pytest.approx(111.2, abs=0.5)


def test_haversine_same_point_is_zero() -> None:
    assert haversine_m((121.5, 25.05), (121.5, 25.05)) == 0.0


def test_haversine_longitude_shrinks_with_latitude() -> None:
    # 0.001° 經度在緯度 25° 約 100.8 m（111.2 * cos 25°）
    assert haversine_m((121.5, 25.0), (121.501, 25.0)) == pytest.approx(100.8, abs=0.5)


def test_polyline_length_is_sum_of_segments() -> None:
    a, b, c = (121.5, 25.05), (121.5, 25.051), (121.501, 25.051)
    assert polyline_length_m([a, b, c]) == pytest.approx(haversine_m(a, b) + haversine_m(b, c))


@pytest.mark.parametrize("points", [[], [(121.5, 25.05)]])
def test_polyline_length_of_fewer_than_two_points_is_zero(points: list[tuple[float, float]]) -> None:
    assert polyline_length_m(points) == 0.0
