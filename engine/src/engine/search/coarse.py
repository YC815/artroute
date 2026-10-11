"""粗篩（spec §4.6 第 1–3 步）：在道路距離場上評估大量擺放，留下彼此差異夠大、成本最低的幾個，再微調。

成本（越低越好）：圖形上等距取樣，每個取樣點的
    d = min(到最近道路的距離, T) / T                      T = 這個大小的吸附搜尋半徑（與 /match 的預設半徑相同）× 3
    c = d + DIRECTION_WEIGHT × (1 − d) × |sin(圖形切線與道路方向的夾角)|
取平均。d 衡量「這裡有沒有路」，方向項讓「沿著路畫」勝過「橫跨很多條路」（棋盤街區裡任何位置都離路很近）。
兩階段：A 對所有擺放只算距離項、取樣少；B 對 A 的前 KEEP_A 名算完整成本。
"""

import math
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from engine.matching.deadline import Deadline
from engine.matching.service import RADIUS_RATIO
from engine.routing.tiles import MAX_SEARCH_RADIUS_M, MIN_SEARCH_RADIUS_M
from engine.search.field import RoadField
from engine.search.geometry import rotate_scale
from engine.search.types import NormStroke, SearchParams

MIN_SIZE_M = 200.0  # 與 web 的 transform.ts MIN_SIZE_M／MAX_SIZE_M 相同：搜尋不會回傳網站拖不出來的大小
MAX_SIZE_M = 20_000.0
# 路線長度 ≈ 大小 × (圖形長 × 繞路係數 + 部件間距)。P1.5 bench 的 length/target 中位數 1.71（含連接線），
# 這裡把連接線另外估（部件間距），繞路係數取它兩側的寬範圍；實際公里數由精算量，超出就依比例修正一次（fine.py）
DETOUR_LO, DETOUR_HI = 1.15, 1.8
SIZE_LEVELS = 6  # spec §4.6：大小 6 級
ANGLE_STEP_DEG = 10.0  # spec §4.6：角度每 10°
POSITION_STEP_M = 150.0  # spec §4.6：位置每 150 m
SAMPLES_A = 48
SAMPLES_B = 256
KEEP_A = 2000  # 進入 B 的擺放數
DIRECTION_WEIGHT = 0.5
NMS_KEEP = 30  # spec §4.6：NMS 留 30 個
FINE_KEEP = 16  # 微調後交給精算的上限（精算至少跑 K=10 個，見 fine.py）
CLOSED_START_POINTS = 16  # 封閉筆畫取幾個點當「可以當起點」的位置
# 兩個擺放算「差不多」的門檻（NMS）：大小差不到 15%、角度差不到 15°、中心距離不到大小的 25%
SIMILAR_SIZE_RATIO = 1.15
SIMILAR_ANGLE_DEG = 15.0
SIMILAR_CENTER_RATIO = 0.25
# 微調（pattern search）：初始步長與停止條件
REFINE_POS_M, REFINE_ANGLE_DEG, REFINE_SIZE_RATIO = 40.0, 4.0, 1.04
REFINE_MIN_POS_M = 5.0
REFINE_MAX_ROUNDS = 30


@dataclass(frozen=True)
class ShapeModel:
    """正規化圖形的取樣與統計量（每個搜尋算一次）。"""

    starts: np.ndarray  # (M, 2) 線段起點（所有筆畫，封閉筆畫含收尾那一段）
    ends: np.ndarray
    cumulative: np.ndarray  # (M,) 每段結束時的累積長度
    unit_length: float  # 圖形總長（正規化單位）
    gap_unit: float  # 部件間距的估計（正規化單位）
    radius: float  # 離原點最遠的點
    diagonal: float  # 外框對角線
    start_points: np.ndarray  # (S, 2) 可以當路線起點的位置：開放筆畫的兩端、封閉筆畫上的點

    def samples(self, count: int) -> tuple[np.ndarray, np.ndarray]:
        """沿全部筆畫等距取 count 個點，回傳 (點, 單位切線)。"""
        t = (np.arange(count) + 0.5) * (self.unit_length / count)
        index = np.minimum(np.searchsorted(self.cumulative, t), len(self.cumulative) - 1)
        direction = self.ends[index] - self.starts[index]
        length = np.hypot(*direction.T)
        before = self.cumulative[index] - length
        u = np.clip((t - before) / length, 0.0, 1.0)
        return self.starts[index] + direction * u[:, None], direction / length[:, None]


