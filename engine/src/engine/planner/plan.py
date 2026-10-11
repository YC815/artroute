"""描形規劃器（P2.5 spec §6.2）：在可騎路網圖上找「像」目標筆畫的路徑。純規劃：不呼叫 Valhalla，結果由下一步交給
Valhalla 照著走（§6.4），合法性由 Valhalla 與既有的接點、轉彎檢查把關。

結構同 meili（逐點挑候選、動態規劃串起來），成本為「像」設計。所有長度都在以目標外框中心為原點的局部平面（公尺，北朝上）。

- 尺寸 L = 所有筆畫外框的長邊。取樣間距 Δ = clamp(0.01 L, 15 m, 60 m)。
- 取樣：輸入折線上 > 30° 的頂點一定取樣（轉角不會被均分的取樣切掉），頂點之間依弧長均分、間距 ≤ Δ；含頭尾，封閉筆畫首尾相接。
  取樣點的轉角 = 前後兩段的有號夾角，> 30° 的標成轉角。
- 候選：每個取樣點在 R = min(0.05 L, 250 m) 內找區域邊上的投影點（每條邊只留最近的一個）。候選有方向：可騎的方向各成一個
  （單行道只有一個，雙向的邊兩個），候選的方向 = 邊在投影處順著行進方向的方向。依 d / R + 0.5 (1 − cos θ) 留前 10 個
  （θ = 候選方向與目標在該點前後兩段中較像的那段的夾角）。
- 成本（權重見 PlannerWeights，單位都換算成公尺）：
  - 平移：每個取樣點 translation × Δ × ((d − r0) / (R − r0))²，d ≤ r0 = 0.01 L 免費。平行的路因此可以直接用。
  - 方向：路徑每公尺 direction × (1 − cos θ)，θ = 路徑有方向的方向與這段目標（相鄰兩個取樣點的連線）的夾角。
    一段路徑上 ∫(1 − cos θ) = 路徑長 − 位移·切線（Σ ℓᵢ uᵢ 就是起點到終點的位移），所以兩個候選固定時，方向成本最小的路徑
    就是最短路徑：轉移用邊長做 Dijkstra 就是精確的，不必依每段的切線重新加權（spec 的建議做法，數學上等價、但每段要重建 CSR）。
  - 轉彎：路徑上每個 > 30° 的轉彎（邊與邊的接點、邊形狀內的頂點）turn × Δ；> 150° 的迴轉另加 uturn × Δ。
  - 轉角：轉彎所在的這段目標弧長 max(0.05 L, 2Δ) 內有目標轉角時，另加 corner × Δ × |路線轉彎角 − 目標轉角角度|（弧度，
    取窗內最像的那個目標轉角）。提早或延後轉彎不扣，挑角度最像的路口。
  - 繞遠：兩個取樣點之間的路程超過兩個候選直線距離 1.5 倍的部分，每公尺 detour。
- 轉移：相鄰取樣點的候選之間，在有方向的圖（Region.csr 的結構，權重換成局部平面邊長）上，從前一點候選所在邊的終點做
  Dijkstra（scipy，一次多個起點、limit = 4Δ、回傳前驅），路徑 = 前一候選到邊終點 + 節點之間 + 下一候選的邊起點到它。
  同一條邊、同方向、順序可騎的兩個候選直接沿邊相連。除了轉彎與轉角，所有成本都能從 Dijkstra 的距離矩陣向量化算出（精確），
  所以每個下一點候選依這個下界由小到大只還原少數幾條路徑算轉彎（分支定界）。
- 封閉筆畫：起點選最尖的轉角（沒有轉角就是第一個取樣點）；終點候選必須等於起點候選。起點的前 3 個候選同步跑
  （狀態多一維，轉移的 Dijkstra 與路徑共用），取最好的。
- 開放筆畫：兩個方向都規劃（backward = 把筆畫倒過來）。先規劃所有筆畫的 forward，再規劃 backward：預算不夠時優先保住 forward。
- 斷開：取樣點沒有候選、或所有轉移都不可達時，在那裡斷開成不同的 PlannedPiece，中間的目標範圍記成沒規劃（交給 fast）。
- 期限：每個轉移之前 deadline.check()（逾時、取消照樣丟 MatchTimeout／MatchCancelled）；超過 budget_s（從呼叫開始算）就停，
  剩下的目標範圍記成沒規劃（不丟例外）。
- 確定性：所有排序都是穩定排序並有固定的決勝規則（同分時取索引小的），同樣的輸入一定得到同樣的輸出。

目標範圍（target_range、unplanned）用輸入點索引，含頭含尾；一段範圍的頭是這段第一個取樣點所在或之前的輸入點、尾是最後一個
取樣點所在或之後的輸入點。所以相鄰的 piece 與沒規劃的範圍會重疊邊界取樣點所在的那一段輸入線段（取樣點剛好在輸入點上時
只共用那一點）：例如 piece (0, 6) 之後接沒規劃的 (5, 9)，表示邊界在輸入點 5–6 之間。
- 封閉筆畫的索引以 mod n 解讀（n = 輸入點數，最後一點不重複起點）：規劃從最尖的轉角開始，範圍 (a, b) 一律 a ≤ b、b 可以 ≥ n，
  表示從 a 往前繞過起點到 b mod n。
- backward 的範圍用原筆畫的索引、依畫的先後排，所以 a ≥ b（從輸入點 a 畫回 b）。
"""

import math
import time
from dataclasses import dataclass, field

import numpy as np
from scipy.sparse import csr_array
from scipy.sparse.csgraph import dijkstra
from scipy.spatial import cKDTree

