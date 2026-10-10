"""吸附與串接共用的型別。"""

from dataclasses import dataclass
from typing import Literal, Protocol

from engine.routing.router import LonLat, RouteResult

type SegmentKind = Literal["stroke", "detour", "connector"]

JOIN_TOLERANCE_M = 1.0  # 相鄰路段接點容許誤差；超過就必須用 route 接，絕不畫直線


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
