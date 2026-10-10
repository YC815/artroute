"""球面距離。座標一律以 (lon, lat) 表示，距離單位為公尺。"""

import math

EARTH_RADIUS_M = 6_371_008.8


def haversine_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    """兩個 (lon, lat) 之間的大圓距離（公尺）。"""
    (lon1, lat1), (lon2, lat2) = a, b
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = phi2 - phi1
    d_lambda = math.radians(lon2 - lon1)
    h = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(h))


def polyline_length_m(points: list[tuple[float, float]]) -> float:
    """折線總長（公尺）；少於 2 個點為 0。"""
    return sum(haversine_m(a, b) for a, b in zip(points, points[1:]))
