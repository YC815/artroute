"""自動搜尋（spec §4.6）：粗篩（距離場）→ NMS 與微調 → 精算（完整吸附）→ 前 5 名。"""

from collections.abc import Callable, Sequence

import numpy as np

from engine.matching.deadline import Deadline
from engine.matching.types import MatchRouter
from engine.search.coarse import coarse_search, field_half_extent, shape_model
from engine.search.field import build_field
from engine.search.fine import TOP_N, FineStage
from engine.search.geometry import EARTH_RADIUS_M, RAD, from_local, lonlat_to_local
from engine.search.roads import RoadIndex
from engine.search.types import Best, Placement, Progress, SearchEventOut, SearchOutcome, SearchParams

NO_FIT_NOTICE = "沒有候選符合公里數範圍，列出最接近的；可以放寬公里數或加大半徑"
FEW_NOTICE = "只找到 {n} 個起點在圈內的擺法；可以加大半徑或放寬條件"
NONE_NOTICE = "圈內找不到能畫出這個圖形的道路；可以加大半徑或換個地方"


def _local_segments(roads: RoadIndex, center: tuple[float, float], half_extent_m: float) -> np.ndarray:
    """距離場範圍內的道路，轉成以圓心為原點的局部平面。"""
    lat_pad = half_extent_m / EARTH_RADIUS_M / RAD
    lon_pad = half_extent_m / (EARTH_RADIUS_M * np.cos((abs(center[1]) + lat_pad) * RAD)) / RAD
    raw = roads.segments(center[0] - lon_pad, center[1] - lat_pad, center[0] + lon_pad, center[1] + lat_pad)
    if not len(raw):
        return np.empty((0, 4))
    return np.hstack((lonlat_to_local(center, raw[:, :2]), lonlat_to_local(center, raw[:, 2:])))


def _notice(count: int, qualified: int) -> str | None:
    if count == 0:
        return NONE_NOTICE
    if qualified == 0:
        return NO_FIT_NOTICE
    if count < TOP_N:
        return FEW_NOTICE.format(n=count)
    return None


def run_search(
    params: SearchParams,
    routers: Sequence[MatchRouter],
    roads: RoadIndex,
    deadline: Deadline,
    emit: Callable[[SearchEventOut], None],
) -> SearchOutcome:
    shape = shape_model(params.strokes)
    half_extent = field_half_extent(params, shape)
    deadline.check()
    field = build_field(_local_segments(roads, params.center, half_extent), half_extent)
    coarse = coarse_search(
        params, shape, field, deadline, progress=lambda stage, done, total: emit(Progress(stage, done, total))
    )
    placements = [
        Placement(from_local(params.center, c.east, c.north), c.size, c.rotation) for c in coarse.candidates
    ]
    fine = FineStage(
        params,
        routers,
        deadline,
        on_progress=lambda done, total: emit(Progress("match", done, total)),
        on_best=lambda candidate: emit(Best(candidate)),
    )
    candidates = fine.run(placements)
    return SearchOutcome(
        candidates=candidates,
        coarse_candidates=coarse.evaluated,
        evaluated=fine.done,
        notice=_notice(len(candidates), sum(c.length_ok for c in candidates)),
        evaluations=list(fine.evaluations),
        ranked=list(fine.ranked),
    )
