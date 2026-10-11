"""萃取自行車可騎的路段，寫成自動搜尋粗篩用的空間索引（spec §4.6；格式與讀取見 engine.search.roads）。

讀的是建 tiles 用的同一份修補後 pbf：快速道路、樓梯已標成 bicycle=no，可騎的人行道已標成 bicycle=dismount，
禁行的判斷只有 bike_patch 一處。粗篩只拿這份資料替候選擺放排名，最後的路線一律由 Valhalla 算，
所以 is_rideable 只是 Valhalla 3.9.1 自行車通行規則的近似：多算一條只是排名稍差，不會讓路線走上禁行的路。
"""

import json
import logging
import math
import os
from array import array
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import osmium

from engine.data.bike_patch import BICYCLE_ACCESS, FOOTWAYS, rideable_footway
from engine.matching.service import TAIWAN_BBOX
from engine.routing.geo import EARTH_RADIUS_M
from engine.search.roads import RIDEABLE_META, RIDEABLE_OFFSETS, RIDEABLE_SEGMENTS, RIDEABLE_VERSION, Grid

# Valhalla 3.9.1 預設讓自行車走的 highway（footway／pedestrian 另外看 rideable_footway）
RIDEABLE_HIGHWAYS = frozenset(
    {"trunk", "trunk_link", "primary", "primary_link", "secondary", "secondary_link", "tertiary", "tertiary_link",
     "unclassified", "residential", "living_street", "service", "road", "track", "path", "cycleway"}
)
# private 在 Valhalla 只能當起訖（destination only），圖形不該擺在上面：bicycle=private 雖在 BICYCLE_ACCESS 裡也不算
DENIED_ACCESS = frozenset({"no", "private"})
MAX_SEGMENT_M = 200.0
# 切段用的長度略短於上限：等分是經緯度線性內插，加上 float32 的誤差，仍要保證每段不超過 MAX_SEGMENT_M
SPLIT_M = MAX_SEGMENT_M - 1.0
BUCKET_DEG = 0.01
FLUSH_POINTS = 1 << 20  # 累積這麼多個節點就先轉成路段，避免整個台灣的節點座標同時留在記憶體

logger = logging.getLogger(__name__)


def is_rideable(tags: Mapping[str, str]) -> bool:
    """近似 Valhalla 3.9.1 的自行車通行：highway 決定能不能騎，有 bicycle tag 就由它決定，否則 access／vehicle=no、private 不能騎。"""
    if tags.get("area") == "yes":
        return False
    highway = tags.get("highway")
    if highway in FOOTWAYS:
        if not rideable_footway(tags):
            return False
    elif highway not in RIDEABLE_HIGHWAYS:  # motorway、steps、construction、proposed……
        return False
    bicycle = tags.get("bicycle")
    if bicycle is not None:
        return bicycle in BICYCLE_ACCESS and bicycle not in DENIED_ACCESS
    return tags.get("access") not in DENIED_ACCESS and tags.get("vehicle") not in DENIED_ACCESS


@dataclass(frozen=True)
class RideableStats:
    ways: int  # 可騎的 way 數
    segments: int  # 寫進索引的路段數（切段後、去掉格網外的）
    bytes: int  # 三個檔案的總大小


def taiwan_grid() -> Grid:
    min_lon, min_lat, max_lon, max_lat = TAIWAN_BBOX
    nx = math.ceil(round((max_lon - min_lon) / BUCKET_DEG, 6))
    ny = math.ceil(round((max_lat - min_lat) / BUCKET_DEG, 6))
    return Grid(min_lon, min_lat, BUCKET_DEG, nx, ny)


def _lengths_m(lon1: np.ndarray, lat1: np.ndarray, lon2: np.ndarray, lat2: np.ndarray) -> np.ndarray:
    phi1, phi2 = np.radians(lat1), np.radians(lat2)
    h = np.sin((phi2 - phi1) / 2) ** 2 + np.cos(phi1) * np.cos(phi2) * np.sin(np.radians(lon2 - lon1) / 2) ** 2
    return 2 * EARTH_RADIUS_M * np.arcsin(np.sqrt(np.minimum(h, 1.0)))