from engine.matching.deadline import Deadline
from engine.planner.graph import BACKWARD, FORWARD, MIN_CSR_WEIGHT_M, Region
from engine.routing.geo import EARTH_RADIUS_M
from engine.routing.router import LonLat

INF = math.inf


@dataclass(frozen=True)
class PlannerWeights:
    """規劃器的所有成本權重與常數。成本一律換算成公尺（「× Δ」的項目會隨圖形大小縮放，同一個圖形放大縮小行為一致）。

    初始值由 tests/test_planner.py 的合成場景定（之後用真實路網 bench 的 v3 調）。
    """

    sample_fraction: float = 0.01  # 取樣間距 Δ = clamp(這個 × L, min_sample_m, max_sample_m)
    min_sample_m: float = 15.0  # Δ 下限（公尺）
    max_sample_m: float = 60.0  # Δ 上限（公尺）
    candidate_fraction: float = 0.05  # 候選半徑 R = min(這個 × L, max_candidate_m)，也是平移成本封頂的距離
    max_candidate_m: float = 250.0  # 候選半徑上限（公尺）
    free_fraction: float = 0.01  # 平移免費的距離 r0 = 這個 × L（公尺；不超過 R 的一半）
    max_candidates: int = 10  # 每個取樣點留的候選數（有方向的候選，雙向的邊算兩個）
    candidate_direction: float = 0.5  # 候選排序：d / R + 這個 × (1 − cos θ)；1 表示方向相反等於遠了一個 R
    corner_deg: float = 30.0  # 目標轉角與路線轉彎的門檻（度）
    uturn_deg: float = 150.0  # 超過這個角度（度）的轉彎算迴轉
    corner_window_fraction: float = 0.05  # 目標轉角的窗：轉彎所在的目標弧長 max(這個 × L, 2Δ) 內有目標轉角才比角度
    transition_limit: float = 4.0  # 轉移的 Dijkstra 距離上限 = 這個 × Δ（節點到節點；不含兩個候選在自己邊上的那一截）
    detour_ratio: float = 1.5  # 路程超過兩個候選直線距離的這個倍數才算繞遠
    closed_starts: int = 3  # 封閉筆畫起點試幾個候選
    translation: float = 4.0  # 平移：每個取樣點 這個 × Δ × ((d − r0) / (R − r0))²（d = R 時 4Δ）
    direction: float = 1.0  # 方向：路徑每公尺 這個 × (1 − cos θ)
    turn: float = 1.0  # 轉彎：每個 > corner_deg 的轉彎 這個 × Δ
    uturn: float = 40.0  # 迴轉：每個 > uturn_deg 的轉彎另加 這個 × Δ（騎進去又退出來被避開）
    corner: float = 2.0  # 轉角：每弧度的角度差 這個 × Δ
    detour: float = 2.0  # 繞遠：每公尺超出的路程


@dataclass(frozen=True)
class PlannedPiece:
    points: list[LonLat]  # 沿圖上邊的幾何串起來的路徑：頭尾是候選的投影點，相鄰點來自同一條邊的形狀或共用節點，沒有跳點
    edges: list[tuple[int, bool]]  # (區域邊索引, 是否順著幾何方向) 依序；頭尾的邊可能只走一部分
    target_range: tuple[int, int]  # 對應目標筆畫的哪一段（輸入點索引，含頭含尾；封閉筆畫與 backward 的寫法見模組說明）
    cost: float  # 這段的總成本（公尺）


@dataclass(frozen=True)
class StrokePlan:
    forward: list[PlannedPiece]  # 依筆畫方向；可能斷成多段（中間有規劃不出來的地方 = 交給 fast 的範圍）
    backward: list[PlannedPiece] | None  # 開放筆畫反向（從終點畫到起點）的規劃；封閉筆畫為 None
    unplanned: list[tuple[int, int]]  # forward 沒規劃到的目標範圍（交給 fast），依筆畫方向排
    backward_unplanned: list[tuple[int, int]] | None = None  # backward 沒規劃到的範圍（原筆畫索引，a ≥ b）；封閉筆畫為 None


# ---- 局部平面與路網 ----


def _to_local(origin: tuple[float, float], lonlat: np.ndarray) -> np.ndarray:
    """(N, 2) 經緯度 → 以 origin 為原點的 (東, 北) 公尺（等距圓柱，與 search.geometry.lonlat_to_local 同式）。

    這是仿射轉換：局部平面上線段的內插比例拿回經緯度用，得到的點仍在同一條線段上。
    """
    rad = math.pi / 180
    return np.column_stack(((lonlat[:, 0] - origin[0]) * rad * EARTH_RADIUS_M * math.cos(origin[1] * rad),
                            (lonlat[:, 1] - origin[1]) * rad * EARTH_RADIUS_M))


def _wrap(angle: np.ndarray | float) -> np.ndarray | float:
    """換算到 (−π, π]。"""
    return np.pi - np.mod(np.pi - angle, 2 * np.pi)


