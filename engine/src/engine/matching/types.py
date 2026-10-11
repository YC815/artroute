"""吸附與串接共用的型別，以及兩者共用的合法 route 規則。"""

from dataclasses import dataclass
from typing import Literal, Protocol

from engine.matching.deadline import Deadline
from engine.routing.geo import haversine_m
from engine.routing.router import JOIN_TOLERANCE_M, LonLat, RouteResult, RoutingError

__all__ = [
    "JOIN_TOLERANCE_M", "MatchRouter", "MatchWarning", "Segment", "SegmentKind", "StartHint", "legal_route", "segment_from",
    "try_route",
]

type SegmentKind = Literal["stroke", "detour", "connector", "edit"]  # edit：手動微調換上的段落（route editing spec §3.2）


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


@dataclass(frozen=True)
class StartHint:
    """路線起點想落在的圓（自動搜尋，spec §4.4）：串接器從圈內離圓心最近的進入點出發。只是提示，不保證。"""

    center: LonLat
    radius_m: float


class MatchRouter(Protocol):
    def trace(self, points: list[LonLat], *, search_radius_m: float) -> RouteResult: ...
    def route(self, start: LonLat, end: LonLat, *, shortest: bool = False) -> RouteResult: ...


def segment_from(kind: SegmentKind, stroke_index: int | None, result: RouteResult) -> Segment:
    return Segment(kind=kind, stroke_index=stroke_index, points=result.points, length_m=result.length_km * 1000)


def try_route(router: MatchRouter, deadline: Deadline, start: LonLat, end: LonLat) -> RouteResult | None:
    """先檢查期限再呼叫 route；找不到路，或起訖吸附到同一處而塌縮（不到 2 個不同的點、長度 0），回傳 None。

    塌縮的結果不能收：收下就是零長度的一段，後面的路段實際上沒接上（封閉筆畫、單點也會這樣）。
    吸附流程的 route（繞路、橋接、connector）一律取最短：預設的 bicycle 成本偏好自行車道，繞路常繞遠
    （品質調查：match_rect 的繞路＋橋接 3021 → 2653 m）。shortest 只換成本，通行規則不變。
    """
    deadline.check()
    try:
        result = router.route(start, end, shortest=True)
    except RoutingError:
        return None
    return result if len(set(result.points)) >= 2 else None


def legal_route(
    router: MatchRouter,
    deadline: Deadline,
    start: LonLat,
    end: LonLat,
    tolerance_m: float,
) -> RouteResult | None:
    """start 到 end 的合法 route（try_route）；端點離要求的位置超過 tolerance_m 也回傳 None。

    route 會自己吸附起訖點。拿來接兩段路（補橋、connector）時容許誤差必須是 JOIN_TOLERANCE_M，
    否則接點留下缺口、輸出時變成一條直線（由 join 負責）；繞路候選才容許吸附到幾倍搜尋半徑外。
    """
    result = try_route(router, deadline, start, end)
    if result is None:
        return None
    if haversine_m(result.points[0], start) > tolerance_m or haversine_m(result.points[-1], end) > tolerance_m:
        return None
    return result
