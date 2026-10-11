"""串接器 v1（spec §4.4）：把各部件串成一條連續路線。

順序與封閉圈的進出點用直線距離估計（貪婪 + 2-opt），選定後只對實際用到的接點呼叫 route。
排序是純 Python 的 CPU 計算：貪婪每接一個部件、2-opt 每一列各檢查一次期限與取消。
組裝從 stroke 長度最長的部件開始往兩端接（見 compose）；接不上原定那一端的部件等其他部件接完再補（見 _assemble）。
開放部件可以反向走（Part.reversible）：反向是以倒過來的輸入點重新吸附，不是把現成路線倒過來。
"""

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import NamedTuple

from engine.matching.deadline import Deadline
from engine.matching.join import connect
from engine.matching.types import MatchRouter, Segment, StartHint
from engine.routing.geo import haversine_m, polyline_length_m
from engine.routing.router import LonLat

CLOSED_ROTATION_STEP_M = 50.0  # 封閉圈進入點的最小間隔
MAX_CLOSED_ENTRIES = 24  # 每個封閉圈最多約這麼多個進入點：間隔取 max(50 m, 周長 / 24)，大圈的 2-opt 才不會失控


@dataclass(frozen=True)
class Part:
    """一個部件。segments 首尾相接；closed=True 時最後一點與第一點相鄰（≤ JOIN_TOLERANCE_M，且接縫的轉彎能直接騎）。
    reversible=True 的開放部件可以反向走：串接器選了反向時，用 rematch 以反向的點重新吸附（不是把路線倒過來）。"""

    segments: list[Segment]
    closed: bool
    reversible: bool = False


type Rematch = Callable[[int], list[Segment] | None]  # 部件索引 → 反向重新吸附的結果；不完整回傳 None


def _no_rematch(index: int) -> None:
    return None


@dataclass(frozen=True)
class Composition:
    """segments 整條相接；dropped 是無法連上的 part 索引（由小到大）。"""

    segments: list[Segment]
    dropped: list[int]


class Pos(NamedTuple):
    """全域攤平後的一個點：它在第 segment 段的第 offset 個點。"""

    segment: int
    offset: int
    point: LonLat


def flat(segments: list[Segment]) -> list[Pos]:
    """依序攤平所有點；相鄰段共用的接點只算一次（歸前一段）。"""
    out: list[Pos] = []
    for i, segment in enumerate(segments):
        skip = 1 if out else 0
        out.extend(Pos(i, skip + k, p) for k, p in enumerate(segment.points[skip:]))
    return out


def route_points(segments: list[Segment]) -> list[LonLat]:
    """攤平成一條折線（flat 的點），不留零長度的一步（connector 可能有 3 個點卻只有 2 個不同）。
    後一段的第一點會被丟掉，所以輸出前必須先確認相鄰段落相接（service.check_continuous）。"""
    points = [p.point for p in flat(segments)]
    return [p for i, p in enumerate(points) if i == 0 or p != points[i - 1]]


def _with_points(segment: Segment, points: list[LonLat]) -> Segment:
    return Segment(segment.kind, segment.stroke_index, points, polyline_length_m(points))


def rotate(segments: list[Segment], index: int) -> list[Segment]:
    """把封閉圈改從全域第 index 個點開始、繞一圈回到同一點；必要時把跨過該點的段落切成兩半。

    切點由攤平時記下的段內位置決定，不是用座標去找：八字形、來回的路線會重複經過同一個座標，
    用座標找會切在較早的那一次，繞出另一條路。
    """
    flattened = flat(segments)
    if index <= 0 or index >= len(flattened) - 1:
        return list(segments)
    pivot = flattened[index]
    segment = segments[pivot.segment]
    tail = segments[: pivot.segment]
    head = segments[pivot.segment + 1 :]
    if pivot.offset > 0:
        tail = tail + [_with_points(segment, segment.points[: pivot.offset + 1])]
    if pivot.offset < len(segment.points) - 1:
        head = [_with_points(segment, segment.points[pivot.offset :])] + head
    return head + tail


@dataclass(frozen=True)
class _Option:
    """部件的一種進出方式。開放部件預設只有一種（起點進、終點出）；
    reverse=True：reversible 的開放部件反向走，從終點進、起點出。
    封閉圈可從全域第 rotation 個點進，繞一圈後從同一點出。"""

    rotation: int
    entry: LonLat
    exit: LonLat
    reverse: bool = False