class _Network:
    """區域圖換算到局部平面後規劃要用的東西（每次 plan_shape 建一次，所有筆畫共用）。"""

    def __init__(self, region: Region, origin: tuple[float, float], radius: float, corner_rad: float) -> None:
        self.region = region
        m = len(region)
        coords = np.asarray(region.coords, dtype=np.float64).reshape(-1, 2)
        offsets = np.asarray(region.coord_offsets, dtype=np.int64)
        self.coords, self.offsets = coords, offsets
        xy = _to_local(origin, coords)
        # 每個形狀點在自己那條邊上的位置（從幾何起點算的局部平面長度）；每條邊至少有兩個形狀點
        step = np.zeros(len(coords))
        step[1:] = np.hypot(*(xy[1:] - xy[:-1]).T)
        step[offsets[:-1]] = 0.0  # 每條邊的第一點：與前一條邊的最後一點無關
        cum = np.cumsum(step)
        self.vertex_pos = cum - np.repeat(cum[offsets[:-1]], np.diff(offsets))
        self.edge_len = self.vertex_pos[offsets[1:] - 1]
        # 線段：同一條邊相鄰兩個形狀點，零長度的不要（投影到一個點沒有方向，鄰近的線段已經涵蓋那個點）
        same_edge = np.ones(max(len(coords) - 1, 0), dtype=bool)
        same_edge[offsets[1:-1] - 1] = False  # 前一條邊的最後一點 → 下一條邊的第一點
        seg = np.flatnonzero(same_edge)
        vec = xy[seg + 1] - xy[seg]
        length = np.hypot(vec[:, 0], vec[:, 1])
        keep = length > 1e-9
        self.seg = seg[keep]  # 線段起點在 coords 的索引
        self.seg_a, self.seg_vec, self.seg_len = xy[self.seg], vec[keep], length[keep]
        self.seg_edge = np.searchsorted(offsets, self.seg, side="right") - 1
        self.seg_pos = self.vertex_pos[self.seg]
        heading = np.arctan2(self.seg_vec[:, 1], self.seg_vec[:, 0])
        # 每條邊順著幾何的進入、離開方向（第一、最後一個非零線段）；整條零長度的邊為 NaN（轉彎時略過）
        self.entry = np.full(m, np.nan)
        self.exit = np.full(m, np.nan)
        if len(self.seg):
            edges_with, first_index = np.unique(self.seg_edge, return_index=True)
            self.entry[edges_with] = heading[first_index]
            last_index = len(self.seg_edge) - 1 - np.unique(self.seg_edge[::-1], return_index=True)[1]
            self.exit[edges_with] = heading[last_index]
        # 邊形狀內 > 門檻的轉彎（順著幾何的有號角度、頂點位置），依邊分組
        if len(self.seg) > 1:
            pair = np.flatnonzero(self.seg_edge[1:] == self.seg_edge[:-1])
            angle = _wrap(heading[pair + 1] - heading[pair])
            sharp = np.abs(angle) > corner_rad
            pair, angle = pair[sharp], angle[sharp]
            self.turn_edge, self.turn_pos, self.turn_angle = self.seg_edge[pair + 1], self.seg_pos[pair + 1], angle
        else:
            self.turn_edge, self.turn_pos, self.turn_angle = (np.empty(0, dtype=np.int64), np.empty(0), np.empty(0))
        self.turn_offsets = np.searchsorted(self.turn_edge, np.arange(m + 1))
        # 有方向的圖：Region.csr 的結構（方向與平行邊的取捨），權重換成局部平面的邊長，與其他長度一致
        csr = region.csr
        self.csr = csr_array((np.maximum(self.edge_len[region.csr_edges], MIN_CSR_WEIGHT_M), csr.indices, csr.indptr),
                             shape=csr.shape)
        self.tails = np.asarray(region.edge_nodes[:, 0], dtype=np.int64)
        self.heads = np.asarray(region.edge_nodes[:, 1], dtype=np.int64)
        self.flags = np.asarray(region.flags, dtype=np.int64)
        # 候選粗篩：把線段切成不超過 R 的小段，cKDTree 建在小段中點上；查詢半徑 R + 半個小段就不會漏
        pieces = np.maximum(1, np.ceil(self.seg_len / max(radius, 1.0)).astype(np.int64))
        self.sub_seg = np.repeat(np.arange(len(self.seg)), pieces)
        part = np.arange(len(self.sub_seg)) - np.repeat(np.cumsum(pieces) - pieces, pieces)
        frac = (part + 0.5) / np.repeat(pieces, pieces)
        mid = self.seg_a[self.sub_seg] + self.seg_vec[self.sub_seg] * frac[:, None]
        self.sub_half = float((self.seg_len / pieces).max() / 2) if len(self.seg) else 0.0
        self.tree = cKDTree(mid) if len(mid) else None

    def point_at(self, edge: int, pos: float) -> tuple[float, float]:
        """邊上位置 pos（局部平面長度）的經緯度：落在形狀點上時就是那個點本身（不內插）。"""
        o0, o1 = int(self.offsets[edge]), int(self.offsets[edge + 1])
        cum = self.vertex_pos[o0:o1]
        if pos <= 0.0:
            return (float(self.coords[o0, 0]), float(self.coords[o0, 1]))
        if pos >= cum[-1]:
            return (float(self.coords[o1 - 1, 0]), float(self.coords[o1 - 1, 1]))
        q = min(int(np.searchsorted(cum, pos, side="right")) - 1, o1 - o0 - 2)
        span = cum[q + 1] - cum[q]
        t = (pos - cum[q]) / span if span > 0 else 0.0
        if t == 0.0:
            return (float(self.coords[o0 + q, 0]), float(self.coords[o0 + q, 1]))
        a, b = self.coords[o0 + q], self.coords[o0 + q + 1]
        return (float(a[0] + t * (b[0] - a[0])), float(a[1] + t * (b[1] - a[1])))

    def portion(self, edge: int, forward: bool, p0: float, p1: float) -> list[tuple[float, float]]:
        """沿邊從位置 p0 走到 p1（行進順序）的經緯度點：兩端的投影點加中間的形狀點。"""
        lo, hi = min(p0, p1), max(p0, p1)
        o0, o1 = int(self.offsets[edge]), int(self.offsets[edge + 1])
        cum = self.vertex_pos[o0:o1]
        inner = np.flatnonzero((cum > lo) & (cum < hi)) + o0
        points = [self.point_at(edge, lo), *((float(x), float(y)) for x, y in self.coords[inner]), self.point_at(edge, hi)]
        return points if forward else points[::-1]

    def internal_turns(self, edge: int, forward: bool, p0: float, p1: float) -> list[float]:
        """邊形狀內位置在 (min, max] 的轉彎（行進方向的有號角度）。

        半開區間讓轉彎恰好落在候選上時只算一次：順著走時頂點歸到抵達它的那段轉移，逆著走時歸到離開它的那段，
        兩種情況下都是「(較小位置, 較大位置]」。
        """
        t0, t1 = int(self.turn_offsets[edge]), int(self.turn_offsets[edge + 1])
        if t0 == t1:
            return []
        lo, hi = min(p0, p1), max(p0, p1)
        pos, angle = self.turn_pos[t0:t1], self.turn_angle[t0:t1]
        inside = angle[(pos > lo) & (pos <= hi)]
        return [float(a) for a in (inside if forward else -inside)]

    def headings(self, edge: int, forward: bool) -> tuple[float, float]:
        """行進方向的（進入, 離開）方向角。"""
        if forward:
            return float(self.entry[edge]), float(self.exit[edge])
        return float(self.exit[edge] + math.pi), float(self.entry[edge] + math.pi)


