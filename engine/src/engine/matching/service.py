"""整個圖形的吸附流程：驗證 → 逐部件分治吸附 → 串接 → 評分。"""

import logging
from dataclasses import dataclass

from engine.matching.compose import Part, Rematch, compose
from engine.matching.deadline import Deadline
from engine.matching.join import adjacent, connect
from engine.matching.score import Score, score
from engine.matching.stroke import match_stroke, trace_whole
from engine.matching.types import JOIN_TOLERANCE_M, MatchRouter, MatchWarning, Segment
from engine.routing.geo import haversine_m, polyline_length_m
from engine.routing.router import LonLat
from engine.routing.tiles import MAX_SEARCH_RADIUS_M, MIN_SEARCH_RADIUS_M

TAIWAN_BBOX = (118.1, 21.8, 122.1, 26.4)  # (min_lon, min_lat, max_lon, max_lat)，含金門、馬祖
MIN_TARGET_LENGTH_M = 50.0
MIN_DIAGONAL_M = 2.0  # 範圍太小 score() 無法評分（它的下限是 1 m，量法不同，多留一倍餘裕）
RADIUS_RATIO = 0.035  # 自動半徑 = 圖形對角線 × 這個比例，夾在 MIN_SEARCH_RADIUS_M–MAX_SEARCH_RADIUS_M（品質調查：0.02 太小，trace 大量斷段）

logger = logging.getLogger(__name__)


class OutsideTaiwan(Exception):
    pass


class TargetTooSmall(Exception):
    pass


class DiscontinuousRoute(RuntimeError):
    """相鄰路段之間有缺口：程式錯誤。輸出的話缺口會變成一條地圖上不存在的直線，所以寧可回內部錯誤。"""


class AllStrokesFailed(Exception):
    def __init__(self, warnings: list[MatchWarning]) -> None:
        super().__init__("所有部件都找不到可騎的道路")
        self.warnings = warnings


@dataclass(frozen=True)
class StrokeInput:
    points: list[LonLat]
    closed: bool


@dataclass(frozen=True)
class MatchResult:
    route_points: list[LonLat]
    route_length_m: float
    segments: list[Segment]
    score: Score
    target_length_m: float
    warnings: list[MatchWarning]


def _target(stroke: StrokeInput) -> list[LonLat]:
    return stroke.points + [stroke.points[0]] if stroke.closed else stroke.points


def _diagonal_m(strokes: list[StrokeInput]) -> float:
    lons = [lon for s in strokes for lon, _ in s.points]
    lats = [lat for s in strokes for _, lat in s.points]
    return haversine_m((min(lons), min(lats)), (max(lons), max(lats)))


def default_search_radius(strokes: list[StrokeInput]) -> float:
    return float(min(MAX_SEARCH_RADIUS_M, max(MIN_SEARCH_RADIUS_M, _diagonal_m(strokes) * RADIUS_RATIO)))


def _resolve_radius(search_radius_m: float | None, strokes: list[StrokeInput]) -> float:
    if search_radius_m is None:
        return default_search_radius(strokes)
    if not MIN_SEARCH_RADIUS_M <= search_radius_m <= MAX_SEARCH_RADIUS_M:  # NaN 的比較恆為 False，也會被擋下
        raise ValueError(f"搜尋半徑必須在 {MIN_SEARCH_RADIUS_M}–{MAX_SEARCH_RADIUS_M} m 之間：{search_radius_m}")
    return search_radius_m


def _check_inside_taiwan(strokes: list[StrokeInput]) -> None:
    min_lon, min_lat, max_lon, max_lat = TAIWAN_BBOX
    points = [point for stroke in strokes for point in stroke.points]
    if not all(min_lon <= lon <= max_lon and min_lat <= lat <= max_lat for lon, lat in points):
        raise OutsideTaiwan("圖形不在台灣範圍內")


def _stitch_closed(
    router: MatchRouter, deadline: Deadline, chains: list[list[Segment]]
) -> tuple[list[list[Segment]], bool]:
    """封閉筆畫：照筆畫順序把相鄰的 chain 接起來（connect：相鄰直接串，否則 connector＋裁切重試，與串接器同一套規則）。

    回傳 (chains, closed)。全部接上、而且最後一點與第一點相鄰 → 一條 chain、closed=True。
    收尾只接受相鄰、不另外補 route：只有一個部件時那段路沒有用，只會讓路線變長。
    中間有接不上的地方 → 從那裡斷開；最後一條與第一條相鄰就合成一條（最後一條在前），
    斷點只剩真正接不上的地方，不會在收尾處多出一個部件（star@taipei_daan@r30）。
    """
    runs = [chains[0]]
    for chain in chains[1:]:
        merged = connect(router, deadline, runs[-1], chain, "connector", None)
        if merged is None:
            runs.append(chain)
        else:
            runs[-1] = merged
    if len(runs) == 1:
        return runs, adjacent(runs[0], runs[0])
    if adjacent(runs[-1], runs[0]):
        return [runs[-1] + runs[0], *runs[1:-1]], False
    return runs, False


