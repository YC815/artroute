"""貼合度評分（spec §4.5）：在以目標中心為原點的局部平面（公尺）上量距離。

score() 是 v2（/match 回傳、自動搜尋的目標函數）；score_v1() 保留原本的算法，bench 拿它和 v1 baseline 做同一把尺的比較。
"""

import math
from dataclasses import dataclass

import numpy as np

from engine.routing.router import LonLat

SCORE_VERSION = 2  # score() 的版本；bench baseline 記這個值，換評分算法就加一
SAMPLE_STEP_M = 10.0  # 覆蓋（兩版）與 v1 乾淨的取樣間距
ZERO_SCORE_RATIO = 0.05  # 平均偏離達對角線 5% 時為 0 分；由基準測試集調整
ZERO_SCORE_MIN_M = 60.0  # v2 的 0 分距離下限：完美路線與畫線也有 e≈16–20 m 的吸附偏差，最多只該扣掉約三分之一的分數
VISIBLE_STEP_M = 2.5  # v2 乾淨：路線依弧長每 2.5 m 取樣（格子邊長的一半，直線穿過的格子都取得到）
VISIBLE_CELL_M = 5.0  # v2 乾淨：取樣點量化到 5 m 格子、每格只留一點；同一條街來回騎（不論方向）佔同一批格子
_BUDGET = 2_000_000  # 每批 (取樣點數 × 線段數) 的上限；暫存陣列為 (chunk, M, 2)，記憶體隨 M 成長，故依 M 縮 chunk


@dataclass(frozen=True)
class Score:
    coverage: int
    cleanliness: int
    overall: int


def _projector(targets: list[list[LonLat]]):
    lons = [lon for line in targets for lon, _ in line]
    lats = [lat for line in targets for _, lat in line]
    lon0, lat0 = (min(lons) + max(lons)) / 2, (min(lats) + max(lats)) / 2
    kx = 111_320.0 * math.cos(math.radians(lat0))
    ky = 110_574.0

    def project(points: list[LonLat]) -> np.ndarray:
        arr = np.asarray(points, dtype=float).reshape(-1, 2)
        return np.column_stack(((arr[:, 0] - lon0) * kx, (arr[:, 1] - lat0) * ky))

    return project


def _sample(line: np.ndarray, step_m: float = SAMPLE_STEP_M) -> np.ndarray:
    """依累積弧長等距重取樣，取樣密度與頂點疏密無關。
    間距是 ≤ step_m 的等分（含兩端點），所以反過來走取到的是同一組點。"""
    s = np.concatenate(([0.0], np.cumsum(np.hypot(*np.diff(line, axis=0).T))))
    if s[-1] == 0.0:
        return line[:1]
    t = np.linspace(0.0, s[-1], math.ceil(s[-1] / step_m) + 1)
    return np.column_stack((np.interp(t, s, line[:, 0]), np.interp(t, s, line[:, 1])))


def _visible(line: np.ndarray) -> np.ndarray:
    """地圖上看得到的線：路線每 VISIBLE_STEP_M 取樣、量化到 VISIBLE_CELL_M 的格子，每格留一點。

    看得到的是路線的聯集而不是重數：同一條街騎兩次（任一方向）佔同一批格子，只算一次。
    每格的代表點取該格取樣點中座標字典序最小的一點，與騎的順序和方向無關
    （「沿路線第一個經過的點」會隨方向改變，分數就跟著變）。
    """
    samples = _sample(line, VISIBLE_STEP_M)
    cells = np.floor(samples / VISIBLE_CELL_M)
    order = np.lexsort((samples[:, 1], samples[:, 0], cells[:, 1], cells[:, 0]))
    sorted_cells = cells[order]
    first = np.ones(len(order), dtype=bool)
    first[1:] = (sorted_cells[1:] != sorted_cells[:-1]).any(axis=1)
    return samples[order[first]]