# ---- 取樣 ----


@dataclass
class _Samples:
    xy: np.ndarray  # (M, 2) 局部平面
    arc: np.ndarray  # (M,) 沿目標的弧長
    before: np.ndarray  # (M,) 取樣點所在或之前的輸入點索引
    after: np.ndarray  # (M,) 取樣點所在或之後的輸入點索引
    turn: np.ndarray  # (M,) 取樣點的有號轉角（弧度）；開放筆畫的頭尾為 0
    t_in: np.ndarray  # (M, 2) 從前一個取樣點來的單位方向（沒有時 NaN）
    t_out: np.ndarray  # (M, 2) 往下一個取樣點的單位方向（沒有時 NaN）
    total: float  # 目標總弧長


def _unit(vec: np.ndarray) -> np.ndarray:
    norm = np.hypot(vec[:, 0], vec[:, 1])
    out = np.full_like(vec, np.nan)
    ok = norm > 0
    out[ok] = vec[ok] / norm[ok, None]
    return out


def _sample(points: np.ndarray, closed: bool, delta: float, corner_rad: float) -> _Samples | None:
    """points (n, 2) 局部平面。封閉筆畫最後一個取樣點與第一個相同（索引 n 表示回到起點）。沒有長度時回傳 None。"""
    n = len(points)
    poly = np.vstack([points, points[:1]]) if closed else points
    index = np.arange(len(poly))
    keep = [0]
    for v in range(1, len(poly)):  # 去掉重複的相鄰點，記住每個保留下來的點原本的索引
        if np.any(poly[v] != poly[keep[-1]]):
            keep.append(v)
    if closed and len(keep) > 1 and keep[-1] != n:
        keep[-1] = n  # 最後一點與起點重合時，回到起點的那一點用索引 n
    if len(keep) < 2:
        return None
    pts, idx = poly[keep], index[keep]
    vec = pts[1:] - pts[:-1]
    seg_len = np.hypot(vec[:, 0], vec[:, 1])
    cum = np.concatenate([[0.0], np.cumsum(seg_len)])
    total = float(cum[-1])
    heading = np.arctan2(vec[:, 1], vec[:, 0])
    vertex_turn = np.zeros(len(pts))
    vertex_turn[1:-1] = _wrap(heading[1:] - heading[:-1])
    if closed:
        vertex_turn[0] = vertex_turn[-1] = _wrap(heading[0] - heading[-1])
    forced = [0, *(v for v in range(1, len(pts) - 1) if abs(vertex_turn[v]) > corner_rad), len(pts) - 1]
    arcs: list[float] = []
    for f0, f1 in zip(forced, forced[1:]):
        steps = max(1, math.ceil((cum[f1] - cum[f0]) / delta - 1e-9))
        arcs.extend(cum[f0] + (cum[f1] - cum[f0]) * k / steps for k in range(steps))
    arcs.append(total)
    arc = np.asarray(arcs)
    q = np.clip(np.searchsorted(cum, arc, side="right") - 1, 0, len(vec) - 1)
    frac = np.clip((arc - cum[q]) / seg_len[q], 0.0, 1.0)
    at_vertex = np.isclose(arc, cum[q], rtol=0, atol=1e-9)
    at_next = np.isclose(arc, cum[q + 1], rtol=0, atol=1e-9) & ~at_vertex
    xy = pts[q] + vec[q] * frac[:, None]
    xy[at_vertex] = pts[q[at_vertex]]
    xy[at_next] = pts[q[at_next] + 1]
    before = np.where(at_next, idx[q + 1], idx[q])
    after = np.where(at_vertex, idx[q], idx[q + 1])
    step = xy[1:] - xy[:-1]
    t_seg = _unit(step)
    m = len(xy)
    t_in, t_out = np.full((m, 2), np.nan), np.full((m, 2), np.nan)
    t_in[1:], t_out[:-1] = t_seg, t_seg
    turn = np.zeros(m)
    seg_heading = np.arctan2(step[:, 1], step[:, 0])
    if m > 2:
        turn[1:-1] = _wrap(seg_heading[1:] - seg_heading[:-1])
    if closed and m > 2:
        turn[0] = turn[-1] = _wrap(seg_heading[0] - seg_heading[-1])
        t_in[0], t_out[-1] = t_seg[-1], t_seg[0]
    return _Samples(xy, arc, before.astype(np.int64), after.astype(np.int64), turn, t_in, t_out, total)


