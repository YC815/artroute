"""吸附與串接共用的型別，以及兩者共用的合法 route 規則。"""

from dataclasses import dataclass
from typing import Literal, Protocol

from engine.matching.deadline import Deadline
from engine.routing.geo import haversine_m
from engine.routing.router import JOIN_TOLERANCE_M, LonLat, RouteResult, RoutingError

__all__ = ["JOIN_TOLERANCE_M", "MatchRouter", "MatchWarning", "Segment", "SegmentKind", "legal_route", "segment_from"]

type SegmentKind = Literal["stroke", "detour", "connector"]


@dataclass(frozen=True)
class Segment:
    kind: SegmentKind
    stroke_index: int | None
    points: list[LonLat]
    length_m: float


@dataclass(frozen=True)
class MatchWarning:
    stroke_index: int
    code: Literal["no_route", "partial"]
    message: str


class MatchRouter(Protocol):
    def trace(self, points: list[LonLat], *, search_radius_m: float) -> RouteResult: ...
    def route(self, start: LonLat, end: LonLat) -> RouteResult: ...


def segment_from(kind: SegmentKind, stroke_index: int | None, result: RouteResult) -> Segment:
    return Segment(kind=kind, stroke_index=stroke_index, points=result.points, length_m=result.length_km * 1000)


def legal_route(
    router: MatchRouter,
    deadline: Deadline,
    start: LonLat,
    end: LonLat,
    tolerance_m: float,
) -> RouteResult | None:
    """start 到 end 的合法 route；繞不出來、塌縮、或端點離要求的位置超過 tolerance_m 就回傳 None。

    - 塌縮：起訖吸附到同一處時 route 會成功，但不到 2 個不同的點、長度 0。收下就是零長度的一段，
      後面的路段實際上沒接上（封閉筆畫、單點也會這樣）。
    - 端點：route 會自己吸附起訖點。拿來接兩段路（補橋、connector）時 tolerance_m 必須是 JOIN_TOLERANCE_M，
      否則接點留下缺口、輸出時變成一條直線（join 負責）；繞路候選才容許吸附到幾倍搜尋半徑外，接不接得上由 join 決定。
    """
    deadline.check()
    try:
        result = router.route(start, end)
    except RoutingError:
        return None
    if len(set(result.points)) < 2:
        return None
    if haversine_m(result.points[0], start) > tolerance_m or haversine_m(result.points[-1], end) > tolerance_m:
        return None
    return result
