"""三個編輯操作（route editing spec §2.2、§3.3）：剪掉、重畫（最短／貼圖形）、拖曳。

每個操作回傳新的 list[Segment]（不改動傳入的 list），做不到丟 EditRejected，不回半成品。
新的部分只來自 Valhalla（route、route_through、現行吸附 match_stroke），保留的部分只切既有的幾何；
拼接處的檢查在 splice，剪掉沿用剪小刺的同一套（prune.turns_ok），兩者都對新接點用嚴格版。
進來的路線先檢查連續：它是簽章過的引擎輸出，有缺口是程式錯誤，不能讓剪掉或換掉的範圍把缺口藏起來。
"""

import math
from itertools import pairwise
from typing import Literal

from engine.edit.position import Cut, NotOnRoute, RoutePos, arc_m, order, pos_at_arc, resolve, split
from engine.edit.splice import EditRejected, EditRouter, edit_segment, replaced_stroke, route_between, splice
from engine.matching.compose import Pos, flat
from engine.matching.deadline import Deadline
from engine.matching.join import connect, junction_arms
from engine.matching.prune import cut_loop, has_new_junction, loop_candidates, turns_ok
from engine.matching.service import StrokeInput, check_continuous, default_search_radius
from engine.matching.stroke import match_stroke
from engine.matching.turns import distance_to_polyline_m, strict_needs_no_route_to_reject
from engine.matching.types import JOIN_TOLERANCE_M, MatchRouter, Segment
from engine.routing.geo import haversine_m, polyline_length_m
from engine.routing.router import DRAG_SNAP_M, LonLat

type RerouteMode = Literal["shortest", "shape"]

MIN_REROUTE_M = 2.0  # 重畫的兩個位置沿路線至少相距這麼遠：再近就沒有東西可以換
DRAG_SPAN_M = 200.0  # 拖曳換掉抓點前後各這麼長：夠拖出一個街廓，又不會動到太遠的地方（spec §3.3，本機實測後再調）
TOO_CLOSE_MESSAGE = "兩個點太近，請把範圍選大一點"
CROSS_STROKE_MESSAGE = "兩個點不在同一個筆畫上，請改用「最短」"
NO_STROKE_MESSAGE = "找不到這段路線對應的原圖，請改用「最短」"
NO_SHAPE_MESSAGE = "這段圖形附近吸附不出一條完整的路，請改用「最短」"


def _arc(segments: list[Segment], pos: RoutePos) -> float:
    try:
        return arc_m(segments, pos)
    except NotOnRoute as error:
        raise EditRejected("not_on_route") from error


def _flat_arcs(segments: list[Segment], flattened: list[Pos]) -> list[float]:
    """flat 每一點沿路線的公尺數，與 position.arc_m 同一把尺：每段從自己的第一點量起，前面各段用幾何長度
    （相鄰段的接點最多差 JOIN_TOLERANCE_M，不能直接沿攤平的點累積）。"""
    cumulative, start = [], 0.0
    for segment in segments:
        walked = [start]
        for a, b in pairwise(segment.points):
            walked.append(walked[-1] + haversine_m(a, b))
        cumulative.append(walked)
        start = walked[-1]
    return [cumulative[p.segment][p.offset] for p in flattened]


def cut_at(router: MatchRouter, deadline: Deadline, segments: list[Segment], at: RoutePos) -> list[Segment]:
    """剪掉經過 at 的圈：路線兩次經過同一點（≤ JOIN_TOLERANCE_M），at 嚴格落在兩次之間。由短到長試，
    第一個新接點過得了 prune.turns_ok（新接點嚴格版、鄰近接點重量）的就剪。使用者指定的剪法不套用剪小刺的
    圈長上限、不比 v3、不限次數（期限照算）。有圈但都騎不過去 → turn_not_rideable；沒有圈 → nothing_to_cut。"""
    s = _arc(segments, at)
    check_continuous(segments)
    flattened = flat(segments)
    arcs = _flat_arcs(segments, flattened)
    loops = [
        loop for loop in loop_candidates([p.point for p in flattened], math.inf) if arcs[loop.start] < s < arcs[loop.end]
    ]
    tried = False
    for loop in loops:
        deadline.check()
        pruned = cut_loop(segments, flattened[loop.start], flattened[loop.end])
        if pruned is None:  # 平面近似找到的假圈（實際相距 > 1 m），或剪完沒有路線
            continue
        tried = True
        if has_new_junction(pruned) and strict_needs_no_route_to_reject(*junction_arms(pruned.segments, pruned.junction)):
            continue
        if turns_ok(router, deadline, pruned, created=set()):
            check_continuous(pruned.segments)
            return pruned.segments
    raise EditRejected("turn_not_rideable" if tried else "nothing_to_cut")