# ---- 候選 ----


@dataclass
class _Candidates:
    """一個取樣點的有方向候選，依排序鍵由好到壞。"""

    edge: np.ndarray  # (C,) 區域邊索引
    forward: np.ndarray  # (C,) 是否順著幾何走
    pos: np.ndarray  # (C,) 在邊上的位置（局部平面長度，從幾何起點算）
    xy: np.ndarray  # (C, 2) 投影點
    dist: np.ndarray  # (C,) 離取樣點的距離

    def __len__(self) -> int:
        return len(self.edge)


def _empty_candidates() -> _Candidates:
    return _Candidates(np.empty(0, dtype=np.int64), np.empty(0, dtype=bool), np.empty(0), np.empty((0, 2)), np.empty(0))


def _candidates(net: _Network, samples: _Samples, radius: float, weights: PlannerWeights) -> list[_Candidates]:
    m = len(samples.xy)
    if net.tree is None:
        return [_empty_candidates() for _ in range(m)]
    found = net.tree.query_ball_point(samples.xy, radius + net.sub_half)
    counts = np.fromiter((len(f) for f in found), dtype=np.int64, count=m)
    if counts.sum() == 0:
        return [_empty_candidates() for _ in range(m)]
    sample = np.repeat(np.arange(m), counts)
    seg = net.sub_seg[np.concatenate([np.asarray(f, dtype=np.int64) for f in found])]
    pair = np.unique(sample * len(net.seg) + seg)  # 同一條線段的多個小段只算一次
    sample, seg = pair // len(net.seg), pair % len(net.seg)
    point = samples.xy[sample]
    a, vec, length = net.seg_a[seg], net.seg_vec[seg], net.seg_len[seg]
    t = np.clip(np.einsum("ij,ij->i", point - a, vec) / length**2, 0.0, 1.0)
    proj = a + vec * t[:, None]
    dist = np.hypot(*(point - proj).T)
    near = dist <= radius
    sample, seg, t, proj, dist = sample[near], seg[near], t[near], proj[near], dist[near]
    edge = net.seg_edge[seg]
    pos = net.seg_pos[seg] + t * net.seg_len[seg]
    # 每個（取樣點, 邊）只留最近的投影；同距離取位置小的
    order = np.lexsort((pos, dist, edge, sample))
    sample, seg, edge, pos, proj, dist = sample[order], seg[order], edge[order], pos[order], proj[order], dist[order]
    first = np.ones(len(sample), dtype=bool)
    first[1:] = (sample[1:] != sample[:-1]) | (edge[1:] != edge[:-1])
    sample, seg, edge, pos, proj, dist = sample[first], seg[first], edge[first], pos[first], proj[first], dist[first]
    # 展開成有方向的候選：只有可騎的方向
    unit = net.seg_vec[seg] / net.seg_len[seg, None]
    flags = net.flags[edge]
    along = np.concatenate([np.flatnonzero(flags & FORWARD), np.flatnonzero(flags & BACKWARD)])
    forward = np.concatenate([np.ones(int((flags & FORWARD).astype(bool).sum()), dtype=bool),
                              np.zeros(int((flags & BACKWARD).astype(bool).sum()), dtype=bool)])
    sample, edge, pos, proj, dist = sample[along], edge[along], pos[along], proj[along], dist[along]
    direction = unit[along] * np.where(forward, 1.0, -1.0)[:, None]
    cos_in = np.einsum("ij,ij->i", direction, samples.t_in[sample])
    cos_out = np.einsum("ij,ij->i", direction, samples.t_out[sample])
    cos = np.fmax(cos_in, cos_out)  # NaN（筆畫頭尾沒有那一段）被忽略
    key = dist / radius + weights.candidate_direction * (1.0 - cos)
    # 依（取樣點, 排序鍵, 邊, 順向優先）排，每個取樣點留前 N 個
    order = np.lexsort((~forward, edge, key, sample))
    sample, edge, forward, pos, proj, dist = (sample[order], edge[order], forward[order], pos[order], proj[order],
                                              dist[order])
    start = np.searchsorted(sample, np.arange(m + 1))
    out = []
    for i in range(m):
        s, e = start[i], min(start[i + 1], start[i] + weights.max_candidates)
        out.append(_Candidates(edge[s:e], forward[s:e], pos[s:e], proj[s:e], dist[s:e]))
    return out


# ---- 動態規劃 ----


type _Traversal = tuple[int, bool, float, float]  # (邊, 是否順著幾何, 從位置, 到位置)


@dataclass
class _Context:
    net: _Network
    weights: PlannerWeights
    delta: float
    radius: float
    free: float
    corner_rad: float
    uturn_rad: float
    window: float
    deadline: Deadline
    stop_at: float
    out_of_budget: bool = False


@dataclass
class _Piece:
    first: int  # 取樣點索引（含）
    last: int  # 取樣點索引（含）
    traversals: list[_Traversal]
    cost: float


@dataclass
class _Run:
    pieces: list[_Piece] = field(default_factory=list)
    gaps: list[tuple[int, int]] = field(default_factory=list)  # 沒規劃的取樣點範圍（含頭含尾）


def _translation(ctx: _Context, dist: np.ndarray) -> np.ndarray:
    span = max(ctx.radius - ctx.free, 1e-9)
    excess = np.clip((dist - ctx.free) / span, 0.0, 1.0)
    return ctx.weights.translation * ctx.delta * excess**2


