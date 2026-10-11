"""可騎道路空間索引的讀取與查詢（不經 osmium：直接用 write_index 寫隨機路段）。"""

import json
import math
from collections.abc import Callable
from pathlib import Path

import numpy as np
import pytest

from engine.data.rideable import MAX_SEGMENT_M, taiwan_grid, write_index
from engine.routing.geo import EARTH_RADIUS_M, haversine_m
from engine.search.roads import (
    RIDEABLE_META,
    RIDEABLE_OFFSETS,
    RIDEABLE_SEGMENTS,
    RoadDataMissing,
    RoadIndex,
    has_road_data,
)

GRID = taiwan_grid()


def random_segments(rng: np.random.Generator, n: int, bbox: tuple[float, float, float, float]) -> np.ndarray:
    """中點均勻散在 bbox 裡、方向隨機、長度 0–MAX_SEGMENT_M 的 float32 路段（多數接近上限，最容易跨格子）。"""
    min_lon, min_lat, max_lon, max_lat = bbox
    mid = np.column_stack([rng.uniform(min_lon, max_lon, n), rng.uniform(min_lat, max_lat, n)])
    length = MAX_SEGMENT_M * np.sqrt(rng.uniform(0, 0.995, n))
    angle = rng.uniform(0, 2 * np.pi, n)
    dlat = np.degrees(length / 2 * np.sin(angle) / EARTH_RADIUS_M)
    dlon = np.degrees(length / 2 * np.cos(angle) / EARTH_RADIUS_M) / np.cos(np.radians(mid[:, 1]))
    segments = np.column_stack([mid[:, 0] - dlon, mid[:, 1] - dlat, mid[:, 0] + dlon, mid[:, 1] + dlat]).astype(np.float32)
    assert all(haversine_m(tuple(s[:2]), tuple(s[2:])) <= MAX_SEGMENT_M for s in segments.astype(np.float64))
    return segments


def intersects(segments: np.ndarray, bbox: tuple[float, float, float, float]) -> np.ndarray:
    """暴力法：每個路段與 bbox 是否相交（經緯度平面上的 Liang–Barsky 裁切）。"""
    min_lon, min_lat, max_lon, max_lat = bbox
    x0, y0 = segments[:, 0].astype(np.float64), segments[:, 1].astype(np.float64)
    dx, dy = segments[:, 2] - x0, segments[:, 3] - y0
    lo, hi = np.zeros(len(segments)), np.ones(len(segments))
    ok = np.ones(len(segments), dtype=bool)
    for p, q in ((-dx, x0 - min_lon), (dx, max_lon - x0), (-dy, y0 - min_lat), (dy, max_lat - y0)):
        parallel = p == 0
        ok &= ~(parallel & (q < 0))
        with np.errstate(divide="ignore", invalid="ignore"):
            t = np.where(parallel, 0.0, q / np.where(parallel, 1.0, p))
        lo = np.where(~parallel & (p < 0), np.maximum(lo, t), lo)
        hi = np.where(~parallel & (p > 0), np.minimum(hi, t), hi)
    return ok & (lo <= hi)


def rows(array: np.ndarray) -> set[tuple[float, ...]]:
    return {tuple(row) for row in array.astype(np.float64)}


def written(tmp_path: Path, segments: np.ndarray) -> RoadIndex:
    write_index(segments, tmp_path)
    return RoadIndex.load(tmp_path)


def test_query_returns_every_intersecting_segment_on_random_bboxes(tmp_path: Path) -> None:
    rng = np.random.default_rng(20261011)
    area = (121.475, 25.015, 121.545, 25.075)  # 跨好幾個 0.01° 的格子
    segments = random_segments(rng, 20_000, area)
    index = written(tmp_path, segments)
    assert len(index) == len(segments)
    for _ in range(200):
        size = rng.choice([0.0, 1e-4, 1e-3, 0.01, 0.03])  # 0 是退化成一個點的範圍
        lon, lat = rng.uniform(area[0], area[2]), rng.uniform(area[1], area[3])
        bbox = (lon, lat, lon + rng.uniform(0, size), lat + rng.uniform(0, size))
        result = index.segments(*bbox)
        assert result.dtype == np.float64 and result.shape[1] == 4
        assert len(rows(result)) == len(result)  # 每個路段只出現一次
        assert rows(segments[intersects(segments, bbox)]) <= rows(result)
        assert rows(result) <= rows(segments)