def split_segments(pairs: np.ndarray) -> np.ndarray:
    """(M, 4) float64 的節點對 → float32 路段：長於 SPLIT_M 的等分成多段，去掉零長度的（含存成 float32 後才重合的）。"""
    lengths = _lengths_m(*pairs.T)
    pieces = np.maximum(np.ceil(lengths / SPLIT_M), 1).astype(np.int64)
    rows = np.repeat(np.arange(len(pairs)), pieces)
    # 每一段在原本節點對裡的序號 0..pieces-1
    index = np.arange(len(rows)) - np.repeat(np.cumsum(pieces) - pieces, pieces)
    t0 = (index / pieces[rows])[:, None]
    t1 = ((index + 1) / pieces[rows])[:, None]
    start, end = pairs[rows, :2], pairs[rows, 2:]
    delta = end - start
    out = np.hstack([start + delta * t0, start + delta * t1]).astype(np.float32)
    return out[np.any(out[:, :2] != out[:, 2:], axis=1)]


def _flush(lons: array, lats: array) -> np.ndarray:
    """節點座標（way 之間、無效座標都以 NaN 隔開）→ 相鄰兩點的路段；碰到 NaN 的不算。"""
    lon, lat = np.frombuffer(lons, dtype=np.float64), np.frombuffer(lats, dtype=np.float64)
    pairs = np.stack([lon[:-1], lat[:-1], lon[1:], lat[1:]], axis=1)
    return split_segments(pairs[np.isfinite(pairs).all(axis=1)])


def _replace(path: Path, write) -> None:
    """先寫暫存檔再改名：中途掛掉不會留下看起來完整的半個檔案。"""
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("wb") as f:
        write(f)
    os.replace(tmp, path)


def write_index(segments: np.ndarray, build_dir: Path, grid: Grid | None = None) -> int:
    """float32 (N, 4) 路段 → 依中點所在格子排序後寫成索引，回傳寫入的路段數（中點在格網外的丟掉）。"""
    grid = grid or taiwan_grid()
    mid_lon = (segments[:, 0].astype(np.float64) + segments[:, 2]) / 2
    mid_lat = (segments[:, 1].astype(np.float64) + segments[:, 3]) / 2
    col, row = grid.col(mid_lon), grid.row(mid_lat)
    inside = (col >= 0) & (col < grid.nx) & (row >= 0) & (row < grid.ny)
    bucket = (row * grid.nx + col)[inside]
    order = np.argsort(bucket, kind="stable")
    kept = np.ascontiguousarray(segments[inside][order], dtype=np.float32)
    offsets = np.zeros(grid.nx * grid.ny + 1, dtype=np.int64)
    np.cumsum(np.bincount(bucket, minlength=grid.nx * grid.ny), out=offsets[1:])
    meta = {"version": RIDEABLE_VERSION, "bucket_deg": grid.bucket_deg, "lon0": grid.lon0, "lat0": grid.lat0,
            "nx": grid.nx, "ny": grid.ny, "segments": len(kept), "max_segment_m": MAX_SEGMENT_M}
    build_dir.mkdir(parents=True, exist_ok=True)
    _replace(build_dir / RIDEABLE_SEGMENTS, lambda f: np.save(f, kept))
    _replace(build_dir / RIDEABLE_OFFSETS, lambda f: np.save(f, offsets))
    _replace(build_dir / RIDEABLE_META, lambda f: f.write(json.dumps(meta).encode()))  # 最後寫：有它才代表另外兩個寫完了
    return len(kept)


def extract_rideable(pbf: Path, build_dir: Path) -> RideableStats:
    processor = (
        osmium.FileProcessor(str(pbf), osmium.osm.NODE | osmium.osm.WAY)
        .with_locations()
        .with_filter(osmium.filter.EntityFilter(osmium.osm.WAY))  # 節點只餵給座標快取，不進 Python
        .with_filter(osmium.filter.KeyFilter("highway"))
    )
    chunks: list[np.ndarray] = []
    lons, lats = array("d"), array("d")
    ways = 0
    for way in processor:
        if not is_rideable({tag.k: tag.v for tag in way.tags}):
            continue
        ways += 1
        for node in way.nodes:
            location = node.location
            valid = location.valid()  # 節點不在 pbf 裡：座標無效，前後兩段都不算
            lons.append(location.lon if valid else math.nan)
            lats.append(location.lat if valid else math.nan)
        lons.append(math.nan)  # 與下一條 way 斷開
        lats.append(math.nan)
        if len(lons) >= FLUSH_POINTS:
            chunks.append(_flush(lons, lats))
            lons, lats = array("d"), array("d")
    chunks.append(_flush(lons, lats))
    count = write_index(np.concatenate(chunks), build_dir)
    size = sum((build_dir / name).stat().st_size for name in (RIDEABLE_META, RIDEABLE_SEGMENTS, RIDEABLE_OFFSETS))
    stats = RideableStats(ways, count, size)
    logger.info("可騎道路：%d 條 way、%d 個路段、%d bytes", stats.ways, stats.segments, stats.bytes)
    return stats
