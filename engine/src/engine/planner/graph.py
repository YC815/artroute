"""可騎路網圖（建圖流程產生，見 engine.data.ridegraph）：描形規劃器在目標附近找路徑用（P2.5 spec §6.1）。

格式（build 目錄，graph.json 最後寫）：
- graph_nodes.npy：float64 (N, 2) 節點座標 (lon, lat)。節點是可騎 way 的端點、被兩條以上可騎 way 共用（或同一條
  way 經過兩次）的 OSM 節點、座標無效的節點前後的斷點，以及長邊切開處（見 MAX_EDGE_M）。
- graph_edges.npy：int64 (E, 4)，每列 (from, to, 幾何起點, 旗標)。from／to 是節點索引；幾何沿 OSM way 的方向
  （from → to），旗標 bit0（FORWARD）= 順著幾何可騎、bit1（BACKWARD）= 逆著幾何可騎，至少一個。
- graph_lengths.npy：float32 (E,) 邊長（公尺，沿幾何的 haversine 總和）。
- graph_coords.npy：float64 (K, 2) 所有邊的形狀串在一起，每條邊含兩端點（相鄰的邊各存一份共用的節點）。
  第 e 條邊的形狀是 coords[edges[e, 2] : end]，end 是下一條邊的幾何起點；最後一條邊的 end 就是 K（coords 的筆數，
  graph.json 也記一份讓讀取時核對）。不另存 sentinel：幾何起點嚴格遞增（每條邊至少 2 點），K 本身就是 sentinel。
- graph_cells.npy：int64 (nx * ny + 1,) 邊依「兩端點的中點」所在的格子排序（格網同 rideable，格子編號 row * nx + col），
  每格在邊陣列的起點；幾何跟著邊一起重排，所以同一格（以及同一列相鄰的格子）的邊與形狀在檔案裡都是連續的。
用 mmap 讀：多個 worker 程序共用同一份記憶體分頁，region 只碰需要的格子。
"""

import json
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from scipy.sparse import csr_array

from engine.routing.geo import EARTH_RADIUS_M
from engine.search.roads import MARGIN_FACTOR, MARGIN_SLOP_DEG, Grid

GRAPH_META = "graph.json"
GRAPH_NODES = "graph_nodes.npy"
GRAPH_EDGES = "graph_edges.npy"
GRAPH_LENGTHS = "graph_lengths.npy"
GRAPH_COORDS = "graph_coords.npy"
GRAPH_CELLS = "graph_cells.npy"
GRAPH_FILES = (GRAPH_META, GRAPH_NODES, GRAPH_EDGES, GRAPH_LENGTHS, GRAPH_COORDS, GRAPH_CELLS)
GRAPH_VERSION = 1
# graph_edges 的欄位
FROM, TO, OFFSET, FLAGS = range(4)
FORWARD = 1  # 順著幾何（from → to）可騎
BACKWARD = 2  # 逆著幾何（to → from）可騎
# CSR 的權重下限：兩個不同的 OSM 節點座標重合時邊長是 0。scipy 的 csgraph 雖把明確存的 0 當成邊，
# 但任何會 eliminate_zeros 的運算都會把它刪掉，所以權重留一個極小的正數；真正的長度看 Region.lengths
MIN_CSR_WEIGHT_M = 1e-3


class GraphDataMissing(Exception):
    pass


def has_graph_data(build_dir: Path) -> bool:
    return all((build_dir / name).is_file() for name in GRAPH_FILES)


@dataclass(frozen=True)
class GraphMeta:
    grid: Grid
    nodes: int
    edges: int
    coords: int
    max_edge_m: float  # 每條邊的長度上限（建圖時把更長的邊切開）：region 往外擴的依據


def _meta(raw: dict) -> GraphMeta:
    if raw.get("version") != GRAPH_VERSION:
        raise ValueError(f"版本 {raw.get('version')!r} 不是 {GRAPH_VERSION}")
    grid = Grid(float(raw["lon0"]), float(raw["lat0"]), float(raw["bucket_deg"]), int(raw["nx"]), int(raw["ny"]))
    meta = GraphMeta(grid, int(raw["nodes"]), int(raw["edges"]), int(raw["coords"]), float(raw["max_edge_m"]))
    if not (grid.bucket_deg > 0 and grid.nx > 0 and grid.ny > 0 and meta.max_edge_m > 0
            and min(meta.nodes, meta.edges, meta.coords) >= 0):
        raise ValueError(f"格網或筆數不合理：{raw}")
    return meta


