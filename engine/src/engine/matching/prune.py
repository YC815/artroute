"""剪小刺與小圈（P2.5 spec §5.2、主 spec §8 第 37 列）：路線兩次經過同一點時，剪掉中間多繞的那段。

候選：攤平的路線（compose.flat，相鄰段共用的接點只算一次）上，相距 ≤ JOIN_TOLERANCE_M（haversine）的兩點 (i, j)，
中間那段沿路線的長度在 MIN_LOOP_M 到 MAX_LOOP_RATIO × 目標外框長邊之間。由短到長逐一嘗試（同長依 i），每剪一次重新找。
每個候選依序檢查，任何一項沒過就不剪、換下一個：
1. 不用 Valhalla：剪完的新接點相距 ≤ JOIN_TOLERANCE_M，新接點不是 rideable_strict 不用 route 就會拒絕的轉彎
   （原地迴轉、一側量不到 JOIN_TOLERANCE_M、探針折回）。
2. v3 不下降：拿評分判斷這段有沒有畫到原圖（目標本身有那個圈，剪了覆蓋就掉）。
3. 轉彎：新接點用 rideable_strict（量不出來的一律不放行：拿掉的可能正是讓路線能迴轉的那一圈）；
   沿路線離新接點不到 TURN_PROBE_M 的既有接點，探針會伸進新幾何，用 rideable 重量（同 join）。
剪法只切 Valhalla 給的幾何：保留 i 之前（含 i）與 j 之後（含 j），所以輸出的點全是原本的點、新接點 ≤ JOIN_TOLERANCE_M，
不會多出直線，騎的方向也與原本相同（不會逆向）。
期限：每個候選前 deadline.check()；剩不到 PRUNE_RESERVE_S 就停、回傳目前結果；
通過第 1 項的候選（要算 v3 或呼叫 route 的）最多 MAX_PRUNE_ATTEMPTS 個，不論成敗。
"""

import math
from dataclasses import dataclass
from itertools import chain

import numpy as np
from scipy.spatial import cKDTree

from engine.matching.compose import Pos, flat, route_points
from engine.matching.deadline import Deadline
from engine.matching.join import after_neighbours, before_neighbours, cut, junction_arms, junction_ok
from engine.matching.score import score
from engine.matching.turns import TURN_PROBE_M, strict_needs_no_route_to_reject
from engine.matching.types import JOIN_TOLERANCE_M, MatchRouter, Segment
from engine.routing.geo import haversine_m
from engine.routing.router import LonLat

MIN_LOOP_M = 2 * TURN_PROBE_M  # 比這短的圈縮圖上看不到，剪了還多一個接點
MAX_LOOP_RATIO = 0.15  # 圈長上限 = 目標外框長邊 × 這個比例：更大的圈可能是圖形本身的一部分，交給規劃器
PRUNE_RESERVE_S = 0.3  # 期限剩不到這麼多就不再剪：評分與回應也要時間
MAX_PRUNE_ATTEMPTS = 40  # 要算 v3 或呼叫 route 的候選最多這麼多個（不論成敗）
_QUERY_SLACK = 1.01  # 局部平面的比例尺與 haversine 差不到 1%：KD-tree 多抓一點，一律以 haversine 為準


@dataclass(frozen=True)
class _Loop:
    """攤平後第 start 點到第 end 點（start < end，平面近似下兩點相距不到 JOIN_TOLERANCE_M）之間沿路線 length_m 公尺。"""

    start: int
    end: int
    length_m: float


@dataclass(frozen=True)
class _Pruned:
    """剪完的路線；新的邊界在 segments[junction] 與 segments[junction + 1] 之間
    （junction = -1：剪掉的是路線開頭；junction = len(segments) - 1：剪掉的是結尾，這兩種沒有新接點）。"""

    segments: list[Segment]
    junction: int


def _candidates(points: list[LonLat], max_m: float) -> list[_Loop]:
    """路線兩次經過同一點的 (i, j)：由短到長、同長依 i、j 排序（確定性）。
    距離是局部平面的近似（而且多抓 _QUERY_SLACK）：接點是否 ≤ JOIN_TOLERANCE_M 一律由 _cut_loop 以 haversine 確認。"""
    if len(points) < 3:
        return []
    arr = np.asarray(points, dtype=float)
    lat0 = float(arr[:, 1].mean())
    kx, ky = 111_320.0 * math.cos(math.radians(lat0)), 110_574.0
    xy = np.column_stack(((arr[:, 0] - arr[0, 0]) * kx, (arr[:, 1] - arr[0, 1]) * ky))
    pairs = cKDTree(xy).query_pairs(r=JOIN_TOLERANCE_M * _QUERY_SLACK, output_type="ndarray")
    if not len(pairs):
        return []
    arc = np.concatenate(([0.0], np.cumsum(np.hypot(*np.diff(xy, axis=0).T))))  # 只用來篩長度，不涉及接點
    lengths = arc[pairs[:, 1]] - arc[pairs[:, 0]]
    keep = (lengths >= MIN_LOOP_M) & (lengths <= max_m)
    # 連續重複的點（route 的結果常在頭尾重複一點）是同一個位置：i 取一串重複的最後一個、j 取第一個，同一個圈只試一次
    repeated = np.concatenate(((arr[1:] == arr[:-1]).all(axis=1), [False]))  # repeated[k]：第 k 點與下一點相同
    keep &= ~repeated[pairs[:, 0]] & ~np.concatenate(([False], repeated[:-1]))[pairs[:, 1]]
    loops = [_Loop(int(i), int(j), float(length)) for (i, j), length in zip(pairs[keep], lengths[keep])]
    return sorted(loops, key=lambda loop: (loop.length_m, loop.start, loop.end))