def _options(part: Part) -> list[_Option]:
    """每個部件只算一次：規劃會反覆比較進出點，不能每次都重新攤平整條折線。"""
    flattened = flat(part.segments)
    if not part.closed:
        forward = _Option(0, flattened[0].point, part.segments[-1].points[-1])
        if not part.reversible:
            return [forward]
        return [forward, _Option(0, forward.exit, forward.entry, reverse=True)]
    # 封閉圈的進入點：起點，加上沿途每隔 step_m 一個（不含與起點重合的終點）
    step_m = max(CLOSED_ROTATION_STEP_M, sum(s.length_m for s in part.segments) / MAX_CLOSED_ENTRIES)
    picks, walked = [0], 0.0
    for k in range(1, len(flattened) - 1):
        walked += haversine_m(flattened[k - 1].point, flattened[k].point)
        if walked >= step_m:
            picks.append(k)
            walked = 0.0
    return [_Option(k, flattened[k].point, flattened[k].point) for k in picks]


def _nearest(position: LonLat, options: list[_Option]) -> _Option:
    return min(options, key=lambda o: haversine_m(position, o.entry))


def _plan(options: list[list[_Option]], order: list[int]) -> tuple[float, dict[int, _Option]]:
    """沿著 order 為每個封閉圈挑最近的進入點；回傳 (總接縫距離, 各 part 選到的進出方式)。"""
    chosen: dict[int, _Option] = {}
    cost, position = 0.0, None
    for i in order:
        pick = options[i][0] if position is None else _nearest(position, options[i])
        if position is not None:
            cost += haversine_m(position, pick.entry)
        chosen[i] = pick
        position = pick.exit
    return cost, chosen


def _greedy(options: list[list[_Option]], deadline: Deadline, first: int = 0) -> list[int]:
    """從第 first 個部件（預設第 0 個）出發，每次接上離目前位置最近的部件。"""
    order, remaining = [first], [i for i in range(len(options)) if i != first]
    position = options[first][0].exit
    while remaining:
        deadline.check()
        nxt = min(remaining, key=lambda i: haversine_m(position, _nearest(position, options[i]).entry))
        order.append(nxt)
        remaining.remove(nxt)
        position = _nearest(position, options[nxt]).exit
    return order


def _reversals(size: int, deadline: Deadline) -> Iterator[tuple[int, int]]:
    """2-opt 要試的反轉區間 (i, j)，反轉 order[i..j]；排第一的部件（規劃的起點）固定。每一列 i 開始前檢查一次期限。"""
    for i in range(1, size - 1):
        deadline.check()
        for j in range(i + 1, size):
            yield i, j


def _sweep(
    options: list[list[_Option]], order: list[int], cost: float, deadline: Deadline
) -> tuple[list[int], float, bool]:
    """掃過所有反轉區間一輪：找到改善就立刻換掉 order，掃描接著往下走（不從頭再來）。"""
    improved = False
    for i, j in _reversals(len(order), deadline):
        candidate = order[:i] + order[i : j + 1][::-1] + order[j + 1 :]
        candidate_cost, _ = _plan(options, candidate)
        if candidate_cost < cost - 1e-6:
            order, cost, improved = candidate, candidate_cost, True
    return order, cost, improved


def _two_opt(options: list[list[_Option]], order: list[int], deadline: Deadline) -> list[int]:
    cost, _ = _plan(options, order)
    improved = True
    while improved:
        order, cost, improved = _sweep(options, order, cost, deadline)
    return order


ANCHOR_TIE_M = 0.1  # 部件長度差不到這麼多視為一樣長（浮點誤差）


def _anchor(parts: list[Part]) -> int:
    """錨點：吸附後 stroke 長度（貼著畫線騎的公尺數）最長的部件，路線從它開始組裝，所以它一定不會被丟掉。
    detour／connector 不算：繞路會把總長灌大，卻不代表圖形（letter_l@taipei_riverside 的 2088 m 繞路）。
    一樣長的取索引小的：部件都一樣長時維持舊版以第 0 個部件為準。"""
    lengths = [sum(s.length_m for s in part.segments if s.kind == "stroke") for part in parts]
    longest = max(lengths)
    return next(i for i, length in enumerate(lengths) if longest - length < ANCHOR_TIE_M)


def _attach(
    router: MatchRouter, deadline: Deadline, route: list[Segment], body: list[Segment], at_end: bool
) -> list[Segment] | None:
    """body 接在 route 的尾端（at_end）或開頭；騎乘方向不變，接不上回傳 None。"""
    if at_end:
        return connect(router, deadline, route, body, "connector", None)
    return connect(router, deadline, body, route, "connector", None)


def _end(route: list[Segment], at_end: bool) -> LonLat:
    return route[-1].points[-1] if at_end else route[0].points[0]


def _retry(
    router: MatchRouter, deadline: Deadline, route: list[Segment], body: list[Segment], at_end: bool, tried: LonLat
) -> list[Segment] | None:
    """擱著的部件：原定那一端後來接了別的部件（端點換了）才再試一次，端點沒換就是同一個接點、結果一樣；再試另一端。"""
    if _end(route, at_end) != tried:
        joined = _attach(router, deadline, route, body, at_end)
        if joined is not None:
            return joined
    return _attach(router, deadline, route, body, not at_end)


