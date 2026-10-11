"""剪小刺與小圈（P2.5 spec §5.2、主 spec §8 第 37 列）：路線兩次經過同一點時，剪掉中間多繞的那段。

候選：攤平的路線（compose.flat，相鄰段共用的接點只算一次）上，相距 ≤ JOIN_TOLERANCE_M（haversine）的兩點 (i, j)，
中間那段沿路線的長度在 MIN_LOOP_M 到 MAX_LOOP_RATIO × 目標外框長邊之間。由短到長逐一嘗試（同長依 i），每剪一次重新找。
每個候選依序檢查，任何一項沒過就不剪、換下一個：
1. 不用 Valhalla：剪完的新接點相距 ≤ JOIN_TOLERANCE_M；原本有 stroke 段的筆畫都還在（整個筆畫被剪掉的話，
   吸附階段決定的 warnings 不會有 no_route，前端也不會標紅，所以不剪）；新接點不是 rideable_strict 不用 route
   就會拒絕的轉彎（原地迴轉、一側量不到 JOIN_TOLERANCE_M、探針折回）。
2. v3 不下降：拿評分判斷這段有沒有畫到原圖（目標本身有那個圈，剪了覆蓋就掉）。比的是沒取整數的 exact，
   不是 overall：取整數後相同、其實略降的也不剪（比 spec 嚴）。
3. 轉彎：新接點用 rideable_strict（量不出來的一律不放行：拿掉的可能正是讓路線能迴轉的那一圈；轉角 ≤ 30° 也交給
   route 量：重疊的兩點常是立體交叉，下層道路與正上方同向的高架轉角 0° 也不能直接接）；
   沿路線離新接點不到 TURN_PROBE_M 的既有接點，探針會伸進新幾何，重量（同 join）：原本的接點用 rideable，
   前幾次剪出來的接點用 rideable_strict（它們當初就是用嚴格版收下的）。
剪法只切 Valhalla 給的幾何：保留 i 之前（含 i）與 j 之後（含 j），所以輸出的點全是原本的點、新接點 ≤ JOIN_TOLERANCE_M，
不會多出直線，騎的方向也與原本相同（不會逆向）。
期限：剪圈用自己的子期限 min(剩餘 − PRUNE_RESERVE_S, PRUNE_BUDGET_S)。子期限到期（含一次嘗試做到一半）就接住、
回傳目前結果（剪到一半的路線每一步都是完整、驗證過的）；外層到期或取消照樣往外丟。進來時剩不到保留量就不剪。
通過第 1 項的候選（要算 v3 或呼叫 route 的）最多 MAX_PRUNE_ATTEMPTS 個，不論成敗。
"""

import math
from dataclasses import dataclass, field
from itertools import chain

import numpy as np
from scipy.spatial import cKDTree

from engine.matching.compose import Pos, flat, route_points
from engine.matching.deadline import Deadline, MatchCancelled, MatchTimeout
from engine.matching.join import after_neighbours, before_neighbours, cut, junction_arms, junction_ok
from engine.matching.score import score
from engine.matching.turns import TURN_PROBE_M, strict_needs_no_route_to_reject
from engine.matching.types import JOIN_TOLERANCE_M, MatchRouter, Segment
from engine.routing.geo import haversine_m
from engine.routing.router import LonLat

MIN_LOOP_M = 2 * TURN_PROBE_M  # 比這短的圈縮圖上看不到，剪了還多一個接點
MAX_LOOP_RATIO = 0.15  # 圈長上限 = 目標外框長邊 × 這個比例：更大的圈可能是圖形本身的一部分，交給規劃器
PRUNE_RESERVE_S = 0.3  # 剪完還要留給最終評分與回應的時間（剪完之後的評分不檢查期限）
# 剪圈最多花的時間。合成路線實測：一般 < 20 ms；45 個小圈試滿 40 次約 0.17 s；6000 點、50 根死巷約 1 s（瓶頸是每次嘗試的全路線 v3）。
# 剪圈只是修飾：/match 期限 5 s、拖曳中每次都會跑，寧可少剪也不讓它吃掉回應時間；1 s 足夠一般路線把 40 次嘗試做完
PRUNE_BUDGET_S = 1.0
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


type _Junction = tuple[LonLat, LonLat]  # 接點兩側的點（前一段的終點、後一段的起點）：剪了別處之後索引會變，用點辨識


def _junction_at(segments: list[Segment], k: int) -> _Junction:
    return segments[k].points[-1], segments[k + 1].points[0]


def _strokes(segments: list[Segment]) -> set[int]:
    """路線上畫了哪些筆畫：stroke 與 detour 段都算（整段 trace 失敗時筆畫可能全是 detour，detour 不記警告）；connector 不屬於筆畫。"""
    return {s.stroke_index for s in segments if s.kind != "connector" and s.stroke_index is not None}