def _check_continuous(segments: list[Segment]) -> None:
    for i, (a, b) in enumerate(zip(segments, segments[1:])):
        gap_m = haversine_m(a.points[-1], b.points[0])
        if gap_m > JOIN_TOLERANCE_M:
            logger.error("路線第 %d、%d 段（%s → %s）之間有 %.1f m 的缺口，不輸出", i, i + 1, a.kind, b.kind, gap_m)
            raise DiscontinuousRoute(f"路線第 {i}、{i + 1} 段之間有 {gap_m:.1f} m 的缺口")


def _route_points(segments: list[Segment]) -> list[LonLat]:
    """攤平成一條折線。後一段的第一點會被丟掉，所以呼叫前必須先確認相鄰段落相接（_check_continuous）。"""
    points: list[LonLat] = []
    for segment in segments:
        points.extend(segment.points if not points else segment.points[1:])
    # connector 可能有 3 個點卻只有 2 個不同：不留零長度的一步
    return [p for i, p in enumerate(points) if i == 0 or p != points[i - 1]]


def _match_parts(
    router: MatchRouter,
    strokes: list[StrokeInput],
    targets: list[list[LonLat]],
    radius: float,
    deadline: Deadline,
) -> tuple[list[Part], list[int], list[MatchWarning]]:
    """逐筆畫吸附。回傳 (部件, 每個部件所屬的筆畫索引, 吸附階段的警告)。

    部件一律取自吸附結果的 chain（端點在道路上），不用原始輸入點。
    封閉筆畫的 chain 先依筆畫順序接回（_stitch_closed）。
    """
    parts: list[Part] = []
    owners: list[int] = []
    warnings: list[MatchWarning] = []
    for index, target in enumerate(targets):
        matched = match_stroke(
            router, target, stroke_index=index, search_radius_m=radius, min_piece_m=2 * radius, deadline=deadline
        )
        warnings.extend(matched.warnings)
        chains, closed = matched.chains, False
        if strokes[index].closed and chains:
            chains, closed = _stitch_closed(router, deadline, chains)
        reversible = not strokes[index].closed and len(chains) == 1 and not matched.warnings
        parts.extend(Part(chain, closed, reversible) for chain in chains)
        owners.extend(index for _ in chains)
    return parts, owners, warnings


def _rematcher(
    router: MatchRouter, targets: list[list[LonLat]], owners: list[int], radius: float, deadline: Deadline
) -> Rematch:
    """串接器選了反向時呼叫：把整筆畫的點倒過來，整段只 trace 一次（trace_whole）。
    逆向走不通（RoutingError，含錯向保護）就回傳 None，部件照畫的方向走；不對半切、不補繞路，
    不然反向會用一條沒騎到畫的那條街的繞路，換掉乾淨的正向吸附。每個反向部件最多多 1 次 trace。"""

    def rematch(part_index: int) -> list[Segment] | None:
        owner = owners[part_index]
        backwards = trace_whole(
            router, targets[owner][::-1], stroke_index=owner, search_radius_m=radius, deadline=deadline
        )
        return None if backwards is None else [backwards]

    return rematch


def _dropped_warnings(owners: list[int], dropped: list[int]) -> list[MatchWarning]:
    """串接器丟掉的部件，每個筆畫一則：整個筆畫的部件都被丟光是 no_route，只丟一部分是 partial。"""
    gone = set(dropped)
    kept = {owner for i, owner in enumerate(owners) if i not in gone}
    out: list[MatchWarning] = []
    for index in sorted({owners[i] for i in dropped}):
        if index in kept:
            out.append(MatchWarning(index, "partial", "這段無法與其他部分相連，已略過"))
        else:
            out.append(MatchWarning(index, "no_route", "這個部件無法與其他部分相連，已略過"))
    return out


def _merge_warnings(matching: list[MatchWarning], dropped: list[MatchWarning]) -> list[MatchWarning]:
    """同一個筆畫只留一則：no_route 比 partial 嚴重（整個筆畫都不在路線上），其餘先到先得。"""
    merged = {w.stroke_index: w for w in matching}
    for w in dropped:
        if w.code == "no_route" or w.stroke_index not in merged:
            merged[w.stroke_index] = w
    return [merged[index] for index in sorted(merged)]


def match_shape(
    router: MatchRouter,
    strokes: list[StrokeInput],
    *,
    search_radius_m: float | None,
    deadline: Deadline,
) -> MatchResult:
    _check_inside_taiwan(strokes)
    targets = [_target(s) for s in strokes]
    target_length = sum(polyline_length_m(t) for t in targets)
    if target_length < MIN_TARGET_LENGTH_M or _diagonal_m(strokes) < MIN_DIAGONAL_M:
        raise TargetTooSmall(f"圖形太小：總長須至少 {MIN_TARGET_LENGTH_M:.0f} m，且範圍須至少 {MIN_DIAGONAL_M:.0f} m")
    radius = _resolve_radius(search_radius_m, strokes)

    parts, owners, warnings = _match_parts(router, strokes, targets, radius, deadline)
    if not parts:
        raise AllStrokesFailed(warnings)

    composition = compose(router, parts, deadline, rematch=_rematcher(router, targets, owners, radius, deadline))
    warnings = _merge_warnings(warnings, _dropped_warnings(owners, composition.dropped))
    _check_continuous(composition.segments)
    route = _route_points(composition.segments)
    return MatchResult(
        route_points=route,
        route_length_m=sum(s.length_m for s in composition.segments),
        segments=composition.segments,
        score=score(targets, route),
        target_length_m=target_length,
        warnings=warnings,
    )
