"""包裝 valhalla.Actor：交通方式固定 bicycle，座標一律 (lon, lat)。"""

import threading
from dataclasses import dataclass
from pathlib import Path

from valhalla import Actor

from engine.routing.geo import haversine_m, polyline_length_m
from engine.routing.polyline import decode

type LonLat = tuple[float, float]

COSTING = "bicycle"

MIN_LENGTH_RATIO = 0.7  # 匹配長度至少要有輸入折線的這個比例；P1 benchmark 會調這個值
LEG_JOIN_TOLERANCE_M = 1.0  # 相鄰 leg 的接點容許誤差，超過代表中間有跳點


@dataclass(frozen=True)
class RouteResult:
    points: list[LonLat]
    length_km: float


@dataclass(frozen=True)
class EdgeInfo:
    way_id: int
    begin_heading: float


class RoutingError(Exception):
    pass


def _locations(points: list[LonLat]) -> list[dict[str, float]]:
    return [{"lon": lon, "lat": lat} for lon, lat in points]


def _route_result(response: dict) -> RouteResult:
    trip = response["trip"]
    points: list[LonLat] = []
    for leg in trip["legs"]:
        leg_points = decode(leg["shape"])
        if points and leg_points:
            gap_m = haversine_m(points[-1], leg_points[0])
            if gap_m > LEG_JOIN_TOLERANCE_M:
                # 直接串起來會在地圖上憑空畫出一條直線
                raise RoutingError(f"路線的 leg 之間不連續，缺口 {gap_m:.1f} m")
            leg_points = leg_points[1:]
        points.extend(leg_points)
    return RouteResult(points=points, length_km=trip["summary"]["length"])


def _ensure_matched(result: RouteResult, points: list[LonLat], search_radius_m: float) -> None:
    """map_snap 遇到非法路段不會報錯，只會悄悄退化或截斷；這裡把這些情況擋下來。"""
    if len(set(result.points)) < 2:
        raise RoutingError("吸附結果少於 2 個不同的點")
    input_m = polyline_length_m(points)
    matched_m = result.length_km * 1000
    if matched_m < MIN_LENGTH_RATIO * input_m:
        raise RoutingError(
            f"吸附只覆蓋輸入的 {matched_m / input_m:.0%}（{matched_m:.0f} m / {input_m:.0f} m），"
            f"低於下限 {MIN_LENGTH_RATIO:.0%}"
        )
    max_gap_m = search_radius_m * 2
    if haversine_m(result.points[0], points[0]) > max_gap_m:
        raise RoutingError(f"吸附結果的起點離輸入起點超過 {max_gap_m:.0f} m")
    if haversine_m(result.points[-1], points[-1]) > max_gap_m:
        raise RoutingError(f"吸附結果的終點離輸入終點超過 {max_gap_m:.0f} m")


class Router:
    """包裝單一個 valhalla.Actor。

    Actor 不是 thread-safe：binding 會釋放 GIL，多個 thread 同時呼叫同一個 Actor 會 segfault。
    所以同一個 Router 上的呼叫由鎖序列化，同時間只有一個在跑。
    要平行就開多個 Router 實例（每個各自載入 Actor）或多個行程。
    """

    def __init__(self, config_path: Path) -> None:
        self._actor = Actor(str(config_path))
        self._lock = threading.Lock()

    def _call(self, method: str, request: dict) -> dict:
        try:
            with self._lock:
                return getattr(self._actor, method)(request)
        except RuntimeError as error:
            raise RoutingError(str(error)) from error

    def trace(self, points: list[LonLat], *, search_radius_m: float = 50.0) -> RouteResult:
        response = self._call(
            "trace_route",
            {
                "shape": _locations(points),
                "costing": COSTING,
                "shape_match": "map_snap",
                "trace_options": {"search_radius": search_radius_m},
            },
        )
        result = _route_result(response)
        _ensure_matched(result, points, search_radius_m)
        return result

    def route(self, start: LonLat, end: LonLat) -> RouteResult:
        response = self._call("route", {"locations": _locations([start, end]), "costing": COSTING})
        return _route_result(response)

    def edges(self, points: list[LonLat]) -> list[EdgeInfo]:
        response = self._call(
            "trace_attributes",
            {
                "shape": _locations(points),
                "costing": COSTING,
                "shape_match": "edge_walk",
                "filters": {"attributes": ["edge.way_id", "edge.begin_heading"], "action": "include"},
            },
        )
        return [EdgeInfo(way_id=e["way_id"], begin_heading=e["begin_heading"]) for e in response["edges"]]