def _cheap_ok(pruned: _Pruned, strokes: set[int]) -> bool:
    """不用 Valhalla 的判斷：strokes（剪之前路線上畫了的筆畫，見 _strokes）都還在；新接點不是 rideable_strict 不用 route 就會拒絕的轉彎。"""
    if not strokes <= _strokes(pruned.segments):
        return False
    return not _has_new_junction(pruned) or not strict_needs_no_route_to_reject(
        *junction_arms(pruned.segments, pruned.junction)
    )


def _turns_ok(router: MatchRouter, deadline: Deadline, pruned: _Pruned, created: set[_Junction]) -> bool:
    """新接點用 rideable_strict；離它不到 TURN_PROBE_M 的既有接點（探針會伸進新幾何）重量（同 join）：
    原本的接點用 rideable，created（前幾次剪出來的接點）用 rideable_strict。
    剪掉的是開頭或結尾時沒有新接點，但離新端點不到 TURN_PROBE_M 的既有接點的探針變短了，一樣重量。"""
    segments, k = pruned.segments, pruned.junction
    if _has_new_junction(pruned) and not junction_ok(router, deadline, segments, k, strict=True):
        return False
    neighbours = chain(before_neighbours(segments, k), after_neighbours(segments, k))
    return all(junction_ok(router, deadline, segments, i, strict=_junction_at(segments, i) in created) for i in neighbours)


@dataclass
class _State:
    """剪到目前為止的結果。子期限到期時 prune_loops 回傳 segments：它只在一刀完全驗證過之後才更新。"""

    segments: list[Segment]
    baseline: float | None = None  # segments 的 v3（exact）；第一次要比較時才算
    attempts: int = 0
    created: set[_Junction] = field(default_factory=set)  # 剪出來的新接點
    rejected: set[tuple[LonLat, ...]] = field(default_factory=set)  # 沒剪的圈（以它的點辨識）：不重試同一個圈


def _prune(router: MatchRouter, state: _State, targets: list[list[LonLat]], deadline: Deadline, max_m: float) -> None:
    while True:
        flattened = flat(state.segments)
        points = [p.point for p in flattened]
        strokes = _strokes(state.segments)
        for loop in _candidates(points, max_m):
            deadline.check()
            key = tuple(points[loop.start : loop.end + 1])
            if key in state.rejected:
                continue
            pruned = _cut_loop(state.segments, flattened[loop.start], flattened[loop.end])
            if pruned is None or not _cheap_ok(pruned, strokes):
                state.rejected.add(key)
                continue
            if state.attempts >= MAX_PRUNE_ATTEMPTS:
                return
            state.attempts += 1
            if state.baseline is None:
                state.baseline = score(targets, route_points(state.segments)).exact
                deadline.check()  # 第一次嘗試要算兩次全路線評分：中間也看一次，大路線不會連算兩次才發現到期
            after = score(targets, route_points(pruned.segments)).exact
            deadline.check()  # 全路線評分可能就花掉不少時間：到期就不再呼叫 route
            if after < state.baseline or not _turns_ok(router, deadline, pruned, state.created):
                state.rejected.add(key)
                continue
            if _has_new_junction(pruned):
                state.created.add(_junction_at(pruned.segments, pruned.junction))
            state.segments, state.baseline = pruned.segments, after
            break  # 剪了：索引全變了，重新找候選
        else:
            return


def prune_loops(
    router: MatchRouter,
    segments: list[Segment],
    targets: list[list[LonLat]],
    deadline: Deadline,
    *,
    extent_m: float,
) -> list[Segment]:
    """剪掉路線上多繞的小刺與小圈（見模組說明），回傳新的 list（不改動傳入的 list）。extent_m：目標外框長邊（公尺）。
    取消（MatchCancelled）與外層期限到期（MatchTimeout）照樣往外丟；只是剪圈的時間用完時回傳目前結果。"""
    state = _State(list(segments))
    max_m = MAX_LOOP_RATIO * extent_m
    if max_m < MIN_LOOP_M:
        return state.segments
    if deadline.cancelled:
        raise MatchCancelled("呼叫端已離開")
    budget_s = min(deadline.remaining() - PRUNE_RESERVE_S, PRUNE_BUDGET_S)
    if budget_s <= 0:
        return state.segments  # 剩不到保留量（含串接時就已經過期）：不剪，到期與否交給呼叫端
    try:
        _prune(router, state, targets, deadline.child(budget_s), max_m)
    except MatchTimeout:
        deadline.check()  # 外層到期或取消：照樣往外丟
    return state.segments
