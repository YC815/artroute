"""整條管線（match_shape）的性質測試，不需要 Valhalla：隨機的多筆畫圖形（開放、封閉混合），
假 router 同時帶著會讓組裝出事的各種情況。固定 seed，結果可重現。

假 router（FuzzRouter）：
- 不能轉彎的路口（TurnBlockRouter）；trace 吸附到旁邊 0／0.4／2.5 m 的路；
- 進不去的端點（route 的終點停在 7 m 外，像網站預設文字筆畫 12 的 ramp，決策 A7 的情境）、出不來的端點；
- 島（trace、route 都找不到路）；
- 一半的直線 link 是 L 形（link 本身帶一個轉彎）。

每個有輸出的案例都要滿足三件事：
1. 每個接點都能直接騎：用跨段的探針（tail_points／head_points）重量，跟 bench 的 bad_turns 同一把尺；
2. 相鄰路段沒有超過 JOIN_TOLERANCE_M 的缺口（match_shape 自己的連續性檢查丟 DiscontinuousRoute 也算缺口）；
3. 沒有無聲遺失：每個筆畫要嘛在路線裡（stroke／detour 段），要嘛有警告。

另外釘住覆蓋下限（反向、封閉圈旋轉、A7 擱置後補接、補接失敗被丟），避免日後改動讓測試變空洞。
"""

import random
import zlib
from collections import Counter

import pytest

from engine.matching import compose as compose_module
from engine.matching.deadline import Deadline
from engine.matching.service import (
    AllStrokesFailed,
    DiscontinuousRoute,
    StrokeInput,
    TargetTooSmall,
    match_shape,
)
from engine.matching.types import JOIN_TOLERANCE_M
from engine.routing.geo import haversine_m, polyline_length_m
from engine.routing.router import RouteResult, RoutingError
from turn_router import TurnBlockRouter, east, every_junction_rideable, north

ORIGIN = (121.507, 25.05)
DIRECTIONS = [(1, 0), (0, 1), (-1, 0), (0, -1)]  # 東、北、西、南
SEEDS = range(2_000)  # 約 2 秒
# 覆蓋下限：2,000 個 seed 實測有輸出 1,997 個、帶不能轉彎路口 1,522、反向 908、封閉圈旋轉 448、
# A7 補接 441（接上 385、被丟 56）、有警告 521、有 detour 1,192；
# 下限取實測的三分之一以內，只擋「測試變空洞」，不是效能指標
MIN_OUTPUTS = 1_500
MIN_WITH_BLOCKED_CORNER = 500
MIN_REVERSED = 300
MIN_ROTATED = 150
MIN_RETRY_JOINED = 120
MIN_RETRY_DROPPED = 15
MIN_WITH_WARNING = 170
MIN_WITH_DETOUR = 400


def pick(a, b, modulo: int) -> int:
    """由兩個點決定的確定性選擇（不用 hash()：不同 Python 版本的 tuple hash 不一樣）。"""
    return zlib.crc32(repr((a, b)).encode()) % modulo


class FuzzRouter(TurnBlockRouter):
    def __init__(self, corners, no_entry, no_exit, islands) -> None:
        super().__init__(corners[0] if corners else None, also_blocked=tuple(corners[1:]))
        self.no_entry, self.no_exit, self.islands = no_entry, no_exit, islands

    def trace(self, points, *, search_radius_m):
        if any(haversine_m(p, i) < 15 for i in self.islands for p in points):
            raise RoutingError("island")
        result = super().trace(points, search_radius_m=search_radius_m)
        shift = (0.0, 0.4, 2.5)[pick(points[0], points[-1], 3)]  # 吸附到旁邊的路
        return RouteResult(points=[north(p, shift) for p in result.points], length_km=result.length_km)

    def route(self, start, end, *, shortest=False):
        if any(haversine_m(start, i) < 15 or haversine_m(end, i) < 15 for i in self.islands):
            raise RoutingError("island")
        if any(haversine_m(end, p) < 15 for p in self.no_entry):  # 進不去：route 終點停在 7 m 外
            beside = north(end, 7)
            self.route_calls.append((start, end))
            return RouteResult(points=[start, beside], length_km=haversine_m(start, beside) / 1000)
        if any(haversine_m(start, p) < 15 for p in self.no_exit):  # 出不來：route 從 7 m 外開始
            beside = north(start, 7)
            self.route_calls.append((start, end))
            return RouteResult(points=[beside, end], length_km=haversine_m(beside, end) / 1000)
        result = super().route(start, end, shortest=shortest)
        if len(result.points) == 2 and pick(start, end, 2) and haversine_m(start, end) > 3:
            corner = (end[0], start[1])  # L 形路線：link 本身帶一個轉彎
            points = [start, corner, end]
            return RouteResult(points=points, length_km=polyline_length_m(points) / 1000)
        return result


