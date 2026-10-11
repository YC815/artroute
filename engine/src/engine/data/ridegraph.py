"""萃取可騎路網圖，給描形規劃器在目標附近找路徑（P2.5 spec §6.1；格式與讀取見 engine.planner.graph）。

與可騎道路索引（engine.data.rideable）讀同一份修補後 pbf、用同一個 is_rideable 篩選：禁行的快速道路、樓梯
本來就不在裡面，禁行的判斷只有 bike_patch 一處。圖只拿來規劃，規劃出的路徑最後由 Valhalla 照著走並檢查，
所以方向旗標拿不準時一律保守（少放行）：只影響規劃品質，不影響安全。轉向限制不建，由 Valhalla 照著走時把關。

兩輪掃描：第一輪數每個 OSM 節點在可騎 way 裡出現幾次，出現兩次以上的（含端點）是圖的節點；第二輪在節點之間切邊。
"""

import json
import logging
import os
from array import array
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

import numpy as np
import osmium

from engine.data.bike_patch import BICYCLE_ACCESS, CYCLEWAY_BOTH_ACCESS, FOOTWAYS
from engine.data.rideable import FLUSH_POINTS, is_rideable, lengths_m, replace_file, taiwan_grid
from engine.planner.graph import (
    BACKWARD,
    FORWARD,
    GRAPH_CELLS,
    GRAPH_COORDS,
    GRAPH_EDGES,
    GRAPH_FILES,
    GRAPH_LENGTHS,
    GRAPH_META,
    GRAPH_NODES,
    GRAPH_VERSION,
)
from engine.search.roads import Grid

BOTH = FORWARD | BACKWARD
# 邊長上限（同 rideable 的路段上限）：更長的邊在中間加節點切開。邊歸在兩端點中點的格子，
# region 往外擴半個上限就不會漏掉穿過範圍的邊；不切的話，一條幾公里沒有岔路的河濱自行車道會讓每次查詢都得擴幾公里
MAX_EDGE_M = 200.0
CUT_M = MAX_EDGE_M / 2  # 長邊沿線每跨過這麼長就切一刀
DENSIFY_M = CUT_M - 1.0  # 長邊上更長的線段先等分插點：切點落在既有的點上，每段 < CUT_M + DENSIFY_M < MAX_EDGE_M
COPY_EDGES = 1 << 16  # 依格子重排形狀時每批搬的邊數
COORDS_SCRATCH = "graph_coords.raw.tmp"  # 第二輪的形狀先依讀入順序寫進這個暫存檔，避免整個台灣的形狀點同時留在記憶體

ROUNDABOUTS = frozenset({"roundabout", "circular"})
# Valhalla 3.9.1 graph lua 的 oneway 表（沒列的值是 nil）與 bike_reverse 表
LUA_ONEWAY = {"no": False, "false": False, "-1": True, "yes": True, "true": True, "1": True,
              "reversible": False, "alternating": False}
BIKE_REVERSE = frozenset({"opposite", "opposite_lane", "opposite_track"})
ONEWAY_FORWARD = frozenset({"yes", "true", "1"})
ONEWAY_REVERSE = frozenset({"-1", "reverse"})
OPPOSITE_KEYS = ("cycleway", "cycleway:left", "cycleway:right", "cycleway:both")

logger = logging.getLogger(__name__)


def _first(tags: Mapping[str, str], *keys: str) -> str | None:
    """lua 的 kv[a] or kv[b]：第一個有值的 tag。"""
    return next((tags[key] for key in keys if key in tags), None)


def _lua_bicycle(value: str) -> bool:
    """lua 的 any_in(bicycle, value) == "true"：整個值或 ; 分隔的任一段在 bicycle 表裡是 true。"""
    return value in BICYCLE_ACCESS or any(part in BICYCLE_ACCESS for part in value.split(";"))


