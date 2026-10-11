"""路線上的位置（route editing spec §3.1）與在兩個位置把路線切開。

位置用 {segment, offset_m} 而不是經緯度：路線會自己重疊（來回騎的路），用經緯度投影會選錯是哪一趟。
距離一律用 haversine_m 沿折線累積（與 polyline_length_m 同一把尺），不用 Segment.length_m：
那是 Valhalla 的摘要（km 只有 3 位小數），不是網站量 offset 的那條折線。
切開的部分只來自原本的幾何：點全是原本的點，加上在原折線上線性內插的切點，所以不會多出直線。
"""

from collections.abc import Sequence
from dataclasses import dataclass

from engine.matching.join import cut
from engine.matching.types import JOIN_TOLERANCE_M, Segment
from engine.routing.geo import haversine_m, polyline_length_m
from engine.routing.router import LonLat


class NotOnRoute(ValueError):
    pass


@dataclass(frozen=True)
class RoutePos:
    segment: int
    offset_m: float  # 從 segments[segment] 的起點沿折線量的公尺數


@dataclass(frozen=True)
class Cut:
    """切點在 segments[segment].points[index] 與 [index + 1] 之間；落在頂點上時 point == points[index]
    （只有段尾例外：index 是倒數第二個點、point 是最後一個點）。"""

    segment: int
    index: int
    point: LonLat


def _interpolate(a: LonLat, b: LonLat, t: float) -> LonLat:
    if t <= 0:
        return a
    if t >= 1:
        return b
    return a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t


def _checked_offset(segments: Sequence[Segment], pos: RoutePos) -> float:
    """pos.offset_m，超過段長但不到 JOIN_TOLERANCE_M 時夾到段長；不在路線上丟 NotOnRoute。"""
    if not 0 <= pos.segment < len(segments):
        raise NotOnRoute(f"沒有第 {pos.segment} 段（路線共 {len(segments)} 段）")
    length = polyline_length_m(segments[pos.segment].points)
    if not 0 <= pos.offset_m <= length + JOIN_TOLERANCE_M:  # 也擋 NaN
        raise NotOnRoute(f"第 {pos.segment} 段長 {length:.1f} m，位置 {pos.offset_m} m 不在段上")
    return min(pos.offset_m, length)


def resolve(segments: Sequence[Segment], pos: RoutePos) -> Cut:
    offset = _checked_offset(segments, pos)
    points = segments[pos.segment].points
    walked = 0.0
    for k in range(len(points) - 1):
        step = haversine_m(points[k], points[k + 1])
        if walked + step > offset:  # 嚴格大於：剛好落在頂點時取下一條邊的起點，point == points[index]
            return Cut(pos.segment, k, _interpolate(points[k], points[k + 1], (offset - walked) / step))
        walked += step
    return Cut(pos.segment, len(points) - 2, points[-1])


def arc_m(segments: Sequence[Segment], pos: RoutePos) -> float:
    """從路線起點沿折線量到 pos 的公尺數。"""
    offset = _checked_offset(segments, pos)
    return sum(polyline_length_m(s.points) for s in segments[: pos.segment]) + offset


def pos_at_arc(segments: Sequence[Segment], arc: float) -> RoutePos:
    """arc_m 的反函數；arc 夾在 [0, 總長]。剛好落在段落交界時取後一段的起點。"""
    start = 0.0
    for i, segment in enumerate(segments):
        length = polyline_length_m(segment.points)
        if arc < start + length or i == len(segments) - 1:
            return RoutePos(i, min(max(arc - start, 0.0), length))
        start += length
    raise NotOnRoute("路線沒有任何段落")


def order(segments: Sequence[Segment], a: RoutePos, b: RoutePos) -> tuple[RoutePos, RoutePos]:
    """依沿路線的先後排好（使用者可以先點後面的）。"""
    return (b, a) if arc_m(segments, b) < arc_m(segments, a) else (a, b)


def _key(segments: Sequence[Segment], c: Cut) -> tuple[int, int, float]:
    return c.segment, c.index, haversine_m(segments[c.segment].points[c.index], c.point)


def precedes(segments: Sequence[Segment], a: Cut, b: Cut) -> bool:
    """a 沿路線嚴格在 b 之前（段尾與下一段的開頭是同一點，但段尾在前）。"""
    return _key(segments, a) < _key(segments, b)


def _head(points: list[LonLat], c: Cut) -> list[LonLat]:
    """points 從頭到切點。"""
    out = points[: c.index + 1]
    return out if out[-1] == c.point else [*out, c.point]


def _tail(points: list[LonLat], c: Cut) -> list[LonLat]:
    """points 從切點到尾。"""
    rest = points[c.index + 1 :]
    return rest if rest and rest[0] == c.point else [c.point, *rest]


def _between(points: list[LonLat], start: Cut, end: Cut) -> list[LonLat]:
    """同一段裡兩個切點之間。"""
    inner = points[start.index + 1 : end.index + 1]
    out = inner if inner and inner[0] == start.point else [start.point, *inner]
    return out if out[-1] == end.point else [*out, end.point]


def _pieces(segment: Segment, points: list[LonLat]) -> list[Segment]:
    return [cut(segment, points)] if len(set(points)) >= 2 else []


def split(
    segments: Sequence[Segment], start: Cut, end: Cut
) -> tuple[list[Segment], list[Segment], list[Segment]]:
    """在 start、end 把路線切成（保留的前段, 被換掉的中間, 保留的後段）。不到 2 個不同點的片段丟掉。"""
    if _key(segments, start) > _key(segments, end):
        raise ValueError("切點的起點必須在終點之前")
    first, last = segments[start.segment], segments[end.segment]
    before = [*segments[: start.segment], *_pieces(first, _head(first.points, start))]
    after = [*_pieces(last, _tail(last.points, end)), *segments[end.segment + 1 :]]
    if start.segment == end.segment:
        middle = _pieces(first, _between(first.points, start, end))
    else:
        middle = [
            *_pieces(first, _tail(first.points, start)),
            *segments[start.segment + 1 : end.segment],
            *_pieces(last, _head(last.points, end)),
        ]
    return before, middle, after
