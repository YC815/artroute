"""可騎道路的判斷與萃取：合成地圖先跑 patch_pbf，再對修補後的 pbf 萃取（與建圖流程相同）。"""

import math
from pathlib import Path

import numpy as np
import osmium
import pytest

from engine.data.bike_patch import Overrides, patch_pbf
from engine.data.build import build_with_patch
from engine.data.checks import rideable_checks, validate_build
from engine.data.rideable import MAX_SEGMENT_M, RideableStats, extract_rideable, is_rideable, write_index
from engine.routing.geo import haversine_m
from engine.search.roads import RIDEABLE_META, RIDEABLE_OFFSETS, RIDEABLE_SEGMENTS, RoadIndex
from osm_fixture import (
    CLOSED_FOOTWAY_WAY_ID,
    EXPRESSWAY_MAP,
    EXPRESSWAY_WAY_ID,
    NO_BICYCLE_FOOTWAY_WAY_ID,
    PLAIN_TRUNK_WAY_ID,
    RESIDENTIAL,
    SIDEWALK_WAY_ID,
    STEPS_MAP,
    STEPS_WAY_ID,
    TEST_MAP,
    MOTORWAY_WAY_ID,
    CLOSED_FOOTWAY_MAP,
    SIDEWALK_MAP,
    GridWay,
    grid_point,
    write_grid_pbf,
)

NO_OVERRIDES = Overrides(deny=frozenset(), allow=frozenset())


@pytest.mark.parametrize(
    "tags, expected",
    [
        ({"highway": "residential"}, True),
        ({"highway": "trunk", "ref": "台1"}, True),
        ({"highway": "primary"}, True),
        ({"highway": "service"}, True),
        ({"highway": "track"}, True),
        ({"highway": "path"}, True),
        ({"highway": "cycleway"}, True),
        ({"highway": "living_street"}, True),
        ({"highway": "motorway"}, False),
        ({"highway": "motorway_link"}, False),
        ({"highway": "steps"}, False),
        ({"highway": "steps", "bicycle": "yes"}, False),  # 樓梯沒有例外
        ({"highway": "construction"}, False),
        ({"highway": "proposed"}, False),
        ({"highway": "bridleway"}, False),
        ({"railway": "rail"}, False),
        ({}, False),
        ({"highway": "residential", "area": "yes"}, False),
        # 有 bicycle tag 就由它決定
        ({"highway": "trunk", "bicycle": "no"}, False),
        ({"highway": "residential", "bicycle": "private"}, False),
        ({"highway": "residential", "bicycle": "use_sidepath"}, True),
        ({"highway": "residential", "bicycle": "unknown"}, False),
        ({"highway": "residential", "access": "no", "bicycle": "yes"}, True),
        ({"highway": "residential", "access": "private", "bicycle": "designated"}, True),
        # 沒有 bicycle tag：access／vehicle 是 no、private 就不能騎
        ({"highway": "residential", "access": "no"}, False),
        ({"highway": "service", "access": "private"}, False),
        ({"highway": "residential", "vehicle": "no"}, False),
        ({"highway": "residential", "access": "destination"}, True),
        ({"highway": "residential", "access": "yes"}, True),
        # 人行道：只有 Valhalla 會讓自行車走的才算（修補後是 bicycle=dismount）
        ({"highway": "footway"}, False),
        ({"highway": "footway", "footway": "sidewalk"}, False),
        ({"highway": "pedestrian", "bicycle": "no"}, False),
        ({"highway": "footway", "footway": "sidewalk", "bicycle": "yes"}, True),
        ({"highway": "footway", "bicycle": "dismount"}, True),
        ({"highway": "pedestrian", "cycleway": "lane"}, True),
        ({"highway": "footway", "cycleway": "opposite"}, False),
        ({"highway": "pedestrian", "cycleway": "lane", "access": "no"}, False),
        ({"highway": "pedestrian", "area": "yes", "bicycle": "yes"}, False),
    ],
)
def test_is_rideable(tags: dict[str, str], expected: bool) -> None:
    assert is_rideable(tags) is expected