def reroute(
    router: EditRouter,
    deadline: Deadline,
    segments: list[Segment],
    a: RoutePos,
    b: RoutePos,
    mode: RerouteMode,
    strokes: list[StrokeInput],
) -> list[Segment]:
    """把 a、b 之間（依沿路線的先後排好）換成 Valhalla 的最短路（shortest），或吸附原圖上對應的那一段（shape）。"""
    if abs(_arc(segments, b) - _arc(segments, a)) < MIN_REROUTE_M:
        raise EditRejected("no_route", TOO_CLOSE_MESSAGE)
    first, second = order(segments, a, b)
    check_continuous(segments)
    start, end = resolve(segments, first), resolve(segments, second)
    if mode == "shape":
        return _reroute_shape(router, deadline, segments, start, end, strokes)
    result, start, end = route_between(router, deadline, start, end, segments)
    before, middle, after = split(segments, start, end)
    return splice(router, deadline, before, [edit_segment(result, replaced_stroke(middle))], after)


def _target_line(stroke: StrokeInput) -> list[LonLat]:
    return [*stroke.points, stroke.points[0]] if stroke.closed else stroke.points


def _local_xy(origin: LonLat, point: LonLat) -> tuple[float, float]:
    kx, ky = 111_320.0 * math.cos(math.radians(origin[1])), 110_574.0
    return (point[0] - origin[0]) * kx, (point[1] - origin[1]) * ky


def _project(point: LonLat, line: list[LonLat]) -> float:
    """point 投影到折線 line 上最近處，沿 line 量的公尺數（haversine 累積，與 _point_at 同一把尺）。
    每條邊在以 point 為中心的局部平面上求垂足（同 turns.distance_to_polyline_m）。"""
    best_d, best_t, walked = math.inf, 0.0, 0.0
    for a, b in pairwise(line):
        (ax, ay), (bx, by) = _local_xy(point, a), _local_xy(point, b)
        dx, dy = bx - ax, by - ay
        length2 = dx * dx + dy * dy
        f = 0.0 if length2 == 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / length2))
        d = math.hypot(ax + f * dx, ay + f * dy)
        step = haversine_m(a, b)
        if d < best_d:
            best_d, best_t = d, walked + f * step
        walked += step
    return best_t


def _point_at(line: list[LonLat], t: float) -> LonLat:
    walked = 0.0
    for a, b in pairwise(line):
        step = haversine_m(a, b)
        if step > 0 and walked + step >= t:
            f = max(0.0, (t - walked) / step)
            return a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f
        walked += step
    return line[-1]


def _sub_line(line: list[LonLat], t0: float, t1: float) -> list[LonLat]:
    """line 上從 t0 到 t1（t0 ≤ t1，公尺）的子折線：兩端內插，中間是原本的頂點。"""
    inner, walked = [], 0.0
    for a, b in pairwise(line):
        walked += haversine_m(a, b)
        if t0 < walked < t1:
            inner.append(b)
    return [_point_at(line, t0), *inner, _point_at(line, t1)]


