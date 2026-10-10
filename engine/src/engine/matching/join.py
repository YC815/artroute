"""把兩段吸附結果接起來：吸附（補橋）與串接（connector）共用。

接點只容許 JOIN_TOLERANCE_M（legal_route）。直接接不上時（常見原因：端點落在 route 用不了的 edge 上，
route 會把它改吸到別處），先沿既有路段把前段尾巴、後段開頭裁掉 20／50／100 m 再接。
裁掉的只是 Valhalla 給的幾何，剩下的仍是原本路段的一部分，接點仍 ≤ JOIN_TOLERANCE_M，所以不會多出直線或新的路段。
全部組合都接不上才回 None，由呼叫端斷開 chain 或丟掉部件。
"""

from dataclasses import dataclass
from itertools import product

from engine.matching.deadline import Deadline
from engine.matching.types import JOIN_TOLERANCE_M, MatchRouter, Segment, SegmentKind, legal_route, segment_from
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


def _attempts(before: Segment, after: Segment) -> list[tuple[int, int]]:
    """(前段保留到第幾點, 後段從第幾點開始)：先直接接，再只裁開頭、只裁尾巴，最後兩邊都裁。"""
    last = len(before.points) - 1
    tails = [last - k for k in _marks(before.points[::-1])]
    heads = _marks(after.points)
    return [(last, 0)] + [(last, h) for h in heads] + [(t, 0) for t in tails] + list(product(tails, heads))


def join(
    router: MatchRouter,
    deadline: Deadline,
    before: Segment,
    after: Segment,
    kind: SegmentKind,
    stroke_index: int | None,
) -> Joined | None:
    """接上 before 的終點與 after 的起點（兩端都已經在路上，容許誤差 JOIN_TOLERANCE_M）；接不上回傳 None。"""
    for tail, head in _attempts(before, after):
        start, end = before.points[tail], after.points[head]
        result = legal_route(router, deadline, start, end, JOIN_TOLERANCE_M)
        if result is not None:
            link = segment_from(kind, stroke_index, result)
            return Joined(_cut(before, before.points[: tail + 1]), link, _cut(after, after.points[head:]))
    return None