def write_raw_pbf(path: Path, nodes: dict[int, tuple[float, float]], ways: dict[int, list[int]]) -> None:
    """任意座標的節點與住宅街 way；way 可以參照不存在的節點。"""
    writer = osmium.SimpleWriter(str(path))
    try:
        for node_id, location in sorted(nodes.items()):
            writer.add_node(osmium.osm.mutable.Node(id=node_id, location=location, version=1))
        for way_id, refs in sorted(ways.items()):
            writer.add_way(osmium.osm.mutable.Way(id=way_id, nodes=refs, tags=RESIDENTIAL, version=1))
    finally:
        writer.close()


def rows_of(segments: np.ndarray) -> set[tuple[float, ...]]:
    return {tuple(row) for row in segments}


def extract(tmp_path: Path, ways: list[GridWay], patched: bool = True) -> tuple[np.ndarray, RideableStats]:
    """寫合成地圖 → patch_pbf（patched=False 時跳過）→ extract_rideable，回傳寫進索引的全部路段（float64）。"""
    tmp_path.mkdir(exist_ok=True)
    src = tmp_path / "src.osm.pbf"
    write_grid_pbf(src, ways)
    pbf = src
    if patched:
        pbf = tmp_path / "patched.osm.pbf"
        patch_pbf(src, pbf, NO_OVERRIDES)
    out = tmp_path / "build"
    out.mkdir()
    stats = extract_rideable(pbf, out)
    return np.load(out / RIDEABLE_SEGMENTS).astype(np.float64), stats


def on_edge(segments: np.ndarray, a: tuple[float, float], b: tuple[float, float]) -> np.ndarray:
    """兩端都落在 a–b 這條線段上的路段（float32 的誤差內：經度 121 附近約 1e-5 度）。"""
    a_, b_ = np.array(a), np.array(b)
    direction = b_ - a_
    tolerance = 2e-5
    mask = np.ones(len(segments), dtype=bool)
    for point in (segments[:, :2], segments[:, 2:]):
        t = (point - a_) @ direction / (direction @ direction)
        nearest = a_ + np.clip(t, 0, 1)[:, None] * direction
        mask &= np.abs(point - nearest).max(axis=1) < tolerance
    return mask


def way_edges(way: GridWay) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    points = [grid_point(*node) for node in way.nodes]
    return list(zip(points, points[1:]))


def covered(segments: np.ndarray, way: GridWay) -> bool:
    """way 的每一段都有路段落在上面。"""
    return all(on_edge(segments, a, b).any() for a, b in way_edges(way))


def touched(segments: np.ndarray, way: GridWay) -> bool:
    """way 的任何一段上有路段。"""
    return any(on_edge(segments, a, b).any() for a, b in way_edges(way))


def by_id(ways: list[GridWay], way_id: int) -> GridWay:
    return next(w for w in ways if w.id == way_id)


def test_residential_included_and_motorway_excluded(tmp_path: Path) -> None:
    segments, stats = extract(tmp_path, TEST_MAP)
    assert all(covered(segments, w) for w in TEST_MAP if w.id != MOTORWAY_WAY_ID)
    assert not touched(segments, by_id(TEST_MAP, MOTORWAY_WAY_ID))
    assert stats.ways == len(TEST_MAP) - 1
    assert stats.segments == len(segments) > 0


def test_expressway_excluded_after_patch_but_plain_trunk_included(tmp_path: Path) -> None:
    segments, _ = extract(tmp_path / "patched", EXPRESSWAY_MAP)
    assert not touched(segments, by_id(EXPRESSWAY_MAP, EXPRESSWAY_WAY_ID))
    assert covered(segments, by_id(EXPRESSWAY_MAP, PLAIN_TRUNK_WAY_ID))
    # 對照組：不修補時快速道路（highway=trunk）照樣算可騎；排除它的是 patch 標的 bicycle=no
    unpatched, _ = extract(tmp_path / "unpatched", EXPRESSWAY_MAP, patched=False)
    assert covered(unpatched, by_id(EXPRESSWAY_MAP, EXPRESSWAY_WAY_ID))


