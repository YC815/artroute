"""貼合度評分：在以目標中心為原點的局部平面上量距離。

score() 是 v3（P2.5 spec §4.1：縮圖相似度，/match 回傳、自動搜尋的目標函數）；score_v2()、score_v1()（主 spec §4.5）
保留舊的算法，bench 拿它們和舊 baseline 做同一把尺的比較。rank() 是搜尋的排名分數（P2.5 spec §4.3）。
"""

import math
from dataclasses import dataclass

import numpy as np

from engine.routing.router import LonLat

SCORE_VERSION = 3  # score() 的版本；bench baseline 記這個值，換評分算法就加一
SAMPLE_STEP_M = 10.0  # v1、v2 覆蓋與 v1 乾淨的取樣間距
ZERO_SCORE_RATIO = 0.05  # 平均偏離達對角線 5% 時為 0 分；由基準測試集調整
ZERO_SCORE_MIN_M = 60.0  # v2 的 0 分距離下限：完美路線與畫線也有 e≈16–20 m 的吸附偏差，最多只該扣掉約三分之一的分數
VISIBLE_STEP_M = 2.5  # v2 乾淨：路線依弧長每 2.5 m 取樣（格子邊長的一半，直線穿過的格子都取得到）
VISIBLE_CELL_M = 5.0  # v2 乾淨：取樣點量化到 5 m 格子、每格只留一點；同一條街來回騎（不論方向）佔同一批格子
# v3（P2.5 spec §4.1）：量的是手機縮圖上看到的像素；容差、權重、簡化容差都是 spec 的初始值（使用者決定不做網格搜尋）
CANVAS_PX = 300.0  # 目標外框長邊縮放到 300 px，路線用同一個轉換：圖形大小不影響分數
SIMPLIFY_PX = 1.5  # 路線先用 Douglas-Peucker 簡化：縮圖上看不到的鋸齒不算
FULL_CREDIT_PX = 3.0  # 距離 ≤ 3 px（1%）滿分
ZERO_CREDIT_PX = 15.0  # 距離 ≥ 15 px（5%）0 分，中間線性；方向只量 15 px 內的點
DIRECTION_ZERO_DEG = 45.0  # 路線與目標的夾角（不分正反，0–90°）達 45° 時方向 0 分
WEIGHTS = (0.4, 0.35, 0.25)  # 覆蓋、乾淨、方向
V3_STEP_PX = 1.0  # 覆蓋與乾淨的取樣間距，也是乾淨度去重的格子邊長
ROTATION_PENALTY = 5.0  # rank：旋轉 ±ROTATION_REF_DEG 扣 5 分，依角度平方增加
ROTATION_REF_DEG = 15.0
_TIE_PX = 1e-9  # 方向：離取樣點一樣近（差不到這麼多）的目標段都算最近的段
_BUDGET = 2_000_000  # 每批 (取樣點數 × 線段數) 的上限；暫存陣列為 (chunk, M, 2)，記憶體隨 M 成長，故依 M 縮 chunk


@dataclass(frozen=True)
class Score:
    """各版評分共用。direction 只有 v3 有（v1、v2 為 0）；exact 是沒取整數的 overall（排名、校正用），沒給就等於 overall。"""

    coverage: int
    cleanliness: int
    overall: int
    direction: int = 0
    exact: float | None = None

    def __post_init__(self) -> None:
        if self.exact is None:
            object.__setattr__(self, "exact", float(self.overall))


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


def target_extent_m(targets: list[list[LonLat]]) -> float:
    """目標外框（局部平面，北朝上）的長邊公尺：v3 把它縮放到 CANVAS_PX，剪小刺與小圈拿它定圈長上限。"""
    project = _projector(targets)
    stacked = np.vstack([project(line) for line in targets])
    return float((stacked.max(axis=0) - stacked.min(axis=0)).max())


def _sample(line: np.ndarray, step_m: float = SAMPLE_STEP_M) -> np.ndarray:
    """依累積弧長等距重取樣，取樣密度與頂點疏密無關。
    間距是 ≤ step_m 的等分（含兩端點），所以反過來走取到的是同一組點。"""
    s = np.concatenate(([0.0], np.cumsum(np.hypot(*np.diff(line, axis=0).T))))
    if s[-1] == 0.0:
        return line[:1]
    t = np.linspace(0.0, s[-1], math.ceil(s[-1] / step_m) + 1)
    return np.column_stack((np.interp(t, s, line[:, 0]), np.interp(t, s, line[:, 1])))


