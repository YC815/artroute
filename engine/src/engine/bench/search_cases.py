"""自動搜尋的基準案例（spec §6.3、§7）：完成條件是 R = 5 km 時 30 秒內回傳前 5 名。

地點沿用吸附 bench 的四個（棋盤、放射＋棋盤、寬路大街廓、河濱）；圖形三種：
- default_text：網站預設文字「I ♥ TAIPEI」（15 筆、1 個封閉；由網站的 textShape 產生，web 的測試確保同步），旋轉 ±15°；
- heart：一個封閉筆畫，旋轉不限（圖片的預設），角度最多（36 個），粗篩最重；
- hi：四個開放筆畫（串接與反向），旋轉 ±15°。
公里數範圍取這些圖形在 2 km 大小時的實測路線長度附近（P1.5：預設文字 16 km、heart 約 10 km）。
"""

import json
from dataclasses import dataclass
from pathlib import Path

from engine.bench.cases import LOCATIONS, SHAPES
from engine.search.types import NormStroke

RADIUS_M = 5000.0
DEFAULT_TEXT_FILE = Path(__file__).with_name("default_text.json")


@dataclass(frozen=True)
class SearchCase:
    name: str
    strokes: list[NormStroke]
    center: tuple[float, float]
    radius_m: float
    min_km: float
    max_km: float
    rotation: tuple[float, float]


def _centered(name: str) -> list[NormStroke]:
    """bench 的圖形是 0–1 座標；搜尋要的是外框中心為原點、長邊 = 1（與網站 normalizeStrokes 相同）。"""
    shape = next(s for s in SHAPES if s.name == name)
    points = [p for pts, _ in shape.strokes for p in pts]
    xs, ys = [x for x, _ in points], [y for _, y in points]
    cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    scale = max(max(xs) - min(xs), max(ys) - min(ys))
    return [NormStroke([((x - cx) / scale, (y - cy) / scale) for x, y in pts], closed) for pts, closed in shape.strokes]


def default_text() -> list[NormStroke]:
    raw = json.loads(DEFAULT_TEXT_FILE.read_text())
    return [NormStroke([(x, y) for x, y in s["points"]], s["closed"]) for s in raw["strokes"]]


SHAPE_SETUPS: dict[str, tuple[list[NormStroke], float, float, tuple[float, float]]] = {
    "default_text": (default_text(), 12.0, 25.0, (-15.0, 15.0)),
    "heart": (_centered("heart"), 8.0, 20.0, (-180.0, 180.0)),
    "hi": (_centered("hi"), 8.0, 20.0, (-15.0, 15.0)),
}

SEARCH_CASES: list[SearchCase] = [
    SearchCase(f"{shape}@{place}@R5", strokes, center, RADIUS_M, low, high, rotation)
    for place, center in LOCATIONS.items()
    for shape, (strokes, low, high, rotation) in SHAPE_SETUPS.items()
]