def _target_between(stroke: StrokeInput, start: LonLat, end: LonLat) -> list[LonLat]:
    """原圖上對應 start → end 的那一段（兩端加上 start、end 本身）。開放筆畫取兩個投影之間、依先後決定方向；
    封閉筆畫照畫的方向從 start 的投影走到 end 的投影（必要時跨過收尾）。比 JOIN_TOLERANCE_M 近的相鄰點沒有形狀資訊，去掉。"""
    line = _target_line(stroke)
    ta, tb = _project(start, line), _project(end, line)
    if ta <= tb:
        sub = _sub_line(line, ta, tb)
    elif stroke.closed:
        sub = [*_sub_line(line, ta, polyline_length_m(line)), *_sub_line(line, 0.0, tb)[1:]]
    else:
        sub = _sub_line(line, tb, ta)[::-1]
    out = [start]
    for q in [*sub, end]:
        if haversine_m(q, out[-1]) > JOIN_TOLERANCE_M:
            out.append(q)
    if out[-1] != end:  # end 離前一點不到 JOIN_TOLERANCE_M：換掉前一點，兩端一定是切點本身
        out[-1:] = [end] if len(out) > 1 else [start, end]
    return out


def _connect(
    router: MatchRouter, deadline: Deadline, left: list[Segment], right: list[Segment], stroke_index: int
) -> tuple[list[Segment], list[int]]:
    """join.connect(left, right)，回傳 (結果, connect 產生的接點)。connect 只會裁 left[-1] 的尾巴、right[0] 的開頭，
    中間可能補一段 link，所以 left[:-1] 與 right[1:] 原封不動：新接點是兩者之間的每個交界。有一邊是空的就沒有接點。"""
    if not left or not right:
        return [*left, *right], []
    joined = connect(router, deadline, left, right, "edit", stroke_index)
    if joined is None:
        raise EditRejected("no_route")
    return joined, list(range(len(left) - 1, len(joined) - len(right)))


def _reroute_shape(
    router: MatchRouter, deadline: Deadline, segments: list[Segment], start: Cut, end: Cut, strokes: list[StrokeInput]
) -> list[Segment]:
    """用現行吸附（fast）吸附原圖上對應的那一段，兩端用 join.connect 接回（同 fine 退回 fast 的做法，主 spec §8 第 50 列）。
    connect 只用原本的規則量接點，接好之後它產生的每個接點都交給 splice 用嚴格版重量。"""
    index = segments[start.segment].stroke_index
    if index is None or segments[end.segment].stroke_index != index:
        raise EditRejected("no_route", CROSS_STROKE_MESSAGE)
    if not 0 <= index < len(strokes):
        raise EditRejected("no_route", NO_STROKE_MESSAGE)
    stroke = strokes[index]
    radius = default_search_radius([stroke])
    matched = match_stroke(
        router, _target_between(stroke, start.point, end.point),
        stroke_index=index, search_radius_m=radius, min_piece_m=2 * radius, deadline=deadline,
    )
    if len(matched.chains) != 1 or matched.warnings:
        raise EditRejected("no_route", NO_SHAPE_MESSAGE)
    chain = [Segment("edit", s.stroke_index, s.points, s.length_m) for s in matched.chains[0]]
    before, _, after = split(segments, start, end)
    joined, first = _connect(router, deadline, before, chain, index)
    joined, second = _connect(router, deadline, joined, after, index)
    return splice(router, deadline, [], joined, [], strict=[*first, *second])


def drag(router: EditRouter, deadline: Deadline, segments: list[Segment], grab: RoutePos, to: LonLat) -> list[Segment]:
    """抓點沿路線前後各 DRAG_SPAN_M（碰到頭尾就停）換成經過 to 的路（to 是 through：不在那裡停下迴轉）。
    Valhalla 失敗分不出是 to 附近沒有路還是接不回原路線，一律 no_road；拼接處騎不過去照樣是 turn_not_rideable。"""
    s = _arc(segments, grab)
    check_continuous(segments)
    total = sum(polyline_length_m(x.points) for x in segments)
    start = resolve(segments, pos_at_arc(segments, max(0.0, s - DRAG_SPAN_M)))
    end = resolve(segments, pos_at_arc(segments, min(total, s + DRAG_SPAN_M)))
    try:
        result, start, end = route_between(router, deadline, start, end, segments, via=to)
    except EditRejected as error:
        raise EditRejected("no_road") from error
    if distance_to_polyline_m(to, result.points) > DRAG_SNAP_M:  # search_cutoff 是 Valhalla 的設定，這裡自己再確認
        raise EditRejected("no_road")
    before, middle, after = split(segments, start, end)
    return splice(router, deadline, before, [edit_segment(result, replaced_stroke(middle))], after)