def test_query_finds_segment_whose_midpoint_is_in_a_neighbouring_bucket(tmp_path: Path) -> None:
    """路段從格子 A 跨進格子 B，中點在 A；只碰到 B 那一小角的查詢也要找到它。"""
    lat = 25.055
    boundary = GRID.lon0 + 340 * GRID.bucket_deg  # 經度 121.5 的格線
    half = math.degrees(99.0 / EARTH_RADIUS_M) / math.cos(math.radians(lat))  # 約 99 m
    segment = np.array([[boundary - 1.5 * half, lat, boundary + 0.5 * half, lat]], dtype=np.float32)
    assert haversine_m(tuple(segment[0, :2].astype(float)), tuple(segment[0, 2:].astype(float))) <= MAX_SEGMENT_M
    index = written(tmp_path, segment)
    tip = float(segment[0, 2])
    bbox = (tip - 1e-6, lat - 1e-6, tip + 1e-3, lat + 1e-6)  # 整個在格線東側的格子裡
    mid = (float(segment[0, 0]) + tip) / 2
    assert GRID.col(bbox[0]) == GRID.col(bbox[2]) == GRID.col(mid) + 1  # 前提：查詢範圍只在鄰格裡
    assert rows(index.segments(*bbox)) == rows(segment)


def test_query_also_crosses_bucket_rows(tmp_path: Path) -> None:
    lon = 121.505
    boundary = GRID.lat0 + 325 * GRID.bucket_deg  # 緯度 25.05 的格線
    half = math.degrees(99.0 / EARTH_RADIUS_M)
    segment = np.array([[lon, boundary + 1.5 * half, lon, boundary - 0.5 * half]], dtype=np.float32)
    index = written(tmp_path, segment)
    tip = float(segment[0, 3])
    bbox = (lon - 1e-4, tip - 1e-3, lon + 1e-4, tip + 1e-6)
    assert GRID.row(bbox[1]) == GRID.row(bbox[3]) == GRID.row((float(segment[0, 1]) + tip) / 2) - 1
    assert rows(index.segments(*bbox)) == rows(segment)


def test_query_far_away_or_outside_the_grid_is_empty(tmp_path: Path) -> None:
    index = written(tmp_path, random_segments(np.random.default_rng(1), 100, (121.50, 25.05, 121.51, 25.06)))
    for bbox in [(120.2, 22.6, 120.3, 22.7), (100.0, 10.0, 101.0, 11.0), (130.0, 30.0, 131.0, 31.0)]:
        result = index.segments(*bbox)
        assert result.shape == (0, 4) and result.dtype == np.float64


def test_segments_outside_the_grid_are_dropped_and_rest_sorted_by_bucket(tmp_path: Path) -> None:
    inside = random_segments(np.random.default_rng(2), 500, (121.40, 25.00, 121.60, 25.10))
    outside = np.array([[117.0, 25.0, 117.001, 25.0], [121.5, 27.0, 121.5, 27.001]], dtype=np.float32)
    assert write_index(np.concatenate([outside, inside]), tmp_path) == len(inside)
    segments = np.load(tmp_path / RIDEABLE_SEGMENTS)
    offsets = np.load(tmp_path / RIDEABLE_OFFSETS)
    assert rows(segments) == rows(inside)
    mid_lon = (segments[:, 0].astype(np.float64) + segments[:, 2]) / 2
    mid_lat = (segments[:, 1].astype(np.float64) + segments[:, 3]) / 2
    bucket = GRID.row(mid_lat) * GRID.nx + GRID.col(mid_lon)
    assert np.array_equal(bucket, np.repeat(np.arange(GRID.nx * GRID.ny), np.diff(offsets)))
    meta = json.loads((tmp_path / RIDEABLE_META).read_text())
    assert meta == {"version": 1, "bucket_deg": 0.01, "lon0": 118.1, "lat0": 21.8, "nx": 400, "ny": 460,
                    "segments": len(inside), "max_segment_m": 200.0}
    assert not list(tmp_path.glob("*.tmp"))