def _segments(lines: list[np.ndarray]) -> tuple[np.ndarray, np.ndarray]:
    starts = np.vstack([line[:-1] for line in lines if len(line) > 1])
    ends = np.vstack([line[1:] for line in lines if len(line) > 1])
    return starts, ends


def _distances(points: np.ndarray, starts: np.ndarray, ends: np.ndarray) -> np.ndarray:
    ab = ends - starts
    ab_len2 = np.maximum((ab**2).sum(axis=1), 1e-12)
    out = np.empty(len(points))
    rows = max(1, _BUDGET // len(starts))
    for i in range(0, len(points), rows):
        p = points[i : i + rows, None, :]
        t = np.clip(((p - starts) * ab).sum(axis=2) / ab_len2, 0.0, 1.0)
        nearest = starts + t[..., None] * ab
        out[i : i + rows] = np.sqrt(((p - nearest) ** 2).sum(axis=2)).min(axis=1)
    return out


def _component_v1(distances: np.ndarray, diagonal: float) -> int:
    e = (distances.mean() + np.percentile(distances, 90)) / 2 / diagonal
    return round(100 * max(0.0, 1 - e / ZERO_SCORE_RATIO))


def _component(distances: np.ndarray, zero_m: float) -> int:
    e = (distances.mean() + np.percentile(distances, 90)) / 2
    return round(100 * max(0.0, 1 - e / zero_m))


@dataclass(frozen=True)
class _Prepared:
    target_lines: list[np.ndarray]
    route_line: np.ndarray
    diagonal: float


def _prepare(targets: list[list[LonLat]], route: list[LonLat]) -> _Prepared | None:
    """投影到局部平面；路線不到兩點回傳 None（0 分）。目標太小丟 ValueError。"""
    project = _projector(targets)
    target_lines = [project(line) for line in targets]
    stacked = np.vstack(target_lines)
    diagonal = float(np.hypot(*(stacked.max(axis=0) - stacked.min(axis=0))))
    if diagonal < 1.0:
        raise ValueError("目標圖形太小，無法評分")
    if len(route) < 2:
        return None
    return _Prepared(target_lines, project(route), diagonal)


def _coverage_distances(p: _Prepared) -> np.ndarray:
    """目標每 SAMPLE_STEP_M 取樣，到路線折線的距離（兩版相同）。"""
    route_starts, route_ends = _segments([p.route_line])
    return _distances(np.vstack([_sample(line) for line in p.target_lines if len(line) > 1]), route_starts, route_ends)


def score(targets: list[list[LonLat]], route: list[LonLat]) -> Score:
    """v2：乾淨度只看地圖上看得到的線（_visible），0 分距離 = max(對角線 × ZERO_SCORE_RATIO, ZERO_SCORE_MIN_M)。"""
    p = _prepare(targets, route)
    if p is None:
        return Score(0, 0, 0)
    zero_m = max(ZERO_SCORE_RATIO * p.diagonal, ZERO_SCORE_MIN_M)
    target_starts, target_ends = _segments(p.target_lines)
    coverage = _component(_coverage_distances(p), zero_m)
    cleanliness = _component(_distances(_visible(p.route_line), target_starts, target_ends), zero_m)
    return Score(coverage, cleanliness, round((coverage + cleanliness) / 2))


def score_v1(targets: list[list[LonLat]], route: list[LonLat]) -> Score:
    """v1（原本的算法，數字不可改）：路線每 SAMPLE_STEP_M 取樣（重複騎的路段重複計），0 分距離 = 對角線 × ZERO_SCORE_RATIO。"""
    p = _prepare(targets, route)
    if p is None:
        return Score(0, 0, 0)
    target_starts, target_ends = _segments(p.target_lines)
    coverage = _component_v1(_coverage_distances(p), p.diagonal)
    cleanliness = _component_v1(_distances(_sample(p.route_line), target_starts, target_ends), p.diagonal)
    return Score(coverage, cleanliness, round((coverage + cleanliness) / 2))