def _assemble(
    router: MatchRouter, deadline: Deadline, bodies: dict[int, list[Segment]], order: list[int], anchor: int
) -> Composition:
    """從錨點開始組裝：order 裡排在錨點後面的依序接在尾端，排在前面的由近到遠接在開頭。
    原定那一端接不上的部件先擱著，其他部件照規劃接完之後再補（_retry），兩端都接不上才丟：
    一個出不去的端點只擋住接在它後面的那一次。不能立刻改接另一端：那會佔掉規劃中接在那一端的部件的位置
    （網站預設文字：只出得去、進不來的 106 m 部件被搬到開頭，排第一的「I」只好橫越整張圖接到尾巴）。"""
    k = order.index(anchor)
    route, deferred, dropped = list(bodies[anchor]), [], []  # 沒有其他部件可接時也回新的 list，不與輸入共用
    sides = [(i, True) for i in order[k + 1 :]] + [(i, False) for i in reversed(order[:k])]
    for i, at_end in sides:
        joined = _attach(router, deadline, route, bodies[i], at_end)
        if joined is None:
            deferred.append((i, at_end, _end(route, at_end)))
        else:
            route = joined
    for i, at_end, tried in deferred:
        joined = _retry(router, deadline, route, bodies[i], at_end, tried)
        if joined is None:
            dropped.append(i)
        else:
            route = joined
    return Composition(segments=route, dropped=sorted(dropped))


def _body(part: Part, option: _Option, rematch: Rematch, index: int) -> list[Segment]:
    """部件實際要騎的路段：封閉圈從選定的點開始；選了反向就重新吸附，吸附不完整時照畫的方向走。"""
    if part.closed:
        return rotate(part.segments, option.rotation)
    if option.reverse:
        backwards = rematch(index)
        if backwards is not None:
            return backwards
    return part.segments


def _start_first(options: list[list[_Option]], start: StartHint) -> tuple[int, list[list[_Option]]]:
    """起點提示：挑一個進入點當規劃的起點，回傳 (部件索引, 調整後的 options)。

    圈內有進入點就取圈內離圓心最近的，沒有就取整體離圓心最近的（結果由呼叫端檢查）。
    選到的進出方式移到該部件 options 的第一個：_plan 對排第一的部件一律取第一個選項；
    排在其他位置時 _nearest 會看所有選項，順序不影響。"""
    best: tuple[bool, float, int, int] | None = None
    for i, part_options in enumerate(options):
        for k, option in enumerate(part_options):
            distance = haversine_m(start.center, option.entry)
            key = (distance > start.radius_m, distance, i, k)
            if best is None or key < best:
                best = key
    assert best is not None
    _, _, part, k = best
    reordered = list(options)
    reordered[part] = [options[part][k], *options[part][:k], *options[part][k + 1 :]]
    return part, reordered


def compose(
    router: MatchRouter,
    parts: list[Part],
    deadline: Deadline,
    rematch: Rematch = _no_rematch,
    start: StartHint | None = None,
) -> Composition:
    """依規劃的順序串起各部件；順序與封閉圈進出點的規劃不變。

    組裝從錨點（stroke 長度最長的部件，繞路不算）開始往兩端接（_assemble），兩端都接不上的部件丟棄。
    舊版固定從第 0 個部件往後接：它的尾巴 route 不出去時，其餘部件全部被丟掉（ledger L64）。
    所有部件都接上原定那一端時，每個接點都是同樣的前後兩段、同樣的 join，輸出與舊版相同；
    有部件被擱置後才補接上、或被丟掉時，輸出就和舊版不同。
    開放部件可能反向走（見 Part.reversible）：規劃的進出點本來就從各部件的選項挑最近的，反向選項自動被考慮。
    start（自動搜尋的起點圈）：規劃從圈內離圓心最近的進入點出發（_start_first），2-opt 固定它；
    組裝照舊從錨點往兩端接，排第一的部件最後接在開頭，所以路線從它開始（它被擱置、改接另一端時例外，由呼叫端檢查）。
    start=None 時與沒有這個參數的版本完全相同。
    """
    if not parts:
        return Composition(segments=[], dropped=[])
    options = [_options(part) for part in parts]
    first = 0
    if start is not None:
        first, options = _start_first(options, start)
    order = _two_opt(options, _greedy(options, deadline, first), deadline)
    _, chosen = _plan(options, order)
    bodies = {i: _body(parts[i], chosen[i], rematch, i) for i in order}
    return _assemble(router, deadline, bodies, order, _anchor(parts))