def test_empty_index_loads_and_answers_empty(tmp_path: Path) -> None:
    index = written(tmp_path, np.empty((0, 4), dtype=np.float32))
    assert len(index) == 0
    assert index.segments(121.0, 25.0, 121.5, 25.5).shape == (0, 4)


def test_has_road_data_needs_all_three_files(tmp_path: Path) -> None:
    assert not has_road_data(tmp_path)
    write_index(np.array([[121.5, 25.05, 121.501, 25.05]], dtype=np.float32), tmp_path)
    assert has_road_data(tmp_path)
    (tmp_path / RIDEABLE_OFFSETS).unlink()
    assert not has_road_data(tmp_path)


@pytest.mark.parametrize("name", [RIDEABLE_META, RIDEABLE_SEGMENTS, RIDEABLE_OFFSETS])
def test_missing_file_raises_road_data_missing(tmp_path: Path, name: str) -> None:
    write_index(np.array([[121.5, 25.05, 121.501, 25.05]], dtype=np.float32), tmp_path)
    RoadIndex.load(tmp_path)
    (tmp_path / name).unlink()
    with pytest.raises(RoadDataMissing):
        RoadIndex.load(tmp_path)


def _truncate(path: Path) -> None:
    data = path.read_bytes()
    path.write_bytes(data[: len(data) - 8])


def _meta(**changes) -> Callable[[Path], None]:
    def change(build_dir: Path) -> None:
        path = build_dir / RIDEABLE_META
        path.write_text(json.dumps({**json.loads(path.read_text()), **changes}))
    return change


def _drop_key(build_dir: Path) -> None:
    path = build_dir / RIDEABLE_META
    meta = json.loads(path.read_text())
    del meta["nx"]
    path.write_text(json.dumps(meta))


CORRUPTIONS = {
    "meta-not-json": lambda d: (d / RIDEABLE_META).write_text("{"),
    "meta-missing-key": _drop_key,
    "wrong-version": _meta(version=2),
    "segment-count": _meta(segments=4),
    "grid-size": _meta(nx=401),
    "bad-bucket": _meta(bucket_deg=0),
    "segments-truncated": lambda d: _truncate(d / RIDEABLE_SEGMENTS),
    "offsets-truncated": lambda d: _truncate(d / RIDEABLE_OFFSETS),
    "segments-garbage": lambda d: (d / RIDEABLE_SEGMENTS).write_bytes(b"garbage"),
    "segments-float64": lambda d: np.save(d / RIDEABLE_SEGMENTS, np.load(d / RIDEABLE_SEGMENTS).astype(np.float64)),
    "segments-shape": lambda d: np.save(d / RIDEABLE_SEGMENTS, np.load(d / RIDEABLE_SEGMENTS).reshape(-1, 2)),
    "offsets-total": lambda d: np.save(d / RIDEABLE_OFFSETS, np.load(d / RIDEABLE_OFFSETS) * 2),
    "offsets-decreasing": lambda d: np.save(d / RIDEABLE_OFFSETS, _decreasing(np.load(d / RIDEABLE_OFFSETS))),
}


def _decreasing(offsets: np.ndarray) -> np.ndarray:
    nonzero = int(np.argmax(offsets > 0))
    out = offsets.copy()
    out[nonzero] = out[-1] + 1  # 中間比最後還大：總數對得上，但不是遞增
    return out


@pytest.mark.parametrize("corrupt", CORRUPTIONS.values(), ids=CORRUPTIONS.keys())
def test_corrupt_files_raise_road_data_missing(tmp_path: Path, corrupt) -> None:
    write_index(random_segments(np.random.default_rng(3), 3, (121.50, 25.05, 121.51, 25.06)), tmp_path)
    RoadIndex.load(tmp_path)
    corrupt(tmp_path)
    with pytest.raises(RoadDataMissing):
        RoadIndex.load(tmp_path)