def test_steps_excluded(tmp_path: Path) -> None:
    segments, _ = extract(tmp_path, STEPS_MAP)
    assert not touched(segments, by_id(STEPS_MAP, STEPS_WAY_ID))
    assert all(covered(segments, w) for w in STEPS_MAP if w.id != STEPS_WAY_ID)


def test_footways_follow_valhalla_bicycle_access(tmp_path: Path) -> None:
    segments, _ = extract(tmp_path / "closed", CLOSED_FOOTWAY_MAP)
    assert not touched(segments, by_id(CLOSED_FOOTWAY_MAP, CLOSED_FOOTWAY_WAY_ID))
    assert not touched(segments, by_id(CLOSED_FOOTWAY_MAP, NO_BICYCLE_FOOTWAY_WAY_ID))
    sidewalk, _ = extract(tmp_path / "sidewalk", SIDEWALK_MAP)
    assert covered(sidewalk, by_id(SIDEWALK_MAP, SIDEWALK_WAY_ID))  # 修補後是 bicycle=dismount


def test_long_segment_is_split_into_pieces_covering_it(tmp_path: Path) -> None:
    a, b = (0, 0), (9, 4)  # 約 2 km 的斜線，只有兩個節點
    segments, _ = extract(tmp_path, [GridWay(1, [a, b], RESIDENTIAL)])
    start, end = np.array(grid_point(*a)), np.array(grid_point(*b))
    total = haversine_m(grid_point(*a), grid_point(*b))
    lengths = [haversine_m(tuple(s[:2]), tuple(s[2:])) for s in segments]
    assert len(segments) >= math.ceil(total / MAX_SEGMENT_M)
    assert max(lengths) <= MAX_SEGMENT_M
    assert sum(lengths) == pytest.approx(total, rel=1e-4)
    # 依離起點的距離排好，前後相接、從起點接到終點
    ordered = segments[np.argsort(np.linalg.norm(segments[:, :2] - start, axis=1))]
    assert np.allclose(ordered[0, :2], start, atol=1e-5) and np.allclose(ordered[-1, 2:], end, atol=1e-5)
    assert np.allclose(ordered[1:, :2], ordered[:-1, 2:], atol=1e-5)
    assert on_edge(segments, grid_point(*a), grid_point(*b)).all()


def test_degenerate_ways_produce_no_segments(tmp_path: Path) -> None:
    segments, stats = extract(tmp_path, [GridWay(1, [(0, 0), (0, 0), (0, 0)], RESIDENTIAL), GridWay(2, [(5, 5)], RESIDENTIAL)])
    assert len(segments) == 0 and stats.ways == 2


def test_edge_slightly_longer_than_the_limit_is_split_in_two(tmp_path: Path) -> None:
    segments, _ = extract(tmp_path, [GridWay(1, [(0, 0), (1, 0)], RESIDENTIAL)])  # 約 201.6 m
    assert len(segments) == 2


def test_short_edges_are_kept_whole(tmp_path: Path) -> None:
    a, b, c = (121.5, 25.05), (121.5015, 25.05), (121.5015, 25.0513)  # 約 151 m、144 m
    src = tmp_path / "src.osm.pbf"
    write_raw_pbf(src, {1: a, 2: b, 3: c}, {10: [1, 2, 3]})
    out = tmp_path / "build"
    out.mkdir()
    extract_rideable(src, out)
    segments = np.load(out / RIDEABLE_SEGMENTS).astype(np.float64)
    assert rows_of(segments) == rows_of(np.array([[*a, *b], [*b, *c]], dtype=np.float32).astype(np.float64))


def test_zero_length_pairs_are_skipped(tmp_path: Path) -> None:
    segments, _ = extract(tmp_path, [GridWay(1, [(0, 0), (0, 0), (0, 1), (0, 1)], RESIDENTIAL)])
    assert len(segments) == 2
    assert np.all(np.any(segments[:, :2] != segments[:, 2:], axis=1))


