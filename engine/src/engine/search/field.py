"""粗篩用的道路距離場（spec §4.6 第 2 步）：把範圍內可騎的道路畫成網格，用 EDT 算每一格到最近道路的距離與那條路的方向。

座標一律是以搜尋圓心為原點的局部平面（東, 北，公尺；geometry.to_local）。網格第 row 列、第 col 欄的中心在
(east, north) = (x0 + col × cell, y0 + row × cell)。方向是「軸向」的（0–π，道路沒有正反）。
"""

import math
from dataclasses import dataclass

import numpy as np
from scipy import ndimage

MAX_GRID_CELLS = 3000  # 每邊最多幾格：EDT 的記憶體（距離＋索引）約 24 bytes × 格數，3000² 約 216 MB
MIN_CELL_M = 10.0  # spec §4.6：10 m 網格


@dataclass(frozen=True)
class RoadField:
    x0: float  # 第 0 欄中心的東座標
    y0: float  # 第 0 列中心的北座標
    cell: float
    width: int  # 欄數
    height: int  # 列數
    distance: np.ndarray  # (height, width) float32，到最近道路的距離（公尺）
    cos2: np.ndarray  # (height, width) float32，最近道路方向 φ 的 cos 2φ
    sin2: np.ndarray  # sin 2φ
    roads: int  # 畫進網格的路段數

    def flat_index(self, east: np.ndarray, north: np.ndarray) -> np.ndarray:
        """最近格的一維索引；超出網格的夾到邊上（呼叫端要讓網格夠大，邊上的距離本來就是截斷值）。"""
        col = np.clip(np.rint((east - self.x0) / self.cell), 0, self.width - 1).astype(np.int64)
        row = np.clip(np.rint((north - self.y0) / self.cell), 0, self.height - 1).astype(np.int64)
        return row * self.width + col

    def bilinear_distance(self, east: np.ndarray, north: np.ndarray) -> np.ndarray:
        """雙線性內插的距離：局部最佳化需要連續的成本，最近格會讓小步移動完全沒有變化。"""
        fx = np.clip((east - self.x0) / self.cell, 0, self.width - 1.000001)
        fy = np.clip((north - self.y0) / self.cell, 0, self.height - 1.000001)
        c0, r0 = np.floor(fx).astype(np.int64), np.floor(fy).astype(np.int64)
        tx, ty = fx - c0, fy - r0
        d = self.distance
        top = d[r0, c0] * (1 - tx) + d[r0, c0 + 1] * tx
        bottom = d[r0 + 1, c0] * (1 - tx) + d[r0 + 1, c0 + 1] * tx
        return top * (1 - ty) + bottom * ty


def grid_cell(half_extent_m: float) -> float:
    """範圍半寬 → 格子大小：至少 10 m，範圍太大時放大格子，讓每邊不超過 MAX_GRID_CELLS 格。"""
    return max(MIN_CELL_M, 2 * half_extent_m / (MAX_GRID_CELLS - 1))


def build_field(segments_local: np.ndarray, half_extent_m: float, cell: float | None = None) -> RoadField:
    """segments_local：(M, 4) 的 (東1, 北1, 東2, 北2)。網格涵蓋 [-half_extent_m, half_extent_m]²。
    沒有任何道路時，每一格都是「遠」（距離 = 半寬的兩倍），方向任意。"""
    cell = grid_cell(half_extent_m) if cell is None else cell
    n = int(math.ceil(2 * half_extent_m / cell)) + 1
    x0 = y0 = -half_extent_m
    road = np.zeros((n, n), dtype=bool)
    angle = np.zeros((n, n), dtype=np.float32)
    count = 0
    if len(segments_local):
        a, b = segments_local[:, :2], segments_local[:, 2:]
        lengths = np.hypot(*(b - a).T)
        # 每段至少取兩端；取樣間隔半格，畫出來的線是連續的
        steps = np.maximum(1, np.ceil(lengths / (cell / 2))).astype(np.int64)
        seg = np.repeat(np.arange(len(a)), steps + 1)
        t = _fractions(steps)
        points = a[seg] + (b - a)[seg] * t[:, None]
        cols = np.rint((points[:, 0] - x0) / cell).astype(np.int64)
        rows = np.rint((points[:, 1] - y0) / cell).astype(np.int64)
        inside = (cols >= 0) & (cols < n) & (rows >= 0) & (rows < n)
        direction = np.mod(np.arctan2((b - a)[:, 1], (b - a)[:, 0]), np.pi).astype(np.float32)
        road[rows[inside], cols[inside]] = True
        angle[rows[inside], cols[inside]] = direction[seg[inside]]
        count = int(np.unique(seg[inside]).size)
    if not road.any():
        far = np.full((n, n), 4 * half_extent_m, dtype=np.float32)
        ones = np.ones((n, n), dtype=np.float32)
        return RoadField(x0, y0, cell, n, n, far, ones, np.zeros((n, n), dtype=np.float32), 0)
    distance, (near_rows, near_cols) = ndimage.distance_transform_edt(~road, return_indices=True)
    phi = angle[near_rows, near_cols]
    del near_rows, near_cols
    return RoadField(
        x0, y0, cell, n, n,
        (distance * cell).astype(np.float32),
        np.cos(2 * phi).astype(np.float32),
        np.sin(2 * phi).astype(np.float32),
        count,
    )


def _fractions(steps: np.ndarray) -> np.ndarray:
    """每段 0, 1/k, …, 1 的 t 值（向量化版的 linspace 串接）。"""
    total = steps + 1
    starts = np.repeat(np.cumsum(total) - total, total)
    index = np.arange(int(total.sum())) - starts
    return index / np.repeat(steps, total)
