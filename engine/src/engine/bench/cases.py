"""基準測試案例：幾個代表性圖形 × 台灣幾種街道型態的地點。

案例命名（CASES 的順序固定，名稱即 baseline 的鍵）：
- `<圖形>@<地點>`：不旋轉，每個圖形 × 每個地點各一個。
- `<圖形>@<地點>@r<角度>`：旋轉變體，每個圖形只在 ROTATED_LOCATION 放一個，角度為 ROTATION_DEG。
"""

import math
from dataclasses import dataclass

from engine.matching.service import StrokeInput

type Point = tuple[float, float]


@dataclass(frozen=True)
class Shape:
    name: str
    strokes: list[tuple[list[Point], bool]]  # (正規化座標, 是否封閉)；x 向東、y 向北，範圍 0–1


@dataclass(frozen=True)
class Case:
    name: str
    shape: Shape
    center: Point
    size_m: float
    rotation_deg: float


def _heart(n: int = 48) -> list[Point]:
    raw = [
        (16 * math.sin(t) ** 3, 13 * math.cos(t) - 5 * math.cos(2 * t) - 2 * math.cos(3 * t) - math.cos(4 * t))
        for t in (2 * math.pi * i / n for i in range(n))
    ]
    xs, ys = [p[0] for p in raw], [p[1] for p in raw]
    return [((x - min(xs)) / (max(xs) - min(xs)), (y - min(ys)) / (max(ys) - min(ys))) for x, y in raw]


def _star() -> list[Point]:
    pts = []
    for i in range(10):
        r = 0.5 if i % 2 == 0 else 0.2
        a = math.pi / 2 + i * math.pi / 5
        pts.append((0.5 + r * math.cos(a), 0.5 + r * math.sin(a)))
    return pts


def _hi() -> list[tuple[list[Point], bool]]:
    """H（左豎、右豎、橫槓三筆）＋ I（一筆）：四個開放筆畫，用來量多筆畫的串接。"""
    return [
        ([(0.0, 1.0), (0.0, 0.0)], False),
        ([(0.6, 1.0), (0.6, 0.0)], False),
        ([(0.0, 0.5), (0.6, 0.5)], False),
        ([(1.0, 1.0), (1.0, 0.0)], False),
    ]


SHAPES: list[Shape] = [
    Shape("heart", [(_heart(), True)]),
    Shape("star", [(_star(), True)]),
    Shape("letter_l", [([(0.2, 1.0), (0.2, 0.0), (0.8, 0.0)], False)]),
    Shape("square", [([(0, 0), (1, 0), (1, 1), (0, 1)], True)]),
    Shape("hi", _hi()),
]

LOCATIONS: dict[str, Point] = {
    "taipei_daan": (121.5435, 25.0330),        # 棋盤狀街廓、單行道多
    "taichung_west": (120.6620, 24.1470),      # 放射＋棋盤
    "kaohsiung_lingya": (120.3014, 22.6273),   # 寬路、街廓大
    "taipei_riverside": (121.5130, 25.0680),   # 河濱與快速道路附近
}

CASE_SIZE_M = 2000.0
ROTATED_LOCATION = "taipei_daan"
ROTATION_DEG = 30.0

CASES: list[Case] = [
    Case(f"{shape.name}@{place_name}", shape, center, CASE_SIZE_M, 0.0)
    for shape in SHAPES
    for place_name, center in LOCATIONS.items()
] + [
    Case(f"{shape.name}@{ROTATED_LOCATION}@r{ROTATION_DEG:g}", shape, LOCATIONS[ROTATED_LOCATION], CASE_SIZE_M, ROTATION_DEG)
    for shape in SHAPES
]


def place(shape: Shape, center: Point, size_m: float, rotation_deg: float) -> list[StrokeInput]:
    """把正規化圖形放到地圖上：以中心為原點、邊長 size_m，旋轉 rotation_deg。

    順時針、0 = 上方朝北，與 web/src/lib/shape/transform.ts 一致。
    """
    lon0, lat0 = center
    kx = 111_320.0 * math.cos(math.radians(lat0))
    ky = 110_574.0
    cos_r, sin_r = math.cos(math.radians(rotation_deg)), math.sin(math.radians(rotation_deg))

    def to_lonlat(p: Point) -> Point:
        x, y = (p[0] - 0.5) * size_m, (p[1] - 0.5) * size_m
        x, y = x * cos_r + y * sin_r, -x * sin_r + y * cos_r  # x = 東、y = 北
        return (lon0 + x / kx, lat0 + y / ky)

    return [StrokeInput([to_lonlat(p) for p in points], closed) for points, closed in shape.strokes]
