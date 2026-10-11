"""擺放與局部平面：與 web 的 lib/shape/transform.ts、lib/geo/local-plane.ts 逐式相同（常數與運算順序都一樣）。

搜尋回傳的擺放（Placement）會被網站拿去當 Transform、重新送 /match：兩邊算出的經緯度必須一致，
否則網站重新吸附的路線會和搜尋評過分的那條不同。tests/test_search_geometry.py 與 web 的 transform.test.ts
用同一份固定案例（engine/tests/data/placement_cases.json）釘住兩邊的數字。
"""

import math

import numpy as np

from engine.matching.service import StrokeInput
from engine.routing.router import LonLat
from engine.search.types import NormPoint, NormStroke, Placement

EARTH_RADIUS_M = 6_371_008.8  # 與 web 的 local-plane.ts 相同
RAD = math.pi / 180


def from_local(origin: LonLat, east: float, north: float) -> LonLat:
    meters_per_rad_lon = EARTH_RADIUS_M * math.cos(origin[1] * RAD)
    return (origin[0] + east / meters_per_rad_lon / RAD, origin[1] + north / EARTH_RADIUS_M / RAD)


def to_local(origin: LonLat, point: LonLat) -> tuple[float, float]:
    meters_per_rad_lon = EARTH_RADIUS_M * math.cos(origin[1] * RAD)
    return ((point[0] - origin[0]) * RAD * meters_per_rad_lon, (point[1] - origin[1]) * RAD * EARTH_RADIUS_M)


def rotate_scale(points: np.ndarray, size_m: float, rotation_deg: float) -> np.ndarray:
    """正規化座標 (N, 2) → 以擺放中心為原點的 (東, 北) 公尺。順時針旋轉（與 transformPoint 相同）。"""
    cos, sin = math.cos(rotation_deg * RAD), math.sin(rotation_deg * RAD)
    x, y = points[:, 0], points[:, 1]
    return np.column_stack((size_m * (x * cos + y * sin), size_m * (-x * sin + y * cos)))


def transform_point(point: NormPoint, placement: Placement) -> LonLat:
    cos, sin = math.cos(placement.rotation_deg * RAD), math.sin(placement.rotation_deg * RAD)
    x, y = point
    east = placement.size_m * (x * cos + y * sin)
    north = placement.size_m * (-x * sin + y * cos)
    return from_local(placement.center, east, north)


def place(strokes: list[NormStroke], placement: Placement) -> list[StrokeInput]:
    """把正規化圖形放到地圖上，得到 /match 的輸入（與網站 buildMatchRequest 送出的點相同）。"""
    return [StrokeInput([transform_point(p, placement) for p in s.points], s.closed) for s in strokes]


def lonlat_to_local(origin: LonLat, lonlat: np.ndarray) -> np.ndarray:
    """(N, 2) 經緯度 → 以 origin 為原點的 (東, 北) 公尺（to_local 的向量版）。"""
    meters_per_rad_lon = EARTH_RADIUS_M * math.cos(origin[1] * RAD)
    return np.column_stack(
        ((lonlat[:, 0] - origin[0]) * RAD * meters_per_rad_lon, (lonlat[:, 1] - origin[1]) * RAD * EARTH_RADIUS_M)
    )