@dataclass(frozen=True)
class Region:
    """圖的一塊：與查詢範圍重疊的格子裡的邊（超集合，見 RideGraph.region），節點重新編號成區域索引 0..n-1。

    邊用區域邊索引 0..m-1 稱呼；edge_ids 對回全域的 graph_edges 列。規劃器在 csr 上跑 scipy.sparse.csgraph
    （有方向，權重 = 邊長），再用 edge_between 把走過的節點對換回實際的邊與方向，用 geometry 取形狀。
    """

    nodes: np.ndarray  # float64 (n, 2) 區域節點的 (lon, lat)
    edge_ids: np.ndarray  # int64 (m,) 全域邊 id
    edge_nodes: np.ndarray  # int64 (m, 2) 區域節點索引 (from, to)，幾何的方向是 from → to
    flags: np.ndarray  # int64 (m,) FORWARD／BACKWARD 位元
    lengths: np.ndarray  # float64 (m,) 邊長（公尺）
    coords: np.ndarray  # float64 (k, 2) 所有區域邊的形狀串在一起
    coord_offsets: np.ndarray  # int64 (m + 1,) 第 i 條邊的形狀是 coords[coord_offsets[i]:coord_offsets[i + 1]]
    # 有方向的鄰接矩陣 (n, n)：順向可騎的邊給 from→to、逆向可騎的給 to→from，權重 = 邊長（下限 MIN_CSR_WEIGHT_M）。
    # 兩個節點之間有多條平行邊時只留最短的一條；自環（from == to）走不到別的節點，不放進來
    csr: csr_array
    csr_edges: np.ndarray  # int64 (nnz,) 每個 CSR 項（與 csr.data、csr.indices 同序）對應的區域邊索引
    csr_forward: np.ndarray  # bool (nnz,) 該項是順著幾何（True）還是逆著幾何（False）走那條邊
    csr_keys: np.ndarray  # int64 (nnz,) 每個 CSR 項的 tail * n + head，遞增；edge_between 用

    def __len__(self) -> int:
        return len(self.edge_ids)

    def geometry(self, edge: int, forward: bool = True) -> np.ndarray:
        """區域邊的形狀 (lon, lat)，forward=False 時倒過來（從 to 走到 from）。"""
        shape = self.coords[self.coord_offsets[edge]:self.coord_offsets[edge + 1]]
        return shape if forward else shape[::-1]

    def edge_between(self, tails: np.ndarray, heads: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """每一對區域節點 tail → head 在 csr 裡那一項對應的（區域邊索引, 是否順著幾何）。

        用來還原 csgraph 的前驅陣列走的是哪條邊（平行邊時就是 csr 留下的最短那條）。沒有這一項（不相鄰、
        或只能反方向走）丟 KeyError。
        """
        tails, heads = np.asarray(tails, dtype=np.int64), np.asarray(heads, dtype=np.int64)
        query = tails * len(self.nodes) + heads
        position = np.searchsorted(self.csr_keys, query)
        found = position < len(self.csr_keys)
        found[found] = self.csr_keys[position[found]] == query[found]
        if not found.all():
            missing = np.flatnonzero(~found)[0]
            raise KeyError(f"節點 {tails[missing]} → {heads[missing]} 之間沒有可騎的邊")
        return self.csr_edges[position], self.csr_forward[position]


def _region(nodes: np.ndarray, edge_ids: np.ndarray, edges: np.ndarray, lengths: np.ndarray,
            coords: np.ndarray, coord_offsets: np.ndarray) -> Region:
    """edges 是 (m, 4) 全域格式；重新編號節點、建 CSR。"""
    unique, inverse = np.unique(edges[:, [FROM, TO]], return_inverse=True)
    edge_nodes = inverse.reshape(-1, 2).astype(np.int64)
    flags = edges[:, FLAGS].astype(np.int64)
    n, index = len(unique), np.arange(len(edges), dtype=np.int64)
    forward, backward = (flags & FORWARD) != 0, (flags & BACKWARD) != 0
    tails = np.concatenate([edge_nodes[forward, 0], edge_nodes[backward, 1]])
    heads = np.concatenate([edge_nodes[forward, 1], edge_nodes[backward, 0]])
    which = np.concatenate([index[forward], index[backward]])
    along = np.concatenate([np.ones(forward.sum(), dtype=bool), np.zeros(backward.sum(), dtype=bool)])
    keep = tails != heads
    tails, heads, which, along = tails[keep], heads[keep], which[keep], along[keep]
    weight = lengths[which]
    # 依 (tail, head, 長度) 排序後每對節點留第一項：平行邊留最短的，同長時留區域索引小的（lexsort 是穩定排序）
    order = np.lexsort((weight, heads, tails))
    tails, heads, which, along, weight = tails[order], heads[order], which[order], along[order], weight[order]
    first = np.ones(len(tails), dtype=bool)
    first[1:] = (tails[1:] != tails[:-1]) | (heads[1:] != heads[:-1])
    tails, heads, which, along, weight = tails[first], heads[first], which[first], along[first], weight[first]
    indptr = np.zeros(n + 1, dtype=np.int64)
    np.cumsum(np.bincount(tails, minlength=n), out=indptr[1:])
    csr = csr_array((np.maximum(weight, MIN_CSR_WEIGHT_M), heads, indptr), shape=(n, n))
    return Region(nodes[unique], edge_ids, edge_nodes, flags, lengths, coords, coord_offsets,
                  csr, which, along, tails * n + heads)


class RideGraph:
    def __init__(self, meta: GraphMeta, nodes: np.ndarray, edges: np.ndarray, lengths: np.ndarray,
                 coords: np.ndarray, cells: np.ndarray) -> None:
        self.meta, self.nodes, self.edges, self.lengths, self.coords, self.cells = meta, nodes, edges, lengths, coords, cells

    @property
    def grid(self) -> Grid:
        return self.meta.grid

    @classmethod
    def load(cls, build_dir: Path) -> "RideGraph":
        """讀不到、讀壞或檔案與 graph.json 對不上都丟 GraphDataMissing（呼叫端當成沒有路網圖）。

        只做便宜的檢查（形狀、型別、格子索引）；邊的索引是否都在範圍內由建圖驗證（data.checks.graph_checks）把關。
        """
        try:
            meta = _meta(json.loads((build_dir / GRAPH_META).read_text()))
            arrays = [np.load(build_dir / name, mmap_mode="r")
                      for name in (GRAPH_NODES, GRAPH_EDGES, GRAPH_LENGTHS, GRAPH_COORDS, GRAPH_CELLS)]
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise GraphDataMissing(f"{build_dir}：{error}") from error
        nodes, edges, lengths, coords, cells = arrays
        cell_count = meta.grid.nx * meta.grid.ny + 1
        expected = (("節點", nodes, np.float64, (meta.nodes, 2)), ("邊", edges, np.int64, (meta.edges, 4)),
                    ("邊長", lengths, np.float32, (meta.edges,)), ("形狀", coords, np.float64, (meta.coords, 2)),
                    ("格子索引", cells, np.int64, (cell_count,)))
        for name, array, dtype, shape in expected:
            if array.dtype != dtype or array.shape != shape:
                raise GraphDataMissing(f"{build_dir}：{name}陣列是 {array.dtype} {array.shape}，應為 {np.dtype(dtype)} {shape}")
        if cells[0] != 0 or cells[-1] != meta.edges or np.any(np.diff(cells) < 0):
            raise GraphDataMissing(f"{build_dir}：格子索引與邊數 {meta.edges} 對不上")
        return cls(meta, nodes, edges, lengths, coords, cells)

    def __len__(self) -> int:
        return len(self.edges)

    def coord_end(self, edge: int) -> int:
        """第 edge 條邊的形狀在 coords 的終點（不含）：下一條邊的幾何起點，最後一條是 K。"""
        return int(self.edges[edge + 1, OFFSET]) if edge + 1 < len(self.edges) else len(self.coords)

    def region(self, min_lon: float, min_lat: float, max_lon: float, max_lat: float) -> Region:
        """與範圍相交的所有邊（含幾何）組成的區域圖；可能多出附近幾個格子的邊（超集合）。

        邊歸在兩端點中點所在的格子。每條邊不超過 max_edge_m，形狀上任一點離兩端點的中點不超過邊長的一半
        （三角不等式），所以範圍往外擴半個最長邊就不會漏掉穿過範圍的邊。經度方向用擴出後最大緯度的 cos 換算，保守。
        """
        grid = self.meta.grid
        margin_lat = math.degrees(self.meta.max_edge_m / 2 * MARGIN_FACTOR / EARTH_RADIUS_M) + MARGIN_SLOP_DEG
        far_lat = min(max(abs(min_lat), abs(max_lat)) + margin_lat, 89.0)
        margin_lon = margin_lat / math.cos(math.radians(far_lat))
        c0 = max(int(grid.col(min_lon - margin_lon)), 0)
        c1 = min(int(grid.col(max_lon + margin_lon)), grid.nx - 1)
        r0 = max(int(grid.row(min_lat - margin_lat)), 0)
        r1 = min(int(grid.row(max_lat + margin_lat)), grid.ny - 1)
        slices: list[tuple[int, int]] = []
        if c0 <= c1 and r0 <= r1:
            # 同一列相鄰格子的邊（與它們的形狀）在檔案裡是連續的：每列一個切片
            rows = np.arange(r0, r1 + 1) * grid.nx
            slices = [(int(s), int(e)) for s, e in zip(self.cells[rows + c0], self.cells[rows + c1 + 1]) if e > s]
        if not slices:
            empty = np.empty((0, 4), dtype=np.int64)
            return _region(self.nodes, np.empty(0, dtype=np.int64), empty, np.empty(0), np.empty((0, 2)),
                           np.zeros(1, dtype=np.int64))
        edge_ids = np.concatenate([np.arange(s, e, dtype=np.int64) for s, e in slices])
        edges = np.asarray(self.edges[edge_ids])
        parts, offsets, base = [], [], 0
        for s, e in slices:
            first, end = int(self.edges[s, OFFSET]), self.coord_end(e - 1)
            parts.append(np.asarray(self.coords[first:end]))
            offsets.append(np.asarray(self.edges[s:e, OFFSET]) - first + base)
            base += end - first
        coord_offsets = np.concatenate([*offsets, [base]]).astype(np.int64)
        lengths = np.asarray(self.lengths[edge_ids], dtype=np.float64)
        return _region(self.nodes, edge_ids, edges, lengths, np.concatenate(parts), coord_offsets)
