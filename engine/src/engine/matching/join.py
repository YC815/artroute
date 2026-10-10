"""把兩段吸附結果接起來：吸附（補橋）與串接（connector）共用。

接點只容許 JOIN_TOLERANCE_M。直接接不上時（常見原因：端點落在 route 用不了的 edge 上，route 會把它改吸到別處），
沿既有路段把沒接上的那一端往內裁一級（20／50／100 m）再接：起點沒接上就裁前段尾巴、終點沒接上就裁後段開頭，
找不到路（看不出是哪一端）就兩端都裁。每次最多一級，所以一次 join 最多 7 次 route。
裁掉的只是 Valhalla 給的幾何，剩下的仍是原本路段的一部分，接點仍 ≤ JOIN_TOLERANCE_M，所以不會多出直線或新的路段。
裁到底仍接不上才回 None，由呼叫端斷開 chain 或丟掉部件。
"""

from dataclasses import dataclass

from engine.matching.deadline import Deadline
from engine.matching.types import JOIN_TOLERANCE_M, MatchRouter, Segment, SegmentKind, segment_from, try_route
from engine.routing.geo import haversine_m, polyline_length_m
from engine.routing.router import LonLat

TRIM_STEPS_M = (20.0, 50.0, 100.0)


@dataclass(frozen=True)
class Joined:
    before: Segment  # 前段（可能裁掉了尾巴）
    link: Segment  # 從 before 的終點到 after 的起點的合法 route
    after: Segment  # 後段（可能裁掉了開頭）


def _marks(points: list[LonLat]) -> list[int]:
    """從頭沿折線走，第一次累積到每個 TRIM_STEPS_M 的點的索引（同一點只算一次）。
    裁掉之後剩下的部分至少要有 2 個不同的點，否則那一段就整個沒了。"""
    out: list[int] = []
    walked, steps = 0.0, list(TRIM_STEPS_M)
    for k in range(1, len(points) - 1):
        walked += haversine_m(points[k - 1], points[k])
        if steps and walked >= steps[0] and len(set(points[k:])) >= 2:
            out.append(k)
            steps = [s for s in steps if s > walked]
    return out


def _cut(segment: Segment, points: list[LonLat]) -> Segment:
    if points == segment.points:
        return segment
    return Segment(segment.kind, segment.stroke_index, points, polyline_length_m(points))


def join(
    router: MatchRouter,
    deadline: Deadline,
    before: Segment,
    after: Segment,
    kind: SegmentKind,
    stroke_index: int | None,
) -> Joined | None:
    """接上 before 的終點與 after 的起點（兩端都已經在路上，容許誤差 JOIN_TOLERANCE_M）；接不上回傳 None。"""
    last = len(before.points) - 1
    tails = [last] + [last - k for k in _marks(before.points[::-1])]  # 前段保留到第幾點
    heads = [0] + _marks(after.points)  # 後段從第幾點開始
    t = h = 0
    while True:
        start, end = before.points[tails[t]], after.points[heads[h]]
        result = try_route(router, deadline, start, end)
        start_missed = result is None or haversine_m(result.points[0], start) > JOIN_TOLERANCE_M
        end_missed = result is None or haversine_m(result.points[-1], end) > JOIN_TOLERANCE_M
        if result is not None and not (start_missed or end_missed):
            link = segment_from(kind, stroke_index, result)
            return Joined(_cut(before, before.points[: tails[t] + 1]), link, _cut(after, after.points[heads[h] :]))
        step = (min(t + start_missed, len(tails) - 1), min(h + end_missed, len(heads) - 1))
        if step == (t, h):  # 沒接上的那一端已經裁到底
            return None
        t, h = step
