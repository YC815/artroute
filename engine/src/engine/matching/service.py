"""整個圖形的吸附流程：驗證 → 逐部件分治吸附 → 串接 → 評分。"""

from dataclasses import dataclass

from engine.matching.compose import Part, compose
from engine.matching.deadline import Deadline
from engine.matching.score import Score, score
from engine.matching.stroke import match_stroke
from engine.matching.types import JOIN_TOLERANCE_M, MatchRouter, MatchWarning, Segment
from engine.routing.geo import haversine_m, polyline_length_m
from engine.routing.router import LonLat

TAIWAN_BBOX = (118.1, 21.8, 122.1, 26.4)  # (min_lon, min_lat, max_lon, max_lat)，含金門、馬祖
MIN_TARGET_LENGTH_M = 50.0
RADIUS_RATIO, MIN_RADIUS_M, MAX_RADIUS_M = 0.02, 30.0, 150.0


class OutsideTaiwan(Exception):
    pass


class TargetTooSmall(Exception):
    pass


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
    return min(MAX_RADIUS_M, max(MIN_RADIUS_M, _diagonal_m(strokes) * RADIUS_RATIO))


def _check_inside_taiwan(strokes: list[StrokeInput]) -> None:
    min_lon, min_lat, max_lon, max_lat = TAIWAN_BBOX
    points = [point for stroke in strokes for point in stroke.points]
    if not all(min_lon <= lon <= max_lon and min_lat <= lat <= max_lat for lon, lat in points):
        raise OutsideTaiwan("圖形不在台灣範圍內")


def _closes(chain: list[Segment]) -> bool:
    return haversine_m(chain[-1].points[-1], chain[0].points[0]) <= JOIN_TOLERANCE_M


def _route_points(segments: list[Segment]) -> list[LonLat]:
    points: list[LonLat] = []
    for segment in segments:
        points.extend(segment.points if not points else segment.points[1:])
    return points


def _match_parts(
    router: MatchRouter,
    strokes: list[StrokeInput],
    targets: list[list[LonLat]],
    radius: float,
    deadline: Deadline,
) -> tuple[list[Part], list[int], list[MatchWarning]]:
    """逐筆畫吸附。回傳 (部件, 每個部件所屬的筆畫索引, 吸附階段的警告)。

    部件一律取自吸附結果的 chain（端點在道路上），不用原始輸入點。
    """
    parts: list[Part] = []
    owners: list[int] = []
    warnings: list[MatchWarning] = []
    for index, target in enumerate(targets):
        matched = match_stroke(
            router, target, stroke_index=index, search_radius_m=radius, min_piece_m=2 * radius, deadline=deadline
        )
        warnings.extend(matched.warnings)
        closed = strokes[index].closed and len(matched.chains) == 1 and _closes(matched.chains[0])
        parts.extend(Part(chain, closed) for chain in matched.chains)
        owners.extend(index for _ in matched.chains)
    return parts, owners, warnings


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
    if target_length < MIN_TARGET_LENGTH_M:
        raise TargetTooSmall(f"圖形總長不到 {MIN_TARGET_LENGTH_M:.0f} m")
    radius = search_radius_m or default_search_radius(strokes)

    parts, owners, warnings = _match_parts(router, strokes, targets, radius, deadline)
    if not parts:
        raise AllStrokesFailed(warnings)

    composition = compose(router, parts, deadline)
    warnings = _merge_warnings(warnings, _dropped_warnings(owners, composition.dropped))
    route = _route_points(composition.segments)
    return MatchResult(
        route_points=route,
        route_length_m=sum(s.length_m for s in composition.segments),
        segments=composition.segments,
        score=score(targets, route),
        target_length_m=target_length,
        warnings=warnings,
    )
