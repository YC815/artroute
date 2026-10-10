"""串接器 v1（spec §4.4）：把各部件串成一條連續路線。

順序與封閉圈的進出點用直線距離估計（貪婪 + 2-opt），選定後只對實際用到的接點呼叫 route。
"""

from collections.abc import Iterator
from dataclasses import dataclass

from engine.matching.deadline import Deadline
from engine.matching.types import JOIN_TOLERANCE_M, MatchRouter, Segment, segment_from
from engine.routing.geo import haversine_m, polyline_length_m
from engine.routing.router import LonLat, RoutingError

CLOSED_ROTATION_STEP_M = 50.0


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


def _flat(segments: list[Segment]) -> list[tuple[int, LonLat]]:
    """(所屬段落索引, 點)；相鄰段共用的接點只算一次。"""
    out: list[tuple[int, LonLat]] = []
    for i, segment in enumerate(segments):
        points = segment.points if not out else segment.points[1:]
        out.extend((i, p) for p in points)
    return out


def _with_points(segment: Segment, points: list[LonLat]) -> Segment:
    return Segment(segment.kind, segment.stroke_index, points, polyline_length_m(points))


def rotate(segments: list[Segment], index: int) -> list[Segment]:
    """把封閉圈改從全域第 index 個點開始、繞一圈回到同一點；必要時把跨過該點的段落切成兩半。"""
    flat = _flat(segments)
    if index <= 0 or index >= len(flat) - 1:
        return list(segments)
    pivot_seg, pivot = flat[index]
    head: list[Segment] = []
    tail: list[Segment] = []
    for i, segment in enumerate(segments):
        if i < pivot_seg:
            tail.append(segment)
        elif i > pivot_seg:
            head.append(segment)
        else:
            cut = segment.points.index(pivot)
            if cut > 0:
                tail.append(_with_points(segment, segment.points[: cut + 1]))
            if cut < len(segment.points) - 1:
                head.insert(0, _with_points(segment, segment.points[cut:]))
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
        return [_Option(0, flat[0][1], part.segments[-1].points[-1])]
    # 封閉圈的進入點：起點，加上沿途每隔約 CLOSED_ROTATION_STEP_M 一個（不含與起點重合的終點）
    picks, walked = [0], 0.0
    for k in range(1, len(flat) - 1):
        walked += haversine_m(flat[k - 1][1], flat[k][1])
        if walked >= CLOSED_ROTATION_STEP_M:
            picks.append(k)
            walked = 0.0
    return [_Option(k, flat[k][1], flat[k][1]) for k in picks]


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


def _greedy(options: list[list[_Option]]) -> list[int]:
    """從第 0 個部件出發，每次接上離目前位置最近的部件。"""
    order, remaining = [0], list(range(1, len(options)))
    position = options[0][0].exit
    while remaining:
        nxt = min(remaining, key=lambda i: haversine_m(position, _nearest(position, options[i]).entry))
        order.append(nxt)
        remaining.remove(nxt)
        position = _nearest(position, options[nxt]).exit
    return order


def _neighbours(order: list[int]) -> Iterator[list[int]]:
    """2-opt 的鄰居：反轉 order[i..j]；第 0 個部件固定。"""
    for i in range(1, len(order) - 1):
        for j in range(i + 1, len(order)):
            yield order[:i] + order[i : j + 1][::-1] + order[j + 1 :]


def _improve(options: list[list[_Option]], order: list[int], cost: float) -> tuple[list[int], float] | None:
    for candidate in _neighbours(order):
        candidate_cost, _ = _plan(options, candidate)
        if candidate_cost < cost - 1e-6:
            return candidate, candidate_cost
    return None


def _two_opt(options: list[list[_Option]], order: list[int]) -> list[int]:
    cost, _ = _plan(options, order)
    while (better := _improve(options, order, cost)) is not None:
        order, cost = better
    return order


def _connect(router: MatchRouter, deadline: Deadline, start: LonLat, end: LonLat) -> Segment | None:
    """start 到 end 的合法路線；繞不出來回傳 None。

    起訖吸附到同一處時 route 會成功，但只回傳不到 2 個不同的點、長度 0：那不是連線，
    收下來就是零長度的 connector，後面的部件實際上沒接上。
    """
    deadline.check()
    try:
        result = router.route(start, end)
    except RoutingError:
        return None
    if len(set(result.points)) < 2:
        return None
    return segment_from("connector", None, result)


def compose(router: MatchRouter, parts: list[Part], deadline: Deadline) -> Composition:
    """依序串起各部件；兩端相距超過 JOIN_TOLERANCE_M 就用 route 補 connector，補不起來的部件丟棄。"""
    if not parts:
        return Composition(segments=[], dropped=[])
    options = [_options(part) for part in parts]
    order = _two_opt(options, _greedy(options))
    _, chosen = _plan(options, order)
    segments: list[Segment] = []
    dropped: list[int] = []
    for i in order:
        body = rotate(parts[i].segments, chosen[i].rotation) if parts[i].closed else parts[i].segments
        start = body[0].points[0]
        if segments and haversine_m(segments[-1].points[-1], start) > JOIN_TOLERANCE_M:
            connector = _connect(router, deadline, segments[-1].points[-1], start)
            if connector is None:
                dropped.append(i)
                continue
            segments.append(connector)
        segments.extend(body)
    return Composition(segments=segments, dropped=sorted(dropped))