def valhalla_directions(tags: Mapping[str, str]) -> int:
    """照 Valhalla 3.9.1 graph lua 算自行車可騎的方向；只看方向相關的 tag，能不能騎由 is_rideable 判斷（這裡當順向可騎）。

    照抄 lua 的分支，連它的怪處一起：oneway:bicycle=no 只在 oneway 剛好是 yes 時生效；雙向道路的 oneway:bicycle
    除了 no 以外都讓逆向不通；cycleway:both 只認 shared／separated／dedicated 表；圓環不管 oneway=no 一律單向；
    oneway=-1 與 oneway:bicycle=-1 各翻一次。
    """
    oneway, oneway_bike = tags.get("oneway"), tags.get("oneway:bicycle")
    forward = True
    backward = (oneway == "yes" and oneway_bike == "no") or tags.get("bicycle:backward") in ("yes", "no")
    if not backward:
        backward = any(tags.get(key) in BIKE_REVERSE for key in ("cycleway", "cycleway:left", "cycleway:right"))
    if LUA_ONEWAY.get(oneway) or tags.get("junction") in ROUNDABOUTS:
        bike_oneway = LUA_ONEWAY.get(oneway_bike) if backward else None
        if bike_oneway is not None:  # lua：oneway:bicycle=yes 加上逆向放行時變成只能逆向
            forward = not bike_oneway
    elif not backward and oneway_bike in (None, "no"):
        backward = forward
    if tags.get("cycleway:both") in CYCLEWAY_BOTH_ACCESS or (
        tags.get("cycleway:left") in CYCLEWAY_BOTH_ACCESS and tags.get("cycleway:right") in CYCLEWAY_BOTH_ACCESS
    ):
        forward = backward = True
    if (value := _first(tags, "bicycle:forward", "vehicle:forward")) is not None:
        forward = _lua_bicycle(value)
    if (value := _first(tags, "bicycle:backward", "vehicle:backward")) is not None:
        backward = _lua_bicycle(value)
    if oneway == "-1":
        forward, backward = backward, forward
    if oneway_bike == "-1":
        forward, backward = backward, forward
    return (FORWARD if forward else 0) | (BACKWARD if backward else 0)


def tagged_directions(tags: Mapping[str, str]) -> int:
    """tag 字面上的單行規則，不管 Valhalla 怎麼解讀：oneway:bicycle 優先；單行道有 cycleway=opposite* 時逆向也放行。"""
    opposite = any(tags.get(key) in BIKE_REVERSE for key in OPPOSITE_KEYS)
    if tags.get("highway") in FOOTWAYS and "bicycle" not in tags and opposite:
        # bike_patch：lua 只經 bike_reverse 放行逆向。這種人行道 is_rideable 判為不可騎，實際上不會進圖
        return BACKWARD
    oneway_bike = tags.get("oneway:bicycle")
    if oneway_bike == "no":
        return BOTH
    if oneway_bike in ONEWAY_FORWARD:
        return FORWARD
    if oneway_bike in ONEWAY_REVERSE:
        return BACKWARD
    if opposite:
        return BOTH
    oneway = tags.get("oneway")
    if oneway in ONEWAY_FORWARD or (tags.get("junction") in ROUNDABOUTS and oneway != "no"):
        return FORWARD
    if oneway in ONEWAY_REVERSE:
        return BACKWARD
    return BOTH


def direction_flags(tags: Mapping[str, str]) -> int:
    """可以規劃的方向：Valhalla 放行、而且 tag 字面上也合法的方向（取交集：照著走時 Valhalla 不會拒絕，也不規劃逆向）。

    0 表示兩個方向都不行（例如 oneway=-1 加 oneway:bicycle=-1：lua 翻兩次變順向，字面是逆向），這種 way 不進圖。
    """
    return valhalla_directions(tags) & tagged_directions(tags)


@dataclass(frozen=True)
class GraphStats:
    ways: int  # 進圖的 way 數（至少有一段座標有效、方向不是 0 的片段）
    nodes: int
    edges: int
    coords: int  # 形狀點數
    bytes: int  # 六個檔案的總大小