def polyline(rng: random.Random, start):
    points, heading = [start], rng.randrange(4)
    for _ in range(rng.randint(2, 6)):
        if rng.random() < 0.6:
            heading = (heading + rng.choice([1, 3])) % 4
        dx, dy = DIRECTIONS[heading]
        meters = rng.choice([25, 40, 60, 90, 150])
        points.append(north(east(points[-1], dx * meters), dy * meters))
    return points


def make_case(seed: int):
    rng = random.Random(seed)
    strokes = []
    for _ in range(rng.randint(1, 5)):
        start = north(east(ORIGIN, rng.uniform(0, 600)), rng.uniform(0, 600))
        points = polyline(rng, start)
        strokes.append(StrokeInput(points, closed=rng.random() < 0.3 and len(points) >= 4))
    vertices = [p for s in strokes for p in s.points]
    corners = rng.sample(vertices, k=min(len(vertices), rng.randint(0, 3)))
    no_entry = rng.sample(vertices, k=min(len(vertices), rng.randint(0, 2)))
    no_exit = rng.sample(vertices, k=min(len(vertices), rng.randint(0, 1)))
    islands = rng.sample(vertices, k=min(len(vertices), rng.randint(0, 1))) if rng.random() < 0.3 else []
    return strokes, (corners, no_entry, no_exit, islands)


def has_gap(segments) -> bool:
    return any(haversine_m(a.points[-1], b.points[0]) > JOIN_TOLERANCE_M for a, b in zip(segments, segments[1:]))


@pytest.fixture(scope="module")
def sweep() -> dict:
    """跑完所有 seed 一次，兩個測試共用。compose 的 _body／_retry 包一層只用來計數，不改行為。"""
    stats: dict = {"counts": Counter(), "bad_turn": [], "gap": [], "silent": []}
    counts = stats["counts"]
    real_body, real_retry = compose_module._body, compose_module._retry

    def body(part, option, rematch, index):
        out = real_body(part, option, rematch, index)
        counts["reversed"] += int(not part.closed and option.reverse and out is not part.segments)
        counts["rotated"] += int(part.closed and option.rotation > 0)
        return out

    def retry(*args, **kwargs):
        joined = real_retry(*args, **kwargs)
        counts["retry_joined" if joined is not None else "retry_dropped"] += 1
        return joined

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(compose_module, "_body", body)
        patch.setattr(compose_module, "_retry", retry)
        for seed in SEEDS:
            strokes, config = make_case(seed)
            try:
                result = match_shape(FuzzRouter(*config), strokes, search_radius_m=None, deadline=Deadline(60))
            except (AllStrokesFailed, TargetTooSmall):
                continue
            except DiscontinuousRoute:
                stats["gap"].append(seed)
                continue
            counts["outputs"] += 1
            counts["with_blocked_corner"] += int(bool(config[0]))
            counts["with_warning"] += int(bool(result.warnings))
            counts["with_detour"] += int(any(s.kind == "detour" for s in result.segments))
            if has_gap(result.segments):
                stats["gap"].append(seed)
            if not every_junction_rideable(FuzzRouter(*config), result.segments):
                stats["bad_turn"].append(seed)
            present = {s.stroke_index for s in result.segments if s.kind in ("stroke", "detour")}
            warned = {w.stroke_index for w in result.warnings}
            missing = set(range(len(strokes))) - present - warned
            if missing:
                stats["silent"].append((seed, sorted(missing)))
    return stats


def test_match_shape_never_emits_an_unrideable_junction_a_gap_or_a_silent_loss(sweep: dict) -> None:
    assert sweep["bad_turn"] == [], "有輸出 Valhalla 不會直接騎的接點"
    assert sweep["gap"] == [], "相鄰路段之間有缺口"
    assert sweep["silent"] == [], "筆畫既不在路線裡、也沒有警告"


def test_fuzz_exercises_the_assembly_paths_it_guards(sweep: dict) -> None:
    counts = sweep["counts"]
    assert counts["outputs"] >= MIN_OUTPUTS
    assert counts["with_blocked_corner"] >= MIN_WITH_BLOCKED_CORNER
    assert counts["reversed"] >= MIN_REVERSED
    assert counts["rotated"] >= MIN_ROTATED
    assert counts["retry_joined"] >= MIN_RETRY_JOINED  # A7：擱置的部件後來接上
    assert counts["retry_dropped"] >= MIN_RETRY_DROPPED  # A7：補接仍失敗而被丟掉
    assert counts["with_warning"] >= MIN_WITH_WARNING
    assert counts["with_detour"] >= MIN_WITH_DETOUR
