"""串接器 v1（spec §4.4）：把各部件串成一條連續路線。

順序與封閉圈的進出點用直線距離估計（貪婪 + 2-opt），選定後只對實際用到的接點呼叫 route。
排序是純 Python 的 CPU 計算：貪婪每接一個部件、2-opt 每一列各檢查一次期限與取消。
"""

from collections.abc import Iterator
from dataclasses import dataclass
from typing import NamedTuple

from engine.matching.deadline import Deadline
from engine.matching.join import join
from engine.matching.types import JOIN_TOLERANCE_M, MatchRouter, Segment
from engine.routing.geo import haversine_m, polyline_length_m
from engine.routing.router import LonLat

CLOSED_ROTATION_STEP_M = 50.0  # 封閉圈進入點的最小間隔
MAX_CLOSED_ENTRIES = 24  # 每個封閉圈最多約這麼多個進入點：間隔取 max(50 m, 周長 / 24)，大圈的 2-opt 才不會失控


@dataclass(frozen=True)
class Part:
    """一個部件。segments 首尾相接；closed=True 時最後一點與第一點相差 ≤ JOIN_TOLERANCE_M。"""

    segments: list[Segment]
    closed: bool


@dataclass(frozen=True)
class Composition:
    """segments 整條相接；dropped 是無法連上的 part 索引（由小到大）。"""

    segments: list[Segment]
    dropped: list[int]


class _Pos(NamedTuple):
    """全域攤平後的一個點：它在第 segment 段的第 offset 個點。"""

    segment: int
    offset: int
    point: LonLat


def _flat(segments: list[Segment]) -> list[_Pos]:
    """依序攤平所有點；相鄰段共用的接點只算一次（歸前一段）。"""
    out: list[_Pos] = []
    for i, segment in enumerate(segments):
        skip = 1 if out else 0
        out.extend(_Pos(i, skip + k, p) for k, p in enumerate(segment.points[skip:]))
    return out


def _with_points(segment: Segment, points: list[LonLat]) -> Segment:
    return Segment(segment.kind, segment.stroke_index, points, polyline_length_m(points))


def rotate(segments: list[Segment], index: int) -> list[Segment]:
    """把封閉圈改從全域第 index 個點開始、繞一圈回到同一點；必要時把跨過該點的段落切成兩半。

    切點由攤平時記下的段內位置決定，不是用座標去找：八字形、來回的路線會重複經過同一個座標，
    用座標找會切在較早的那一次，繞出另一條路。
    """
    flat = _flat(segments)
    if index <= 0 or index >= len(flat) - 1:
        return list(segments)
    pivot = flat[index]
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
    """部件的一種進出方式。開放部件只有一種（起點進、終點出）；
    封閉圈可從全域第 rotation 個點進，繞一圈後從同一點出。"""

    rotation: int
    entry: LonLat
    exit: LonLat


def _options(part: Part) -> list[_Option]:
    """每個部件只算一次：規劃會反覆比較進出點，不能每次都重新攤平整條折線。"""
    flat = _flat(part.segments)
    if not part.closed:
        return [_Option(0, flat[0].point, part.segments[-1].points[-1])]
    # 封閉圈的進入點：起點，加上沿途每隔 step_m 一個（不含與起點重合的終點）
    step_m = max(CLOSED_ROTATION_STEP_M, sum(s.length_m for s in part.segments) / MAX_CLOSED_ENTRIES)
    picks, walked = [0], 0.0
    for k in range(1, len(flat) - 1):
        walked += haversine_m(flat[k - 1].point, flat[k].point)
        if walked >= step_m:
            picks.append(k)
            walked = 0.0
    return [_Option(k, flat[k].point, flat[k].point) for k in picks]


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


def _greedy(options: list[list[_Option]], deadline: Deadline) -> list[int]:
    """從第 0 個部件出發，每次接上離目前位置最近的部件。"""
    order, remaining = [0], list(range(1, len(options)))
    position = options[0][0].exit
    while remaining:
        deadline.check()
        nxt = min(remaining, key=lambda i: haversine_m(position, _nearest(position, options[i]).entry))
        order.append(nxt)
        remaining.remove(nxt)
        position = _nearest(position, options[nxt]).exit
    return order


def _reversals(size: int, deadline: Deadline) -> Iterator[tuple[int, int]]:
    """2-opt 要試的反轉區間 (i, j)，反轉 order[i..j]；第 0 個部件固定。每一列 i 開始前檢查一次期限。"""
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


def _append(router: MatchRouter, deadline: Deadline, route: list[Segment], body: list[Segment]) -> bool:
    """把 body 接在 route 後面，必要時先補 connector（join：兩端都是部件端點，已經在路上，容許誤差 JOIN_TOLERANCE_M；
    接不上先裁掉 route 尾巴、body 開頭再試）。接不上回傳 False，route 不變。"""
    if route and haversine_m(route[-1].points[-1], body[0].points[0]) > JOIN_TOLERANCE_M:
        joined = join(router, deadline, route[-1], body[0], "connector", None)
        if joined is None:
            return False
        route[-1:] = [joined.before, joined.link]
        body = [joined.after, *body[1:]]
    route.extend(body)
    return True


def compose(router: MatchRouter, parts: list[Part], deadline: Deadline) -> Composition:
    """依序串起各部件；兩端相距超過 JOIN_TOLERANCE_M 就用 route 補 connector，補不起來的部件丟棄。"""
    if not parts:
        return Composition(segments=[], dropped=[])
    options = [_options(part) for part in parts]
    order = _two_opt(options, _greedy(options, deadline), deadline)
    _, chosen = _plan(options, order)
    segments: list[Segment] = []
    dropped: list[int] = []
    for i in order:
        body = rotate(parts[i].segments, chosen[i].rotation) if parts[i].closed else parts[i].segments
        if not _append(router, deadline, segments, body):
            dropped.append(i)
    return Composition(segments=segments, dropped=sorted(dropped))