def shape_model(strokes: list[NormStroke]) -> ShapeModel:
    lines = [np.asarray(s.points + ([s.points[0]] if s.closed else []), dtype=float) for s in strokes]
    starts = np.vstack([line[:-1] for line in lines])
    ends = np.vstack([line[1:] for line in lines])
    keep = np.hypot(*(ends - starts).T) > 0
    starts, ends = starts[keep], ends[keep]
    lengths = np.hypot(*(ends - starts).T)
    points = np.vstack(lines)
    start_points = []
    for stroke, line in zip(strokes, lines):
        if stroke.closed:
            step = max(1, len(line) // CLOSED_START_POINTS)
            start_points.append(line[::step])
        else:
            start_points.append(line[[0, -1]])
    span = points.max(axis=0) - points.min(axis=0)
    return ShapeModel(
        starts=starts,
        ends=ends,
        cumulative=np.cumsum(lengths),
        unit_length=float(lengths.sum()),
        gap_unit=_gap_estimate(strokes, lines),
        radius=float(np.hypot(*points.T).max()),
        diagonal=float(np.hypot(*span)),
        start_points=np.vstack(start_points),
    )


def _gap_estimate(strokes: list[NormStroke], lines: list[np.ndarray]) -> float:
    """部件之間的連接距離估計：從第 0 筆出發，每次接最近的一筆（與串接器的貪婪規劃同一個想法）。"""
    remaining = list(range(1, len(lines)))
    position = lines[0][-1] if not strokes[0].closed else lines[0][0]
    total = 0.0
    while remaining:
        entries = {i: (lines[i] if strokes[i].closed else lines[i][[0, -1]]) for i in remaining}
        nearest, distance = min(
            ((i, float(np.hypot(*(points - position).T).min())) for i, points in entries.items()), key=lambda x: x[1]
        )
        total += distance
        remaining.remove(nearest)
        line = lines[nearest]
        if strokes[nearest].closed:
            position = line[np.argmin(np.hypot(*(line - position).T))]
        else:
            ends = line[[0, -1]]
            position = ends[1] if np.hypot(*(ends[0] - position)) <= np.hypot(*(ends[1] - position)) else ends[0]
    return total


def size_bounds(shape: ShapeModel, min_km: float, max_km: float) -> tuple[float, float]:
    """依公里數範圍反推大小（正規化 1 單位的公尺數）的上下限，夾在 MIN_SIZE_M–MAX_SIZE_M。"""
    low = min_km * 1000 / (shape.unit_length * DETOUR_HI + shape.gap_unit)
    high = max_km * 1000 / (shape.unit_length * DETOUR_LO + shape.gap_unit)
    low, high = (min(MAX_SIZE_M, max(MIN_SIZE_M, v)) for v in (low, high))
    return low, max(low, high)


def size_levels(low: float, high: float) -> np.ndarray:
    if high / low < 1.02:
        return np.array([math.sqrt(low * high)])
    return np.geomspace(low, high, SIZE_LEVELS)


def angle_levels(low: float, high: float) -> np.ndarray:
    """旋轉範圍內每 10° 一個角度；範圍是整圈時不重複頭尾。"""
    span = high - low
    if span >= 360 - 1e-9:
        return low + np.arange(round(360 / ANGLE_STEP_DEG)) * ANGLE_STEP_DEG
    if span <= 0:
        return np.array([low])
    return np.linspace(low, high, int(round(span / ANGLE_STEP_DEG)) + 1)


# 截斷距離 = 吸附搜尋半徑 × 這個倍數。只用吸附半徑時，大小差一級（約 26%）就讓所有取樣點都超出截斷、成本一片平坦，
# 微調找不到方向（合成測試：1 km 的正方形環，最好的擺放偏 10% 大小、成本只比沒有環時好 0.1）
TOLERANCE_FACTOR = 3.0


def snap_tolerance_m(shape: ShapeModel, size: float) -> float:
    """這個大小的截斷距離：吸附搜尋半徑（與 matching.service.default_search_radius 同一公式）的 TOLERANCE_FACTOR 倍。"""
    radius = min(MAX_SEARCH_RADIUS_M, max(MIN_SEARCH_RADIUS_M, shape.diagonal * size * RADIUS_RATIO))
    return float(radius * TOLERANCE_FACTOR)


@dataclass(frozen=True)
class Placed:
    """粗篩階段的擺放：中心在局部平面（東, 北），尚未轉回經緯度。"""

    east: float
    north: float
    size: float
    rotation: float
    cost: float


@dataclass(frozen=True)
class Coarse:
    candidates: list[Placed]  # 成本由低到高，彼此差異夠大
    evaluated: int  # 評估過的擺放數


class _Scorer:
    """同一個距離場、同一個圖形的成本函式（每個大小的截斷距離不同）。"""

    def __init__(self, field: RoadField, shape: ShapeModel, radius_m: float) -> None:
        self.field = field
        self.shape = shape
        self.radius_m = radius_m
        self.points_b, self.tangents_b = shape.samples(SAMPLES_B)
        self.points_a, _ = shape.samples(SAMPLES_A)

    def start_ok(self, east: np.ndarray, north: np.ndarray, size: float, rotation: float) -> np.ndarray:
        """至少有一個可以當起點的位置落在圈內（spec §4.6：起點條件放寬）。"""
        offsets = rotate_scale(self.shape.start_points, size, rotation)
        dx = east[:, None] + offsets[None, :, 0]
        dy = north[:, None] + offsets[None, :, 1]
        return (dx * dx + dy * dy).min(axis=1) <= self.radius_m**2

    def _direction(self, size: float, rotation: float) -> tuple[np.ndarray, np.ndarray]:
        tangent = rotate_scale(self.tangents_b, 1.0, rotation)
        psi = np.arctan2(tangent[:, 1], tangent[:, 0])
        return np.cos(2 * psi), np.sin(2 * psi)

    def cost_b(self, east: np.ndarray, north: np.ndarray, size: float, rotation: float, bilinear: bool) -> np.ndarray:
        """完整成本（距離＋方向），east／north 是 (N,) 的擺放中心。"""
        offsets = rotate_scale(self.points_b, size, rotation)
        px = east[:, None] + offsets[None, :, 0]
        py = north[:, None] + offsets[None, :, 1]
        flat = self.field.flat_index(px, py)
        if bilinear:
            distance = self.field.bilinear_distance(px, py)
        else:
            distance = self.field.distance.ravel()[flat]
        tolerance = snap_tolerance_m(self.shape, size)
        d = np.minimum(distance, tolerance) / tolerance
        cos2psi, sin2psi = self._direction(size, rotation)
        cos2delta = cos2psi[None, :] * self.field.cos2.ravel()[flat] + sin2psi[None, :] * self.field.sin2.ravel()[flat]
        sin_delta = np.sqrt(np.clip((1 - cos2delta) / 2, 0.0, 1.0))
        return (d + DIRECTION_WEIGHT * (1 - d) * sin_delta).mean(axis=1)


def _lattice(reach: float) -> tuple[np.ndarray, np.ndarray]:
    """以圓心為原點、每 POSITION_STEP_M 一格、離圓心不超過 reach 的位置。"""
    k = int(reach // POSITION_STEP_M)
    axis = np.arange(-k, k + 1) * POSITION_STEP_M
    east, north = np.meshgrid(axis, axis)
    inside = east**2 + north**2 <= reach**2
    return east[inside].astype(float), north[inside].astype(float)


def field_half_extent(params: SearchParams, shape: ShapeModel) -> float:
    """距離場要涵蓋的半寬：圖形中心最遠可以在 R + 大小 × 半徑，圖形本身再延伸大小 × 半徑，外加截斷距離。"""
    high = size_bounds(shape, params.min_km, params.max_km)[1]
    return params.radius_m + 2 * high * shape.radius + MAX_SEARCH_RADIUS_M * TOLERANCE_FACTOR + 2 * POSITION_STEP_M


def _similar(a: Placed, b: Placed) -> bool:
    if max(a.size, b.size) / min(a.size, b.size) >= SIMILAR_SIZE_RATIO:
        return False
    if abs((a.rotation - b.rotation + 180) % 360 - 180) >= SIMILAR_ANGLE_DEG:
        return False
    return math.hypot(a.east - b.east, a.north - b.north) < SIMILAR_CENTER_RATIO * min(a.size, b.size)


def suppress(candidates: list[Placed], keep: int) -> list[Placed]:
    """非極大值抑制：依成本由低到高，和已留下的都不「差不多」才留下，最多 keep 個。"""
    kept: list[Placed] = []
    for candidate in sorted(candidates, key=lambda c: c.cost):
        if all(not _similar(candidate, other) for other in kept):
            kept.append(candidate)
            if len(kept) == keep:
                break
    return kept


def _refine(scorer: _Scorer, start: Placed, sizes: tuple[float, float], angles: tuple[float, float] | None) -> Placed:
    """座標輪流試 ± 一步（位置、角度、大小），有改善就走，沒有就把步長減半；用雙線性距離，所以小步也量得到差別。
    角度限制在旋轉範圍內（angles 為 None 代表整圈），大小限制在 sizes 內，起點條件一直要成立。"""

    def cost(p: Placed) -> float:
        east, north = np.array([p.east]), np.array([p.north])
        if not scorer.start_ok(east, north, p.size, p.rotation)[0]:
            return math.inf
        return float(scorer.cost_b(east, north, p.size, p.rotation, bilinear=True)[0])

    best = Placed(start.east, start.north, start.size, start.rotation, cost(start))
    step_pos, step_angle, step_size = REFINE_POS_M, REFINE_ANGLE_DEG, REFINE_SIZE_RATIO
    for _ in range(REFINE_MAX_ROUNDS):
        if step_pos < REFINE_MIN_POS_M:
            break
        moves = [(step_pos, 0, 0, 1), (-step_pos, 0, 0, 1), (0, step_pos, 0, 1), (0, -step_pos, 0, 1),
                 (0, 0, step_angle, 1), (0, 0, -step_angle, 1), (0, 0, 0, step_size), (0, 0, 0, 1 / step_size)]
        improved = False
        for de, dn, da, ds in moves:
            rotation = best.rotation + da
            size = best.size * ds
            if angles is not None and not angles[0] - 1e-9 <= rotation <= angles[1] + 1e-9:
                continue
            if not sizes[0] - 1e-9 <= size <= sizes[1] + 1e-9:
                continue
            trial = Placed(best.east + de, best.north + dn, size, rotation, 0.0)
            value = cost(trial)
            if value < best.cost - 1e-9:
                best = Placed(trial.east, trial.north, trial.size, trial.rotation, value)
                improved = True
        if not improved:
            step_pos, step_angle, step_size = step_pos / 2, step_angle / 2, math.sqrt(step_size)
    return best


def coarse_search(
    params: SearchParams,
    shape: ShapeModel,
    field: RoadField,
    deadline: Deadline,
    progress: Callable[[str, int, int], None] = lambda stage, done, total: None,
) -> Coarse:
    scorer = _Scorer(field, shape, params.radius_m)
    low, high = size_bounds(shape, params.min_km, params.max_km)
    sizes = size_levels(low, high)
    angles = angle_levels(params.rotation_min_deg, params.rotation_max_deg)
    full_turn = params.rotation_max_deg - params.rotation_min_deg >= 360 - 1e-9
    dcost_cache: dict[float, np.ndarray] = {}
    pass_a: list[tuple[np.ndarray, int, int, np.ndarray, np.ndarray]] = []
    evaluated = 0
    for i, size in enumerate(sizes):
        deadline.check()
        progress("coarse", i, len(sizes))
        tolerance = snap_tolerance_m(shape, size)
        dcost = dcost_cache.setdefault(
            size, (np.minimum(field.distance, tolerance) / tolerance).astype(np.float32).ravel()
        )
        lattice_e, lattice_n = _lattice(params.radius_m + size * float(np.hypot(*shape.start_points.T).max()))
        for j, rotation in enumerate(angles):
            ok = scorer.start_ok(lattice_e, lattice_n, size, rotation)
            east, north = lattice_e[ok], lattice_n[ok]
            if not len(east):
                continue
            offsets = rotate_scale(scorer.points_a, size, rotation)
            flat = field.flat_index(east[:, None] + offsets[None, :, 0], north[:, None] + offsets[None, :, 1])
            pass_a.append((dcost[flat].mean(axis=1), i, j, east, north))
            evaluated += len(east)
    progress("coarse", len(sizes), len(sizes))
    if not pass_a:
        return Coarse([], 0)

    costs = np.concatenate([p[0] for p in pass_a])
    group = np.concatenate([np.full(len(p[0]), k) for k, p in enumerate(pass_a)])
    within = np.concatenate([np.arange(len(p[0])) for p in pass_a])
    top = np.argpartition(costs, min(KEEP_A, len(costs)) - 1)[:KEEP_A]
    survivors: list[Placed] = []
    for k in np.unique(group[top]):
        deadline.check()
        _, i, j, east, north = pass_a[k]
        rows = within[top[group[top] == k]]
        values = scorer.cost_b(east[rows], north[rows], float(sizes[i]), float(angles[j]), bilinear=False)
        survivors.extend(
            Placed(float(e), float(n), float(sizes[i]), float(angles[j]), float(v))
            for e, n, v in zip(east[rows], north[rows], values)
        )

    picked = suppress(survivors, NMS_KEEP)
    rotation_range = None if full_turn else (params.rotation_min_deg, params.rotation_max_deg)
    refined: list[Placed] = []
    for k, candidate in enumerate(picked):
        deadline.check()
        progress("refine", k, len(picked))
        refined.append(_refine(scorer, candidate, (low, high), rotation_range))
    progress("refine", len(picked), len(picked))
    return Coarse(suppress([c for c in refined if math.isfinite(c.cost)], FINE_KEEP), evaluated)