def _turn_cost(ctx: _Context, angles: list[float], corners: np.ndarray) -> float:
    w, cost = ctx.weights, 0.0
    for angle in angles:
        size = abs(angle)
        if size <= ctx.corner_rad:
            continue
        cost += w.turn * ctx.delta
        if size > ctx.uturn_rad:
            cost += w.uturn * ctx.delta
        if len(corners):
            cost += w.corner * ctx.delta * float(np.abs(_wrap(angle - corners)).min())
    return cost


def _path_turns(net: _Network, traversals: list[_Traversal]) -> list[float]:
    """路徑上的轉彎角度：邊與邊的接點（前一條邊的離開方向 → 下一條邊的進入方向），以及邊形狀內的轉彎。"""
    angles: list[float] = []
    previous = math.nan
    for edge, forward, p0, p1 in traversals:
        entry, leave = net.headings(edge, forward)
        if not math.isnan(previous) and not math.isnan(entry):
            angles.append(float(_wrap(entry - previous)))
        angles.extend(net.internal_turns(edge, forward, p0, p1))
        if not math.isnan(leave):
            previous = leave
    return angles


class _Transition:
    """取樣點 i 的候選 A → 取樣點 i + 1 的候選 B：一次 Dijkstra，向量化算出除了轉彎以外的成本（精確的下界），
    轉彎只對需要的配對還原路徑才算（結果快取，封閉筆畫的多個起點共用）。"""

    def __init__(self, ctx: _Context, a: _Candidates, b: _Candidates, target: np.ndarray, corners: np.ndarray) -> None:
        net, w = ctx.net, ctx.weights
        self.ctx, self.a, self.b, self.corners = ctx, a, b, corners
        a_len, b_len = net.edge_len[a.edge], net.edge_len[b.edge]
        self.a_head = np.where(a.forward, net.heads[a.edge], net.tails[a.edge])
        a_rest = np.where(a.forward, a_len - a.pos, a.pos)
        self.b_tail = np.where(b.forward, net.tails[b.edge], net.heads[b.edge])
        b_lead = np.where(b.forward, b.pos, b_len - b.pos)
        self.sources, self.source_row = np.unique(self.a_head, return_inverse=True)
        self.dist, self.pred = dijkstra(net.csr, directed=True, indices=self.sources, return_predecessors=True,
                                        limit=w.transition_limit * ctx.delta)
        node_dist = self.dist[self.source_row][:, self.b_tail]
        ahead = np.where(a.forward[:, None], b.pos[None] >= a.pos[:, None], b.pos[None] <= a.pos[:, None])
        self.direct = (a.edge[:, None] == b.edge[None]) & (a.forward[:, None] == b.forward[None]) & ahead
        length = np.where(self.direct, np.abs(b.pos[None] - a.pos[:, None]), a_rest[:, None] + node_dist + b_lead[None])
        shift = b.xy[None] - a.xy[:, None]
        chord = np.hypot(shift[..., 0], shift[..., 1])
        along = shift[..., 0] * target[0] + shift[..., 1] * target[1]
        # 方向成本 = 路徑長 − 位移·切線（模組說明），數學上 ≥ 0，夾掉浮點誤差的負數。權重為 0 時 0 × inf 是 NaN：
        # 不可達的配對最後一律設成 inf
        with np.errstate(invalid="ignore"):
            self.base = (w.direction * np.maximum(length - along, 0.0)
                         + w.detour * np.maximum(0.0, length - w.detour_ratio * chord))
        self.base[~np.isfinite(length)] = INF
        self._paths: dict[tuple[int, int], tuple[float, list[_Traversal]]] = {}

    def path(self, i: int, j: int) -> tuple[float, list[_Traversal]]:
        """A 的第 i 個候選到 B 的第 j 個候選：（轉彎＋轉角成本, 路徑）。"""
        cached = self._paths.get((i, j))
        if cached is not None:
            return cached
        net, a, b = self.ctx.net, self.a, self.b
        ea, fa, pa = int(a.edge[i]), bool(a.forward[i]), float(a.pos[i])
        eb, fb, pb = int(b.edge[j]), bool(b.forward[j]), float(b.pos[j])
        if self.direct[i, j]:
            traversals: list[_Traversal] = [(ea, fa, pa, pb)]
        else:
            traversals = [(ea, fa, pa, float(net.edge_len[ea]) if fa else 0.0)]
            head, tail = int(self.a_head[i]), int(self.b_tail[j])
            nodes = [tail]
            predecessors = self.pred[self.source_row[i]]
            while nodes[-1] != head:
                nodes.append(int(predecessors[nodes[-1]]))
            nodes.reverse()
            if len(nodes) > 1:
                edges, forwards = net.region.edge_between(np.asarray(nodes[:-1]), np.asarray(nodes[1:]))
                for e, f in zip(edges.tolist(), forwards.tolist()):
                    length = float(net.edge_len[e])
                    traversals.append((e, f, 0.0, length) if f else (e, f, length, 0.0))
            traversals.append((eb, fb, 0.0 if fb else float(net.edge_len[eb]), pb))
        result = (_turn_cost(self.ctx, _path_turns(net, traversals), self.corners), traversals)
        self._paths[(i, j)] = result
        return result

    def step(self, dp: np.ndarray, arrival: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """dp (S, |A|) → (S, |B|) 與回溯指標。arrival (|B|,) 是 B 的平移成本（封閉筆畫回到起點時為 0）。

        每個 (s, j) 依下界（dp + 向量化成本）由小到大還原路徑加上轉彎成本，下界已經不小於目前最好的就停（分支定界）。
        同分時取下界排序在前的（穩定排序，即索引小的）。
        """
        lower = dp[:, :, None] + self.base[None]  # (S, |A|, |B|)
        best = np.full((dp.shape[0], len(self.b)), INF)
        back = np.full((dp.shape[0], len(self.b)), -1, dtype=np.int64)
        order = np.argsort(lower, axis=1, kind="stable")
        for s in range(dp.shape[0]):
            for j in range(len(self.b)):
                column, top = lower[s, :, j], INF
                for i in order[s, :, j]:
                    bound = column[i]
                    if not bound < top:
                        break
                    total = bound + self.path(int(i), j)[0]
                    if total < top:
                        top, back[s, j] = total, i
                best[s, j] = top
        return best + arrival[None], back


def _plan_run(ctx: _Context, samples: _Samples, cands: list[_Candidates], closed: bool) -> _Run:
    """依取樣順序跑動態規劃，回傳各段與沒規劃的取樣點範圍。

    closed=True 時 samples 已經從起點轉角開始、最後一點回到起點（cands[-1] 就是 cands[0]）：從第一個取樣點開始的那一段
    對起點的前 closed_starts 個候選同步跑（dp 的第一維），最後只收回到同一個候選的狀態。中途斷開時就不再要求首尾相接。
    """
    m = len(samples.xy)
    run = _Run()
    corner = np.abs(samples.turn) > ctx.corner_rad
    corner_arc, corner_angle = samples.arc[corner], samples.turn[corner]

    def corners_near(i: int) -> np.ndarray:
        """轉移 i → i + 1 所在的目標弧長窗內的目標轉角（有號弧度）。"""
        if not len(corner_arc):
            return corner_angle
        gap = np.abs(corner_arc - (samples.arc[i] + samples.arc[i + 1]) / 2)
        if closed:
            gap = np.mod(gap, samples.total)
            gap = np.minimum(gap, samples.total - gap)
        return corner_angle[gap <= ctx.window]

    previous_end: int | None = None  # 上一段的最後一個取樣點
    i = 0
    while i < m and not ctx.out_of_budget:
        if not len(cands[i]):
            i += 1
            continue
        first = i
        lockstep = closed and first == 0
        count = min(ctx.weights.closed_starts, len(cands[first])) if lockstep else 1
        arrival = _translation(ctx, cands[first].dist)
        dp = np.full((count, len(cands[first])), INF)
        if lockstep:
            dp[np.arange(count), np.arange(count)] = arrival[:count]
        else:
            dp[0] = arrival
        history: list[np.ndarray] = [dp]
        backs: list[np.ndarray] = []
        transitions: list[_Transition] = []
        while i < m - 1:
            if time.monotonic() > ctx.stop_at:
                ctx.out_of_budget = True
                break
            ctx.deadline.check()
            following = cands[i + 1]
            if not len(following):
                break
            target = samples.xy[i + 1] - samples.xy[i]
            transition = _Transition(ctx, cands[i], following, target / math.hypot(target[0], target[1]), corners_near(i))
            closing = lockstep and i + 1 == m - 1
            # 回到起點那一點的平移在起點已經算過
            arrival = np.zeros(len(following)) if closing else _translation(ctx, following.dist)
            new_dp, back = transition.step(history[-1], arrival)
            if closing:  # 只收與起點相同的候選
                diagonal = new_dp[np.arange(count), np.arange(count)]
                new_dp = np.full_like(new_dp, INF)
                new_dp[np.arange(count), np.arange(count)] = diagonal
            if not np.isfinite(new_dp).any():
                break
            history.append(new_dp)
            backs.append(back)
            transitions.append(transition)
            i += 1
        last = first + len(transitions)
        if transitions:
            # 最後一個取樣點最好的狀態回溯（同分取索引小的）
            final = history[-1]
            s, c = np.unravel_index(int(np.argmin(final)), final.shape)
            chain = [int(c)]
            for back in reversed(backs):
                chain.append(int(back[s, chain[-1]]))
            chain.reverse()
            traversals = [t for k, transition in enumerate(transitions)
                          for t in transition.path(chain[k], chain[k + 1])[1]]
            if previous_end is not None or first > 0:
                run.gaps.append((previous_end if previous_end is not None else 0, first))
            run.pieces.append(_Piece(first, last, traversals, float(final[s, c])))
            previous_end = last
        i = last + 1  # 斷在 last → last + 1：從下一點重新開始（預算用完時迴圈條件會停）
    if previous_end is None:
        run.gaps.append((0, m - 1))
    elif previous_end < m - 1:
        run.gaps.append((previous_end, m - 1))
    return run


def _merge(net: _Network, traversals: list[_Traversal]) -> tuple[list[tuple[float, float]], list[tuple[int, bool]]]:
    """接起連續走同一條邊同方向的部分，去掉零長度的部分，串成點列與邊列。"""
    merged: list[list] = []
    for edge, forward, p0, p1 in traversals:
        if merged and merged[-1][0] == edge and merged[-1][1] == forward and merged[-1][3] == p0:
            merged[-1][3] = p1
        else:
            merged.append([edge, forward, p0, p1])
    points: list[tuple[float, float]] = []
    edges: list[tuple[int, bool]] = []
    for edge, forward, p0, p1 in merged:
        if p0 == p1:
            continue
        edges.append((int(edge), bool(forward)))
        for point in net.portion(edge, forward, p0, p1):
            if not points or points[-1] != point:
                points.append(point)
    return points, edges


type _Range = tuple[int, int]


def _plan_stroke(ctx: _Context, points: np.ndarray, closed: bool) -> tuple[list[PlannedPiece], list[_Range]]:
    """一個方向的規劃。points (n, 2) 局部平面（backward 時呼叫端已經倒過來）。範圍用這個點列的索引。"""
    n = len(points)
    whole = [(0, n if closed else n - 1)] if n else []
    samples = _sample(points, closed, ctx.delta, ctx.corner_rad) if n else None
    if samples is None:
        return [], whole
    if ctx.out_of_budget or time.monotonic() > ctx.stop_at:
        ctx.out_of_budget = True
        return [], whole
    cands = _candidates(ctx.net, samples, ctx.radius, ctx.weights)
    unwrap = np.zeros(len(samples.xy), dtype=np.int64)
    if closed:
        # 起點：有候選的取樣點中最尖的轉角，沒有轉角就是第一個有候選的取樣點。從那裡繞一圈回到它；
        # 繞過原本起點之後的取樣點索引加 n（原本的最後一個取樣點，也就是回到起點的索引 n，不再用到）
        m = len(samples.xy) - 1
        has = np.array([len(c) > 0 for c in cands[:m]])
        sharp = np.where(has & (np.abs(samples.turn[:m]) > ctx.corner_rad), np.abs(samples.turn[:m]), -1.0)
        k = int(np.argmax(sharp)) if sharp.max() > 0 else int(np.argmax(has))
        order = np.concatenate([np.arange(k, m), np.arange(0, k + 1)])
        wrapped = (np.arange(len(order)) >= m - k).astype(np.int64)
        unwrap = wrapped * n
        samples = _Samples(samples.xy[order], samples.arc[order] + wrapped * samples.total, samples.before[order],
                           samples.after[order], samples.turn[order], samples.t_in[order], samples.t_out[order],
                           samples.total)
        cands = [cands[j] for j in order.tolist()]
    run = _plan_run(ctx, samples, cands, closed)
    before, after = samples.before + unwrap, samples.after + unwrap
    pieces, gaps = [], list(run.gaps)
    for piece in run.pieces:
        piece_points, edges = _merge(ctx.net, piece.traversals)
        if len(piece_points) < 2:  # 所有候選都在同一點：沒有可以照著走的路徑
            gaps.append((piece.first, piece.last))
            continue
        pieces.append(PlannedPiece(piece_points, edges, (int(before[piece.first]), int(after[piece.last])), piece.cost))
    return pieces, _merge_ranges([(int(before[g0]), int(after[g1])) for g0, g1 in gaps])


def _merge_ranges(ranges: list[_Range]) -> list[_Range]:
    """依起點排序，重疊（不只共用端點）的範圍合併。"""
    out: list[_Range] = []
    for start, end in sorted(ranges):
        if out and start < out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], end))
        else:
            out.append((start, end))
    return out