def _has_length(segment: Segment) -> bool:
    return len(set(segment.points)) >= 2


def _cut_loop(segments: list[Segment], first: Pos, last: Pos) -> _Pruned | None:
    """保留 first 之前（含 first 那一點）與 last 之後（含 last 那一點），中間的段整個拿掉；同一段就切成兩段。

    新邊界兩側的點一律用 haversine 量，超過 JOIN_TOLERANCE_M 就回 None（不剪）：候選是平面近似找的，這裡是最後的確認。
    切片後不到 2 個不同點的片段（例如 last 剛好是那一段的最後一點、與下一段共用的接點）丟掉：它沒有經過任何路段。
    丟掉後新邊界兩側換成更外面的段，那兩點離 first／last 各自可能差到 1 m，所以同樣要量；
    絕不留零長度的片段把 2 m 的缺口拆成兩個 1 m。剪完沒有任何有長度的段也回 None。
    """
    left = cut(segments[first.segment], segments[first.segment].points[: first.offset + 1])
    right = cut(segments[last.segment], segments[last.segment].points[last.offset :])
    before = [*segments[: first.segment], *([left] if _has_length(left) else [])]
    after = [*([right] if _has_length(right) else []), *segments[last.segment + 1 :]]
    if before and after and haversine_m(before[-1].points[-1], after[0].points[0]) > JOIN_TOLERANCE_M:
        return None
    pruned = before + after
    if not any(_has_length(s) for s in pruned):
        return None
    return _Pruned(pruned, len(before) - 1)


def _has_new_junction(pruned: _Pruned) -> bool:
    return 0 <= pruned.junction < len(pruned.segments) - 1


def _cheap_ok(pruned: _Pruned) -> bool:
    """不用 Valhalla 的判斷：新接點不是 rideable_strict 不用 route 就會拒絕的轉彎。"""
    return not _has_new_junction(pruned) or not strict_needs_no_route_to_reject(
        *junction_arms(pruned.segments, pruned.junction)
    )


def _turns_ok(router: MatchRouter, deadline: Deadline, pruned: _Pruned) -> bool:
    """新接點用 rideable_strict；離它不到 TURN_PROBE_M 的既有接點（探針會伸進新幾何）用 rideable 重量（同 join）。
    剪掉的是開頭或結尾時沒有新接點，但離新端點不到 TURN_PROBE_M 的既有接點的探針變短了，一樣重量。"""
    segments, k = pruned.segments, pruned.junction
    if _has_new_junction(pruned) and not junction_ok(router, deadline, segments, k, strict=True):
        return False
    neighbours = chain(before_neighbours(segments, k), after_neighbours(segments, k))
    return all(junction_ok(router, deadline, segments, i) for i in neighbours)


def prune_loops(
    router: MatchRouter,
    segments: list[Segment],
    targets: list[list[LonLat]],
    deadline: Deadline,
    *,
    extent_m: float,
) -> list[Segment]:
    """剪掉路線上多繞的小刺與小圈（見模組說明），回傳新的 list（不改動傳入的 list）。extent_m：目標外框長邊（公尺）。"""
    current = list(segments)
    max_m = MAX_LOOP_RATIO * extent_m
    if max_m < MIN_LOOP_M:
        return current
    baseline: float | None = None  # 目前路線的 v3（exact）；第一次要比較時才算
    rejected: set[tuple[LonLat, ...]] = set()  # 沒剪的圈（以它的點辨識）：路線別處剪了之後索引會變，不重試同一個圈
    attempts = 0
    while True:
        flattened = flat(current)
        points = [p.point for p in flattened]
        for loop in _candidates(points, max_m):
            deadline.check()
            if deadline.remaining() < PRUNE_RESERVE_S:
                return current
            key = tuple(points[loop.start : loop.end + 1])
            if key in rejected:
                continue
            pruned = _cut_loop(current, flattened[loop.start], flattened[loop.end])
            if pruned is None or not _cheap_ok(pruned):
                rejected.add(key)
                continue
            if attempts >= MAX_PRUNE_ATTEMPTS:
                return current
            attempts += 1
            if baseline is None:
                baseline = score(targets, route_points(current)).exact
            after = score(targets, route_points(pruned.segments)).exact
            if after < baseline or not _turns_ok(router, deadline, pruned):
                rejected.add(key)
                continue
            current, baseline = pruned.segments, after
            break  # 剪了：索引全變了，重新找候選
        else:
            return current