def _pieces(pbf: Path) -> Iterator[tuple[int, list[int], list[float], list[float], int]]:
    """可騎 way 切成座標都有效的片段：(way id, 節點 id, 經度, 緯度, 方向旗標)。

    座標無效（節點不在 pbf 裡）的節點從那裡斷開，前後兩段不相連；連續重複的節點只留一個；不到 2 個節點的片段丟掉。
    兩輪掃描都用這個函式，所以兩輪的切法一定相同。
    """
    processor = (
        osmium.FileProcessor(str(pbf), osmium.osm.NODE | osmium.osm.WAY)
        .with_locations()
        .with_filter(osmium.filter.EntityFilter(osmium.osm.WAY))  # 節點只餵給座標快取，不進 Python
        .with_filter(osmium.filter.KeyFilter("highway"))
    )
    for way in processor:
        tags = {tag.k: tag.v for tag in way.tags}
        if not is_rideable(tags) or not (flags := direction_flags(tags)):
            continue
        refs: list[int] = []
        lons: list[float] = []
        lats: list[float] = []
        for node in way.nodes:
            location = node.location
            if not location.valid():
                if len(refs) >= 2:
                    yield way.id, refs, lons, lats, flags
                refs, lons, lats = [], [], []
            elif not refs or refs[-1] != node.ref:
                refs.append(node.ref)
                lons.append(location.lon)
                lats.append(location.lat)
        if len(refs) >= 2:
            yield way.id, refs, lons, lats, flags


def _junctions(pbf: Path) -> np.ndarray:
    """第一輪：出現兩次以上的 OSM 節點 id（排序過）。片段的端點多記一次，所以一定是節點。

    全台灣約數千萬個 node ref：用 array 收集、原地排序後比相鄰，不建 Python 的 set 或 dict。
    """
    refs = array("q")
    for _, piece, _, _, _ in _pieces(pbf):
        refs.extend(piece)
        refs.append(piece[0])
        refs.append(piece[-1])
    ids = np.frombuffer(refs, dtype=np.int64)
    ids.sort()
    return np.unique(ids[1:][ids[1:] == ids[:-1]])


def concat_ranges(starts: np.ndarray, counts: np.ndarray) -> np.ndarray:
    """把多個 [start, start + count) 串成一個索引陣列。"""
    counts = np.asarray(counts, dtype=np.int64)
    return np.repeat(np.asarray(starts, dtype=np.int64) - np.cumsum(counts) + counts, counts) + np.arange(counts.sum())