def _one_per_cell(samples: np.ndarray, cell: float) -> np.ndarray:
    """量化到 cell 的格子，每格留一點，回傳留下的索引。代表點取該格取樣點中座標字典序最小的一點，與騎的順序和方向無關。"""
    cells = np.floor(samples / cell)
    order = np.lexsort((samples[:, 1], samples[:, 0], cells[:, 1], cells[:, 0]))
    sorted_cells = cells[order]
    first = np.ones(len(order), dtype=bool)
    first[1:] = (sorted_cells[1:] != sorted_cells[:-1]).any(axis=1)
    return order[first]


def _visible(line: np.ndarray) -> np.ndarray:
    """地圖上看得到的線：路線每 VISIBLE_STEP_M 取樣、量化到 VISIBLE_CELL_M 的格子，每格留一點。

    看得到的是路線的聯集而不是重數：同一條街騎兩次（任一方向）佔同一批格子，只算一次。
    每格的代表點取該格取樣點中座標字典序最小的一點，與騎的順序和方向無關
    （「沿路線第一個經過的點」會隨方向改變，分數就跟著變）。
    """
    samples = _sample(line, VISIBLE_STEP_M)
    return samples[_one_per_cell(samples, VISIBLE_CELL_M)]


def _segments(lines: list[np.ndarray]) -> tuple[np.ndarray, np.ndarray]:
    starts = np.vstack([line[:-1] for line in lines if len(line) > 1])
    ends = np.vstack([line[1:] for line in lines if len(line) > 1])
    return starts, ends


