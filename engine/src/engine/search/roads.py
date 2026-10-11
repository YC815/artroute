"""可騎道路的空間索引（建圖流程產生，見 engine.data.rideable）。

格式：rideable_segments.npy 是 (N, 4) 的 float32 路段 (lon1, lat1, lon2, lat2)，依中點所在的格子排序；
rideable_offsets.npy 是每個格子在路段陣列的起點（CSR，長度 nx*ny + 1，格子編號 row * nx + col）；
rideable.json 記格網與路段數。用 mmap 讀：多個 worker 程序共用同一份記憶體分頁。
"""

import json
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from engine.routing.geo import EARTH_RADIUS_M

RIDEABLE_META = "rideable.json"
RIDEABLE_SEGMENTS = "rideable_segments.npy"
RIDEABLE_OFFSETS = "rideable_offsets.npy"
RIDEABLE_VERSION = 1
# 查詢範圍往外擴的保險：路段長度是以 float64 量的、存成 float32 後端點最多偏 1e-5 度內（台灣經度約 1 m）
MARGIN_FACTOR = 1.01
MARGIN_SLOP_DEG = 1e-5


class RoadDataMissing(Exception):
    pass


def has_road_data(build_dir: Path) -> bool:
    return all((build_dir / name).is_file() for name in (RIDEABLE_META, RIDEABLE_SEGMENTS, RIDEABLE_OFFSETS))


@dataclass(frozen=True)
class Grid:
    lon0: float
    lat0: float
    bucket_deg: float
    nx: int
    ny: int

    def col(self, lon: np.ndarray | float) -> np.ndarray:
        return np.floor((np.asarray(lon, dtype=np.float64) - self.lon0) / self.bucket_deg).astype(np.int64)

    def row(self, lat: np.ndarray | float) -> np.ndarray:
        return np.floor((np.asarray(lat, dtype=np.float64) - self.lat0) / self.bucket_deg).astype(np.int64)


def _meta_grid(meta: dict) -> tuple[Grid, int, float]:
    if meta.get("version") != RIDEABLE_VERSION:
        raise ValueError(f"版本 {meta.get('version')!r} 不是 {RIDEABLE_VERSION}")
    grid = Grid(float(meta["lon0"]), float(meta["lat0"]), float(meta["bucket_deg"]), int(meta["nx"]), int(meta["ny"]))
    count, max_segment_m = int(meta["segments"]), float(meta["max_segment_m"])
    if not (grid.bucket_deg > 0 and grid.nx > 0 and grid.ny > 0 and count >= 0 and max_segment_m > 0):
        raise ValueError(f"格網或路段數不合理：{meta}")
    return grid, count, max_segment_m


class RoadIndex:
    def __init__(self, grid: Grid, segments: np.ndarray, offsets: np.ndarray, max_segment_m: float) -> None:
        self._grid, self._segments, self._offsets, self._max_segment_m = grid, segments, offsets, max_segment_m

    @classmethod
    def load(cls, build_dir: Path) -> "RoadIndex":
        """讀不到、讀壞或三個檔案彼此對不上都丟 RoadDataMissing（呼叫端當成沒有可騎道路資料）。"""
        try:
            grid, count, max_segment_m = _meta_grid(json.loads((build_dir / RIDEABLE_META).read_text()))
            segments = np.load(build_dir / RIDEABLE_SEGMENTS, mmap_mode="r")
            offsets = np.load(build_dir / RIDEABLE_OFFSETS, mmap_mode="r")
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise RoadDataMissing(f"{build_dir}：{error}") from error
        if segments.dtype != np.float32 or segments.shape != (count, 4):
            raise RoadDataMissing(f"{build_dir}：路段陣列是 {segments.dtype} {segments.shape}，應為 float32 ({count}, 4)")
        if offsets.dtype != np.int64 or offsets.shape != (grid.nx * grid.ny + 1,):
            raise RoadDataMissing(f"{build_dir}：格子索引是 {offsets.dtype} {offsets.shape}，應為 int64 ({grid.nx * grid.ny + 1},)")
        if offsets[0] != 0 or offsets[-1] != count or np.any(np.diff(offsets) < 0):
            raise RoadDataMissing(f"{build_dir}：格子索引與路段數 {count} 對不上")
        return cls(grid, segments, offsets, max_segment_m)

    def __len__(self) -> int:
        return len(self._segments)

    def segments(self, min_lon: float, min_lat: float, max_lon: float, max_lat: float) -> np.ndarray:
        """與範圍相交的所有路段（float64，(M, 4)），可能多出附近幾個格子的路段（超集合）。

        路段歸在中點所在的格子，所以範圍先往外擴半個最長路段：相交的路段，中點離範圍不會超過它長度的一半。
        經度方向用範圍（含擴出的部分）裡最小的 cos(lat) 換算，保守。
        """
        grid = self._grid
        half_m = self._max_segment_m / 2 * MARGIN_FACTOR
        margin_lat = math.degrees(half_m / EARTH_RADIUS_M) + MARGIN_SLOP_DEG
        # 相交的路段，另一端可以離範圍一整個路段長：最小 cos(lat) 要算到那裡
        far_lat = min(max(abs(min_lat), abs(max_lat)) + 2 * margin_lat, 89.0)
        margin_lon = margin_lat / math.cos(math.radians(far_lat))
        c0 = max(int(grid.col(min_lon - margin_lon)), 0)
        c1 = min(int(grid.col(max_lon + margin_lon)), grid.nx - 1)
        r0 = max(int(grid.row(min_lat - margin_lat)), 0)
        r1 = min(int(grid.row(max_lat + margin_lat)), grid.ny - 1)
        if c0 > c1 or r0 > r1:
            return np.empty((0, 4), dtype=np.float64)
        # 同一列相鄰格子的路段在陣列裡是連續的：每列一個切片
        starts = self._offsets[np.arange(r0, r1 + 1) * grid.nx + c0]
        ends = self._offsets[np.arange(r0, r1 + 1) * grid.nx + c1 + 1]
        parts = [self._segments[start:end] for start, end in zip(starts, ends) if end > start]
        if not parts:
            return np.empty((0, 4), dtype=np.float64)
        return np.concatenate(parts).astype(np.float64)
