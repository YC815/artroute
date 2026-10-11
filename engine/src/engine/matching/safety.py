"""路線的安全檢查（bench 的安全斷言與 POST /verify 共用）。

只做 Valhalla 能回答的部分：接點缺口、經過被禁行的 way、逆向騎、判斷不了。接點的轉彎另外量（matching.turns）。
判斷不了（配不出路段、Valhalla 沒給通行方向、沒有任何可檢查的路段）一律算有問題：無法證明安全就不放行。
"""

from dataclasses import dataclass, field, replace

from engine.matching.deadline import Deadline
from engine.matching.replay import ReplayRouter, legal_replay
from engine.matching.types import JOIN_TOLERANCE_M, Segment
from engine.routing.geo import haversine_m
from engine.routing.router import EdgeInfo, LonLat, RoutingError


def matched_edges(router: ReplayRouter, points: list[LonLat]) -> list[EdgeInfo]:
    """折線經過的路段（edge）。edge_walk 整段配不上時（實測：吸附結果在死巷原地折返 A→B→A），從中間的點切成兩半各自配：
    兩半共用切點，經過的路段一條都不會漏。切到只剩兩點還配不上，或配到的路段是空的，就丟 RoutingError。
    edge 的範圍（begin／end_shape_index）一律是相對傳進來的 points：後半段的編號要加上切點的位移。"""
    try:
        edges = router.edges(points)
    except RoutingError:
        if len(points) <= 2:
            raise
        mid = len(points) // 2
        return matched_edges(router, points[: mid + 1]) + [_shifted(e, mid) for e in matched_edges(router, points[mid:])]
    if not edges and len(set(points)) >= 2:
        raise RoutingError("沒有配到任何路段")
    return edges


def _shifted(edge: EdgeInfo, offset: int) -> EdgeInfo:
    if edge.begin_shape_index is None or edge.end_shape_index is None:
        return edge
    return replace(edge, begin_shape_index=edge.begin_shape_index + offset, end_shape_index=edge.end_shape_index + offset)


@dataclass
class SafetyReport:
    """逐段檢查的結果。segment 的索引都是 segments 的索引；gaps 的 i 是 segments[i] 與 [i + 1] 之間。"""

    gaps: list[tuple[int, float]] = field(default_factory=list)  # (i, 缺口公尺)
    undetermined: dict[int, str] = field(default_factory=dict)  # 配不出路段的段落 → 說明
    used: dict[int, set[int]] = field(default_factory=dict)  # 段落 → 經過的被禁行 way
    wrong: dict[int, set[int]] = field(default_factory=dict)  # 段落 → 逆向騎的 way
    unknown: set[int] = field(default_factory=set)  # 有路段 Valhalla 沒給通行方向的段落
    checked: int = 0  # 成功配出路段、檢查過的段落數


def inspect_segments(
    router: ReplayRouter,
    segments: list[Segment],
    denied_ids: frozenset[int],
    deadline: Deadline | None = None,
) -> SafetyReport:
    """逐段檢查；每段之間看一次 deadline（到期丟 MatchTimeout、斷線丟 MatchCancelled）。

    每一段經過的路段（edges）：逐段查，每段都是 Valhalla 自己的輸出，配不上就切開再配（matched_edges）。
    被判禁行或逆向的 edge 還要「合法重現」（matching.replay.legal_replay）：edge_walk 在兩條 way 共用同一段幾何時
    可能配錯 way（例如與 cycleway 重疊的 footway）。用遵守單行與禁行的 router.route 重新規劃該 edge 的範圍，
    幾何完全重合才豁免；重現不了（含範圍欄位缺漏）一律照舊算有問題。重現不用 map_snap（它遵守通行權，永遠判不出逆向）。
    """
    report = SafetyReport()
    for i, (a, b) in enumerate(zip(segments, segments[1:])):
        gap_m = haversine_m(a.points[-1], b.points[0])
        if gap_m > JOIN_TOLERANCE_M:
            report.gaps.append((i, gap_m))
    for i, segment in enumerate(segments):
        if deadline is not None:
            deadline.check()
        if len(set(segment.points)) < 2:
            continue  # 封閉圈旋轉切出的零長度片段：沒有經過任何路段
        try:
            edges = matched_edges(router, segment.points)
        except RoutingError as error:
            report.undetermined[i] = f"第 {i} 段（{segment.kind}）配不出經過的路段：{error}"
            continue
        for e in edges:
            denied, backwards = e.way_id in denied_ids, bool(e.wrong_way)
            if (denied or backwards) and not legal_replay(
                router, segment.points, e.begin_shape_index, e.end_shape_index, denied_ids
            ):
                if denied:
                    report.used.setdefault(i, set()).add(e.way_id)
                if backwards:
                    report.wrong.setdefault(i, set()).add(e.way_id)
        if any(e.wrong_way is None for e in edges):
            report.unknown.add(i)
        report.checked += 1
    return report


def unsafe_segments(
    router: ReplayRouter,
    segments: list[Segment],
    denied_ids: frozenset[int],
    deadline: Deadline | None = None,
) -> list[int] | None:
    """有問題的段落索引（由小到大）；全部通過回傳 None。缺口兩側都列；沒有任何可檢查的路段時全部都列。"""
    report = inspect_segments(router, segments, denied_ids, deadline)
    bad = {j for i, _ in report.gaps for j in (i, i + 1)}
    bad |= report.undetermined.keys() | report.used.keys() | report.wrong.keys() | report.unknown
    if not report.checked and not bad:
        bad = set(range(len(segments)))
    return sorted(bad) or None