def _point_segment(p: np.ndarray, starts: np.ndarray, ends: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """p (K, 1, 2) 到每條線段的距離與投影參數 t（夾在 0–1），都是 (K, M)。"""
    ab = ends - starts
    ab_len2 = np.maximum((ab**2).sum(axis=1), 1e-12)
    t = np.clip(((p - starts) * ab).sum(axis=2) / ab_len2, 0.0, 1.0)
    return np.sqrt(((p - (starts + t[..., None] * ab)) ** 2).sum(axis=2)), t


def _nearest(points: np.ndarray, starts: np.ndarray, ends: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """每個點到最近線段的距離、該線段的索引（同距離取索引小的）、最近處是不是線段的端點。
    分批向量化，每批 (點數 × 線段數) 不超過 _BUDGET。"""
    out = np.empty(len(points))
    index = np.empty(len(points), dtype=np.intp)
    at_end = np.empty(len(points), dtype=bool)
    rows = max(1, _BUDGET // len(starts))
    for i in range(0, len(points), rows):
        d, t = _point_segment(points[i : i + rows, None, :], starts, ends)
        k = d.argmin(axis=1)
        index[i : i + rows] = k
        out[i : i + rows] = np.take_along_axis(d, k[:, None], axis=1)[:, 0]
        t_k = np.take_along_axis(t, k[:, None], axis=1)[:, 0]
        at_end[i : i + rows] = (t_k == 0.0) | (t_k == 1.0)
    return out, index, at_end


def _distances(points: np.ndarray, starts: np.ndarray, ends: np.ndarray) -> np.ndarray:
    return _nearest(points, starts, ends)[0]


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
    """目標每 SAMPLE_STEP_M 取樣，到路線折線的距離（v1、v2 相同）。"""
    route_starts, route_ends = _segments([p.route_line])
    return _distances(np.vstack([_sample(line) for line in p.target_lines if len(line) > 1]), route_starts, route_ends)


@dataclass(frozen=True)
class V3Params:
    """v3 可以校正的參數（P2.5 spec §4.4 的調整範圍）；預設是 spec 的初始值，score() 一律用預設。"""

    weights: tuple[float, float, float] = WEIGHTS
    full_credit_px: float = FULL_CREDIT_PX
    zero_credit_px: float = ZERO_CREDIT_PX
    simplify_px: float = SIMPLIFY_PX


V3_DEFAULT = V3Params()


def _canvas(p: _Prepared) -> tuple[list[np.ndarray], np.ndarray]:
    """目標外框（局部平面，北朝上）的中心移到原點、長邊縮放到 CANVAS_PX；路線用同一個轉換。"""
    stacked = np.vstack(p.target_lines)
    low, high = stacked.min(axis=0), stacked.max(axis=0)
    center, k = (low + high) / 2, CANVAS_PX / float((high - low).max())
    return [(line - center) * k for line in p.target_lines], (p.route_line - center) * k


def _without_repeats(line: np.ndarray) -> np.ndarray:
    """拿掉連續重複的點（零長度的一步沒有方向）。"""
    keep = np.ones(len(line), dtype=bool)
    keep[1:] = (line[1:] != line[:-1]).any(axis=1)
    return line[keep]


def _simplify(line: np.ndarray, tolerance: float) -> np.ndarray:
    """Douglas-Peucker：保留兩端點，遞迴保留離弦最遠且超過 tolerance 的點（用堆疊，不遞迴呼叫）。

    距離量到弦這條線段（同 GEOS），不是量到弦所在的直線：沿同一條直線騎過頭再折回來（例如連接線沿著剛畫完的筆畫往回走、
    再超過起點）時，折返點離直線的距離是 0，量到直線的話整段折返會被簡化掉，路線就少了一截。
    """
    if len(line) < 3:
        return line
    keep = np.zeros(len(line), dtype=bool)
    keep[0] = keep[-1] = True
    stack = [(0, len(line) - 1)]
    while stack:
        a, b = stack.pop()
        if b <= a + 1:
            continue
        inner, start, chord = line[a + 1 : b], line[a], line[b] - line[a]
        # 起點等於終點（繞一圈回來）時 t = 0：量到起點的距離
        t = np.clip(((inner - start) @ chord) / max(float(chord @ chord), 1e-12), 0.0, 1.0)
        d = np.hypot(*(inner - start - t[:, None] * chord).T)
        i = int(np.argmax(d))
        if d[i] > tolerance:
            k = a + 1 + i
            keep[k] = True
            stack += [(a, k), (k, b)]
    return line[keep]


def _sample_segments(line: np.ndarray, step: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """同 _sample（依弧長等分、含兩端點），另回傳每個取樣點所在的段：(前一段, 後一段)，line 不能有零長度的段。

    段內的點兩者相同；剛好落在頂點上的點兩者是頂點兩側的段（繞一圈回到起點時，起終點兩側是最後一段與第一段）。
    頂點上的方向不分前後兩段都算（取比較像的那段），反過來騎才會得到同一個分數。
    """
    s = np.concatenate(([0.0], np.cumsum(np.hypot(*np.diff(line, axis=0).T))))
    t = np.linspace(0.0, s[-1], math.ceil(s[-1] / step) + 1)
    last = len(line) - 2
    after = np.clip(np.searchsorted(s, t, side="right") - 1, 0, last)
    before = np.clip(np.searchsorted(s, t, side="left") - 1, 0, last)
    if (line[0] == line[-1]).all():
        before[t == 0.0] = last
        after[t == s[-1]] = 0
    return np.column_stack((np.interp(t, s, line[:, 0]), np.interp(t, s, line[:, 1]))), before, after


def _unit(vectors: np.ndarray) -> np.ndarray:
    return vectors / np.hypot(*vectors.T)[:, None]


def _best_cos(
    points: np.ndarray,
    nearest: tuple[np.ndarray, np.ndarray, np.ndarray],
    route_dirs: tuple[np.ndarray, np.ndarray],
    starts: np.ndarray,
    ends: np.ndarray,
) -> np.ndarray:
    """每個取樣點的路線方向與最近的目標段方向的 |cos|（不分正反，越大越像）。

    最近的目標段不只一段時都算、取最像的：點最近的是目標的頂點時，頂點兩側的段一樣近，只取一段的話選到哪段取決於
    浮點誤差（反過來騎、平移整張圖分數就會跳）。路線方向同理：route_dirs 是取樣點前後兩段（段內兩者相同、頂點上是兩側）。
    nearest 是 _nearest(points, starts, ends) 的結果。一樣近只會發生在最近處是端點時，只有這些點重量一次到每一段的距離。
    """
    distance, index, at_end = nearest
    target_unit = _unit(ends - starts)
    dir_a, dir_b = _unit(route_dirs[0]), _unit(route_dirs[1])

    def cos(u: np.ndarray, a: np.ndarray, b: np.ndarray) -> np.ndarray:
        return np.maximum(np.abs((a * u).sum(axis=-1)), np.abs((b * u).sum(axis=-1)))

    out = cos(target_unit[index], dir_a, dir_b)
    corner = np.flatnonzero(at_end)
    rows = max(1, _BUDGET // len(starts))
    for i in range(0, len(corner), rows):
        k = corner[i : i + rows]
        d, _ = _point_segment(points[k, None, :], starts, ends)
        tied = d <= distance[k, None] + _TIE_PX  # 一定含 nearest（同一個算式算出的同一個值）
        every = cos(target_unit[None, :, :], dir_a[k, None, :], dir_b[k, None, :])
        out[k] = np.where(tied, every, 0.0).max(axis=1)
    return out


def _v3_components(
    targets: list[list[LonLat]], route: list[LonLat], params: V3Params
) -> tuple[float, float, float] | None:
    """v3 的覆蓋、乾淨、方向（0–100，沒取整數）；路線不到兩個不同的點回傳 None。目標太小丟 ValueError。"""
    p = _prepare(targets, route)
    if p is None:
        return None
    target_lines, route_line = _canvas(p)
    route_line = _without_repeats(_simplify(_without_repeats(route_line), params.simplify_px))
    if len(route_line) < 2:
        return None
    target_starts, target_ends = _segments(target_lines)
    nonzero = (target_starts != target_ends).any(axis=1)  # 零長度的段沒有方向；拿掉不影響距離（端點也在相鄰的段上）
    target_starts, target_ends = target_starts[nonzero], target_ends[nonzero]
    if len(target_starts) == 0:
        raise ValueError("目標圖形太小，無法評分")
    route_starts, route_ends = route_line[:-1], route_line[1:]
    full, zero = params.full_credit_px, params.zero_credit_px

    def credit(d: np.ndarray) -> np.ndarray:
        return np.clip((zero - d) / (zero - full), 0.0, 1.0)

    target_samples = np.vstack([_sample(line, V3_STEP_PX) for line in target_lines if len(line) > 1])
    coverage = 100 * credit(_nearest(target_samples, route_starts, route_ends)[0]).mean()

    samples, before, after = _sample_segments(route_line, V3_STEP_PX)
    kept = _one_per_cell(samples, V3_STEP_PX)  # 同一條街來回騎（不論方向）只算一次
    d, nearest, at_end = _nearest(samples[kept], target_starts, target_ends)
    cleanliness = 100 * credit(d).mean()

    near = d < zero
    if not near.any():
        return float(coverage), float(cleanliness), 0.0
    route_vectors = route_ends - route_starts
    cos = _best_cos(
        samples[kept][near], (d[near], nearest[near], at_end[near]),
        (route_vectors[before[kept][near]], route_vectors[after[kept][near]]), target_starts, target_ends,
    )  # fmt: skip
    theta = np.degrees(np.arccos(np.clip(cos, 0.0, 1.0)))  # 不分正反：0–90°
    direction = 100 * np.maximum(0.0, 1 - theta / DIRECTION_ZERO_DEG).mean()
    return float(coverage), float(cleanliness), float(direction)


def score(targets: list[list[LonLat]], route: list[LonLat], params: V3Params = V3_DEFAULT) -> Score:
    """v3（P2.5 spec §4.1）。exact 用三項沒取整數的值加權；overall = round(exact)，三項各自取整數。
    params 只給校正（bench.likeness）用，其餘一律用預設。"""
    components = _v3_components(targets, route, params)
    if components is None:
        return Score(0, 0, 0, 0, 0.0)
    exact = sum(w * c for w, c in zip(params.weights, components, strict=True))
    coverage, cleanliness, direction = components
    return Score(round(coverage), round(cleanliness), round(exact), round(direction), exact)


def rank(s: Score, rotation_deg: float) -> float:
    """搜尋的排名分數（P2.5 spec §4.3）：exact − 5 × (θ / 15°)²，θ 是旋轉換算到 [−180°, 180°)。不顯示成貼合度。"""
    theta = (rotation_deg + 180.0) % 360.0 - 180.0
    return float(s.exact) - ROTATION_PENALTY * (theta / ROTATION_REF_DEG) ** 2


def score_v2(targets: list[list[LonLat]], route: list[LonLat]) -> Score:
    """v2（數字不可改）：乾淨度只看地圖上看得到的線（_visible），0 分距離 = max(對角線 × ZERO_SCORE_RATIO, ZERO_SCORE_MIN_M)。"""
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
