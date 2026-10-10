"""貼合度評分（spec §4.5）：在以目標中心為原點的局部平面（公尺）上量距離。"""

import math
from dataclasses import dataclass

import numpy as np

from engine.routing.router import LonLat

SAMPLE_STEP_M = 10.0
ZERO_SCORE_RATIO = 0.05  # 平均偏離達對角線 5% 時為 0 分；由基準測試集調整
_CHUNK = 2048  # 每批計算的取樣點數，限制 N×M 矩陣的記憶體


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


def _sample(line: np.ndarray) -> np.ndarray:
    parts = [line[:1]]
    for a, b in zip(line, line[1:]):
        n = max(1, math.ceil(float(np.hypot(*(b - a))) / SAMPLE_STEP_M))
        t = np.arange(1, n + 1)[:, None] / n
        parts.append(a + (b - a) * t)
    return np.vstack(parts)


def _segments(lines: list[np.ndarray]) -> tuple[np.ndarray, np.ndarray]:
    starts = np.vstack([line[:-1] for line in lines if len(line) > 1])
    ends = np.vstack([line[1:] for line in lines if len(line) > 1])
    return starts, ends


def _distances(points: np.ndarray, starts: np.ndarray, ends: np.ndarray) -> np.ndarray:
    ab = ends - starts
    ab_len2 = np.maximum((ab**2).sum(axis=1), 1e-12)
    out = np.empty(len(points))
    for i in range(0, len(points), _CHUNK):
        p = points[i : i + _CHUNK, None, :]
        t = np.clip(((p - starts) * ab).sum(axis=2) / ab_len2, 0.0, 1.0)
        nearest = starts + t[..., None] * ab
        out[i : i + _CHUNK] = np.sqrt(((p - nearest) ** 2).sum(axis=2)).min(axis=1)
    return out


def _component(distances: np.ndarray, diagonal: float) -> int:
    e = (distances.mean() + np.percentile(distances, 90)) / 2 / diagonal
    return round(100 * max(0.0, 1 - e / ZERO_SCORE_RATIO))


def score(targets: list[list[LonLat]], route: list[LonLat]) -> Score:
    project = _projector(targets)
    target_lines = [project(line) for line in targets]
    stacked = np.vstack(target_lines)
    diagonal = float(np.hypot(*(stacked.max(axis=0) - stacked.min(axis=0))))
    if diagonal < 1.0:
        raise ValueError("目標圖形太小，無法評分")
    if len(route) < 2:
        return Score(0, 0, 0)
    route_line = project(route)
    target_starts, target_ends = _segments(target_lines)
    route_starts, route_ends = _segments([route_line])
    coverage = _component(
        _distances(np.vstack([_sample(line) for line in target_lines if len(line) > 1]), route_starts, route_ends),
        diagonal,
    )
    cleanliness = _component(_distances(_sample(route_line), target_starts, target_ends), diagonal)
    return Score(coverage, cleanliness, round((coverage + cleanliness) / 2))