def plan_shape(region: Region, strokes: list[list[LonLat]], closed: list[bool], *, deadline: Deadline,
               budget_s: float, weights: PlannerWeights = PlannerWeights()) -> list[StrokePlan]:
    """規劃每個筆畫在 region 上「像」的路徑（演算法、範圍的寫法見模組說明）。回傳與 strokes 同序。

    region 要涵蓋目標外框往外 R（候選半徑）再加上轉移可能走到的範圍（呼叫端通常取外框往外擴 R + 4Δ）。
    strokes 的點是 (lon, lat)；closed[i] 為 True 時最後一點不重複起點。
    deadline 逾時或取消時照樣丟例外；budget_s（秒，從呼叫開始算）用完時停下，剩下的範圍記成沒規劃。
    先規劃所有筆畫的 forward 再規劃 backward：預算不夠時優先保住 forward。
    """
    if len(strokes) != len(closed):
        raise ValueError("strokes 與 closed 的長度不同")
    stop_at = time.monotonic() + budget_s
    arrays = [np.asarray(s, dtype=np.float64).reshape(-1, 2) for s in strokes]
    every = np.vstack(arrays) if any(len(a) for a in arrays) else np.zeros((1, 2))
    low, high = every.min(axis=0), every.max(axis=0)
    origin = (float(low[0] + high[0]) / 2, float(low[1] + high[1]) / 2)
    local = [_to_local(origin, a) for a in arrays]
    extent = _to_local(origin, np.vstack([low, high]))
    size = float(max(extent[1] - extent[0]))
    delta = min(max(weights.sample_fraction * size, weights.min_sample_m), weights.max_sample_m)
    radius = max(min(weights.candidate_fraction * size, weights.max_candidate_m), 1.0)
    free = min(weights.free_fraction * size, radius / 2)
    corner_rad, uturn_rad = math.radians(weights.corner_deg), math.radians(weights.uturn_deg)
    net = _Network(region, origin, radius, corner_rad)
    ctx = _Context(net, weights, delta, radius, free, corner_rad, uturn_rad,
                   max(weights.corner_window_fraction * size, 2 * delta), deadline, stop_at)
    forward = [_plan_stroke(ctx, points, flag) for points, flag in zip(local, closed)]
    plans = []
    for (pieces, gaps), points, flag in zip(forward, local, closed):
        if flag:
            plans.append(StrokePlan(pieces, None, gaps, None))
            continue
        back_pieces, back_gaps = _plan_stroke(ctx, points[::-1], False)
        last = len(points) - 1

        def flip(r: _Range, last: int = last) -> _Range:  # 倒過來的索引換回原筆畫
            return (last - r[0], last - r[1])

        plans.append(StrokePlan(pieces, [PlannedPiece(p.points, p.edges, flip(p.target_range), p.cost)
                                         for p in back_pieces], gaps, [flip(g) for g in back_gaps]))
    return plans
