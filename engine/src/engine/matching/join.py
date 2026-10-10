"""把兩段吸附結果接起來：吸附（補橋）、串接（connector）與封閉筆畫收尾共用。

接點有兩個條件：相距 ≤ JOIN_TOLERANCE_M，而且接點的轉彎 Valhalla 自己會直接騎（turns.rideable）。
新接上的幾何也會被鄰近既有接點的探針量到：沿路線離新接點不到 TURN_PROBE_M 的既有接點（兩側的片段很短時才有）
一併重量，沒過的算那一側沒接上，跟新接點一樣處理。
直接接不上時（常見原因：端點落在 route 用不了的 edge 上，route 會把它改吸到別處；或接點轉彎不能騎），
沿既有路段把沒接上的那一端往內裁一級（20／50／100 m）再接：起點側沒接上就裁前段尾巴、終點側沒接上就裁後段開頭，
找不到路（看不出是哪一端）就兩端都裁。每次最多一級，所以一次 join 最多 7 次嘗試
（每次 1 次 route；新接點與鄰近既有接點轉角超過 30° 時各多 1 次短 route）。
裁掉的只是 Valhalla 給的幾何，剩下的仍是原本路段的一部分，接點仍 ≤ JOIN_TOLERANCE_M，所以不會多出直線或新的路段。
裁到底仍接不上才回 None，由呼叫端斷開 chain 或丟掉部件。
"""

from collections.abc import Iterator, Sequence
from itertools import chain
from dataclasses import dataclass

from engine.matching.deadline import Deadline
from engine.matching.turns import TURN_PROBE_M, head_points, rideable, tail_points
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


def _junction_ok(router: MatchRouter, deadline: Deadline, segments: list[Segment], i: int) -> bool:
    """segments[i] 與 segments[i + 1] 之間的接點：探針沿整條路線走（跨過短片段）。"""
    return rideable(router, deadline, tail_points(segments[: i + 1]), head_points(segments[i + 1 :]))


def _before_neighbours(segments: list[Segment], junction: int) -> Iterator[int]:
    """接點 junction 之前、沿路線距離不到 TURN_PROBE_M 的既有接點，由近到遠：它們的探針會伸進新接上的幾何。"""
    walked = 0.0
    for i in range(junction - 1, -1, -1):
        walked += segments[i + 1].length_m
        if walked >= TURN_PROBE_M:
            return
        yield i


def _after_neighbours(segments: list[Segment], junction: int) -> Iterator[int]:
    """接點 junction 之後、沿路線距離不到 TURN_PROBE_M 的既有接點，由近到遠。"""
    walked = 0.0
    for i in range(junction + 1, len(segments) - 1):
        walked += segments[i].length_m
        if walked >= TURN_PROBE_M:
            return
        yield i


def _attempt(
    router: MatchRouter,
    deadline: Deadline,
    before: Segment,
    after: Segment,
    tail: int,
    head: int,
    kind: SegmentKind,
    stroke_index: int | None,
    lead: Sequence[Segment],
    trail: Sequence[Segment],
) -> tuple[Joined | None, bool, bool]:
    """試一次：route 前段第 tail 點 → 後段第 head 點。回傳 (接上的結果或 None, 起點側沒接上, 終點側沒接上)。
    端點離要求的位置超過 JOIN_TOLERANCE_M，或接點的轉彎不能直接騎，都算那一側沒接上。
    轉彎的探針沿著整條路線走：前段 before 之前接著 lead、後段 after 之後接著 trail，
    所以裁切留下的短殘段或零長度片段，擋不住它兩側的轉彎。"""
    start, end = before.points[tail], after.points[head]
    result = try_route(router, deadline, start, end)
    if result is None:
        return None, True, True
    start_missed = haversine_m(result.points[0], start) > JOIN_TOLERANCE_M
    end_missed = haversine_m(result.points[-1], end) > JOIN_TOLERANCE_M
    if start_missed or end_missed:
        return None, start_missed, end_missed
    kept_before, kept_after = _cut(before, before.points[: tail + 1]), _cut(after, after.points[head:])
    link = segment_from(kind, stroke_index, result)
    segments = [*lead, kept_before, link, kept_after, *trail]
    first = len(lead)  # 起點側接點 kept_before|link；終點側接點 link|kept_after 是 first + 1
    start_missed = not _junction_ok(router, deadline, segments, first)
    end_missed = not _junction_ok(router, deadline, segments, first + 1)
    # 新幾何也落在鄰近既有接點的探針裡（lead／trail 的短片段）：一併重量，沒過的算那一側沒接上
    start_missed = start_missed or not all(
        _junction_ok(router, deadline, segments, i) for i in _before_neighbours(segments, first)
    )
    end_missed = end_missed or not all(
        _junction_ok(router, deadline, segments, i) for i in _after_neighbours(segments, first + 1)
    )
    if start_missed or end_missed:
        return None, start_missed, end_missed
    return Joined(kept_before, link, kept_after), False, False


def join(
    router: MatchRouter,
    deadline: Deadline,
    before: Segment,
    after: Segment,
    kind: SegmentKind,
    stroke_index: int | None,
    *,
    lead: Sequence[Segment] = (),
    trail: Sequence[Segment] = (),
) -> Joined | None:
    """接上 before 的終點與 after 的起點（兩端都已經在路上）；接不上回傳 None。
    lead 是 before 之前的路段、trail 是 after 之後的路段（只用來讓轉彎探針跨段，不會被裁切或改動）。"""
    last = len(before.points) - 1
    tails = [last] + [last - k for k in _marks(before.points[::-1])]  # 前段保留到第幾點
    heads = [0] + _marks(after.points)  # 後段從第幾點開始
    t = h = 0
    while True:
        joined, start_missed, end_missed = _attempt(
            router, deadline, before, after, tails[t], heads[h], kind, stroke_index, lead, trail
        )
        if joined is not None:
            return joined
        step = (min(t + start_missed, len(tails) - 1), min(h + end_missed, len(heads) - 1))
        if step == (t, h):  # 沒接上的那一端已經裁到底
            return None
        t, h = step


def adjacent(router: MatchRouter, deadline: Deadline, left: list[Segment], right: list[Segment]) -> bool:
    """可以直接串（不補 link）：相距 ≤ JOIN_TOLERANCE_M，接點的轉彎能直接騎，
    而且離接點不到 TURN_PROBE_M 的既有接點（探針會伸進對面的幾何）也還能騎。"""
    if haversine_m(left[-1].points[-1], right[0].points[0]) > JOIN_TOLERANCE_M:
        return False
    segments = [*left, *right]
    seam = len(left) - 1
    junctions = chain([seam], _before_neighbours(segments, seam), _after_neighbours(segments, seam))
    return all(_junction_ok(router, deadline, segments, i) for i in junctions)


def connect(
    router: MatchRouter,
    deadline: Deadline,
    left: list[Segment],
    right: list[Segment],
    kind: SegmentKind,
    stroke_index: int | None,
) -> list[Segment] | None:
    """把 right 接在 left 後面，回傳新的 list（不改動傳入的 list）。

    相鄰就直接串；否則用 join 補一段 kind 的 link（必要時裁 left 的尾巴、right 的開頭）。接不上回傳 None。
    吸附（補橋）、串接（connector）、封閉筆畫收尾都走這裡：接點規則只有這一份。
    """
    if adjacent(router, deadline, left, right):
        return [*left, *right]
    joined = join(router, deadline, left[-1], right[0], kind, stroke_index, lead=left[:-1], trail=right[1:])
    if joined is None:
        return None
    return [*left[:-1], joined.before, joined.link, joined.after, *right[1:]]