def _edges(xy: np.ndarray, piece: np.ndarray, node: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """同一片段內相鄰兩個節點之間是一條邊：回傳每條邊起訖點的索引，與沿線的累積長度（片段之間不累加）。"""
    seg = lengths_m(xy[:-1, 0], xy[:-1, 1], xy[1:, 0], xy[1:, 1])
    seg[piece[1:] != piece[:-1]] = 0.0
    cum = np.concatenate([[0.0], np.cumsum(seg)])
    at = np.flatnonzero(node >= 0)
    same = piece[at[1:]] == piece[at[:-1]]  # 片段的頭尾都是節點：跨片段的那一對不算
    return at[:-1][same], at[1:][same], cum


def _densify(xy: np.ndarray, piece: np.ndarray, node: np.ndarray, cum: np.ndarray,
             starts: np.ndarray, ends: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """長邊（starts[i]..ends[i]）上長於 DENSIFY_M 的線段等分插點（經緯度線性內插，仍在原本的線段上）。"""
    n = len(xy)
    seg = np.diff(cum)
    # 線段 s 在某條長邊裡 ⇔ start ≤ s < end
    inside = np.cumsum(np.bincount(starts, minlength=n) - np.bincount(ends, minlength=n))[:-1] > 0
    reps = np.ones(n, dtype=np.int64)
    reps[:-1] = np.where(inside & (seg > DENSIFY_M), np.ceil(seg / DENSIFY_M), 1)
    src = np.repeat(np.arange(n), reps)
    k = np.arange(len(src)) - np.repeat(np.cumsum(reps) - reps, reps)  # 0 是原本的點，1.. 是插進去的
    nxt = np.minimum(src + 1, n - 1)
    out = xy[src] + (xy[nxt] - xy[src]) * (k / reps[src])[:, None]
    return out, piece[src], np.where(k == 0, node[src], -1)


def _cut_points(cum: np.ndarray, starts: np.ndarray, ends: np.ndarray) -> np.ndarray:
    """長邊內部，沿線每跨過一個 CUT_M 的點：densify 過後每段 < CUT_M + DENSIFY_M。"""
    counts = ends - starts - 1
    inner = concat_ranges(starts + 1, counts)
    base = np.repeat(cum[starts], counts)
    group = np.floor((cum[inner] - base) / CUT_M)
    previous = np.floor((cum[inner - 1] - base) / CUT_M)
    return inner[group != previous]


class _Collector:
    """第二輪：把片段切成邊。節點索引：OSM 節點用它在 junctions 的位置，長邊的切點接在後面。"""

    def __init__(self, junctions: np.ndarray, scratch: BinaryIO) -> None:
        self.junctions = junctions
        self.junction_xy = np.full((len(junctions), 2), np.nan)
        self.extra_xy: list[np.ndarray] = []
        self.extra = 0
        self.scratch = scratch  # 形狀依讀入順序寫在這裡
        self.parts: list[np.ndarray] = []  # 每塊 (m, 4) int64：from、to、旗標、形狀點數
        self.lengths: list[np.ndarray] = []

    def collect(self, pbf: Path) -> int:
        refs, lons, lats, starts, flags = array("q"), array("d"), array("d"), array("q"), array("B")
        ways, last = 0, None
        for way_id, piece_refs, piece_lons, piece_lats, piece_flags in _pieces(pbf):
            if way_id != last:  # 同一條 way 的片段是連續的
                ways, last = ways + 1, way_id
            starts.append(len(refs))
            flags.append(piece_flags)
            refs.extend(piece_refs)
            lons.extend(piece_lons)
            lats.extend(piece_lats)
            if len(refs) >= FLUSH_POINTS:
                self._add(refs, lons, lats, starts, flags)
                refs, lons, lats, starts, flags = array("q"), array("d"), array("d"), array("q"), array("B")
        self._add(refs, lons, lats, starts, flags)
        return ways

    def _new_nodes(self, xy: np.ndarray) -> np.ndarray:
        index = len(self.junctions) + self.extra + np.arange(len(xy))
        self.extra += len(xy)
        self.extra_xy.append(xy.copy())
        return index

    def _add(self, refs: array, lons: array, lats: array, starts: array, flags: array) -> None:
        if not refs:
            return
        ref = np.frombuffer(refs, dtype=np.int64)
        xy = np.column_stack([np.frombuffer(lons), np.frombuffer(lats)])
        first = np.zeros(len(ref), dtype=bool)
        first[np.frombuffer(starts, dtype=np.int64)] = True
        piece = np.cumsum(first) - 1
        position = np.searchsorted(self.junctions, ref)
        is_junction = self.junctions[np.minimum(position, len(self.junctions) - 1)] == ref
        node = np.where(is_junction, position, -1)
        self.junction_xy[position[is_junction]] = xy[is_junction]
        start, end, cum = _edges(xy, piece, node)
        long = cum[end] - cum[start] > MAX_EDGE_M
        if long.any():
            xy, piece, node = _densify(xy, piece, node, cum, start[long], end[long])
            start, end, cum = _edges(xy, piece, node)
            long = cum[end] - cum[start] > MAX_EDGE_M
            cuts = _cut_points(cum, start[long], end[long])
            node[cuts] = self._new_nodes(xy[cuts])
            start, end, cum = _edges(xy, piece, node)
        counts = end - start + 1
        xy[concat_ranges(start, counts)].tofile(self.scratch)
        piece_flags = np.frombuffer(flags, dtype=np.uint8)[piece[start]]
        self.parts.append(np.column_stack([node[start], node[end], piece_flags, counts]).astype(np.int64))
        self.lengths.append(cum[end] - cum[start])

    def write(self, build_dir: Path, grid: Grid, scratch_path: Path) -> tuple[int, int, int]:
        """依格子排序、丟掉格網外的邊與沒用到的節點，寫檔（graph.json 最後寫）。回傳（節點、邊、形狀點）筆數。"""
        parts = np.concatenate(self.parts) if self.parts else np.empty((0, 4), dtype=np.int64)
        lengths = np.concatenate(self.lengths) if self.lengths else np.empty(0)
        node_xy = np.concatenate([self.junction_xy, *self.extra_xy])
        source, target, flags, counts = parts.T
        mid = (node_xy[source] + node_xy[target]) / 2
        col, row = grid.col(mid[:, 0]), grid.row(mid[:, 1])
        kept = np.flatnonzero((col >= 0) & (col < grid.nx) & (row >= 0) & (row < grid.ny))
        bucket = (row * grid.nx + col)[kept]
        sort = np.argsort(bucket, kind="stable")
        order, bucket = kept[sort], bucket[sort]
        cells = np.zeros(grid.nx * grid.ny + 1, dtype=np.int64)
        np.cumsum(np.bincount(bucket, minlength=grid.nx * grid.ny), out=cells[1:])
        used = np.zeros(len(node_xy), dtype=bool)
        used[source[order]] = used[target[order]] = True
        remap = np.cumsum(used) - 1
        nodes = np.ascontiguousarray(node_xy[used])
        new_counts = counts[order]
        offsets = np.cumsum(new_counts) - new_counts
        edges = np.column_stack([remap[source[order]], remap[target[order]], offsets, flags[order]]).astype(np.int64)
        coords = int(new_counts.sum())
        old_offsets = (np.cumsum(counts) - counts)[order]
        meta = {"version": GRAPH_VERSION, "bucket_deg": grid.bucket_deg, "lon0": grid.lon0, "lat0": grid.lat0,
                "nx": grid.nx, "ny": grid.ny, "nodes": len(nodes), "edges": len(edges), "coords": coords,
                "max_edge_m": MAX_EDGE_M}
        replace_file(build_dir / GRAPH_NODES, lambda f: np.save(f, nodes))
        replace_file(build_dir / GRAPH_EDGES, lambda f: np.save(f, edges))
        replace_file(build_dir / GRAPH_LENGTHS, lambda f: np.save(f, lengths[order].astype(np.float32)))
        _write_coords(build_dir / GRAPH_COORDS, scratch_path, int(counts.sum()), old_offsets, new_counts)
        replace_file(build_dir / GRAPH_CELLS, lambda f: np.save(f, cells))
        replace_file(build_dir / GRAPH_META, lambda f: f.write(json.dumps(meta).encode()))  # 最後寫：有它才代表其他檔案寫完了
        return len(nodes), len(edges), coords


def _write_coords(path: Path, scratch_path: Path, scratch_count: int, starts: np.ndarray, counts: np.ndarray) -> None:
    """把暫存檔裡依讀入順序的形狀，照邊的新順序分批搬進 .npy（兩邊都是 mmap，不必整份載入記憶體）。"""
    total = int(counts.sum())
    if total == 0:
        replace_file(path, lambda f: np.save(f, np.empty((0, 2))))
        return
    source = np.memmap(scratch_path, dtype=np.float64, mode="r", shape=(scratch_count, 2))
    tmp = path.with_name(path.name + ".tmp")
    out = np.lib.format.open_memmap(tmp, mode="w+", dtype=np.float64, shape=(total, 2))
    position = 0
    for lo in range(0, len(counts), COPY_EDGES):
        index = concat_ranges(starts[lo:lo + COPY_EDGES], counts[lo:lo + COPY_EDGES])
        out[position:position + len(index)] = source[index]
        position += len(index)
    out.flush()
    del out, source
    os.replace(tmp, path)


def extract_graph(pbf: Path, build_dir: Path) -> GraphStats:
    build_dir.mkdir(parents=True, exist_ok=True)
    (build_dir / GRAPH_META).unlink(missing_ok=True)  # 中途掛掉時不會讓舊的 graph.json 配上新的一半檔案
    junctions = _junctions(pbf)
    scratch = build_dir / COORDS_SCRATCH
    try:
        with scratch.open("wb") as f:
            collector = _Collector(junctions, f)
            ways = collector.collect(pbf)
        nodes, edges, coords = collector.write(build_dir, taiwan_grid(), scratch)
    finally:
        scratch.unlink(missing_ok=True)
    size = sum((build_dir / name).stat().st_size for name in GRAPH_FILES)
    stats = GraphStats(ways, nodes, edges, coords, size)
    logger.info("可騎路網圖：%d 條 way、%d 個節點、%d 條邊、%d 個形狀點、%d bytes",
                stats.ways, stats.nodes, stats.edges, stats.coords, stats.bytes)
    return stats