def test_segments_outside_taiwan_grid_are_dropped(tmp_path: Path) -> None:
    far = GridWay(2, [(2000, 0), (2000, 1)], RESIDENTIAL)  # 經度 125.5，在台灣格網外
    near = GridWay(1, [(0, 0), (0, 1)], RESIDENTIAL)
    segments, stats = extract(tmp_path, [near, far])
    assert covered(segments, near) and not touched(segments, far)
    assert stats.ways == 2 and stats.segments == len(segments) == 2


def test_written_index_is_bucket_sorted_and_loads(tmp_path: Path) -> None:
    segments, stats = extract(tmp_path, TEST_MAP)
    build = tmp_path / "build"
    offsets = np.load(build / RIDEABLE_OFFSETS)
    index = RoadIndex.load(build)
    assert len(index) == stats.segments
    grid_col = np.floor(((segments[:, 0] + segments[:, 2]) / 2 - 118.1) / 0.01).astype(int)
    grid_row = np.floor(((segments[:, 1] + segments[:, 3]) / 2 - 21.8) / 0.01).astype(int)
    assert np.array_equal(grid_row * 400 + grid_col, np.repeat(np.arange(400 * 460), np.diff(offsets)))
    lon0, lat0 = grid_point(0, 0)
    lon1, lat1 = grid_point(6, 3)
    assert len(index.segments(lon0, lat0, lon1, lat1)) == len(segments)
    assert stats.bytes == sum((build / n).stat().st_size for n in (RIDEABLE_META, RIDEABLE_SEGMENTS, RIDEABLE_OFFSETS))


def test_missing_node_locations_break_the_way(tmp_path: Path) -> None:
    """節點不在 pbf 裡：碰到它的兩段不算，同一條 way 的其他段照常。"""
    src = tmp_path / "src.osm.pbf"
    write_raw_pbf(src, {1: grid_point(0, 0), 2: grid_point(0, 1), 3: grid_point(0, 2)}, {10: [1, 999, 2, 3]})
    out = tmp_path / "build"
    out.mkdir()
    stats = extract_rideable(src, out)
    segments = np.load(out / RIDEABLE_SEGMENTS).astype(np.float64)
    assert stats.ways == 1
    assert on_edge(segments, grid_point(0, 1), grid_point(0, 2)).all() and len(segments) == 2
    assert not on_edge(segments, grid_point(0, 0), grid_point(0, 1)).any()


# ---- 建圖驗證：可騎道路資料要讀得進來、至少一個路段 ----


def test_rideable_checks_pass_with_segments(tmp_path: Path) -> None:
    write_index(np.array([[121.5, 25.05, 121.501, 25.05]], dtype=np.float32), tmp_path)
    assert rideable_checks(tmp_path) == []


def test_rideable_checks_fail_when_files_are_missing(tmp_path: Path) -> None:
    failures = rideable_checks(tmp_path)
    assert len(failures) == 1 and "可騎道路資料讀不到" in failures[0]


def test_rideable_checks_fail_when_there_are_no_segments(tmp_path: Path) -> None:
    write_index(np.empty((0, 4), dtype=np.float32), tmp_path)
    failures = rideable_checks(tmp_path)
    assert len(failures) == 1 and "沒有任何路段" in failures[0]


@pytest.mark.valhalla
def test_build_with_patch_writes_rideable_index_and_validate_build_checks_it(tmp_path: Path) -> None:
    src = tmp_path / "src.osm.pbf"
    write_grid_pbf(src, EXPRESSWAY_MAP)
    build_dir = tmp_path / "build"
    build_dir.mkdir()
    build_with_patch(src, build_dir)
    assert not list(build_dir.glob("*.osm.pbf"))  # 修補用的暫存 pbf 照樣刪掉
    segments = RoadIndex.load(build_dir).segments(*grid_point(0, -10), *grid_point(40, 10))
    assert not touched(segments, by_id(EXPRESSWAY_MAP, EXPRESSWAY_WAY_ID))
    assert covered(segments, by_id(EXPRESSWAY_MAP, PLAIN_TRUNK_WAY_ID))
    assert not any("可騎道路" in f for f in validate_build(build_dir))

    (build_dir / RIDEABLE_SEGMENTS).unlink()
    assert any("可騎道路資料讀不到" in f for f in validate_build(build_dir))
