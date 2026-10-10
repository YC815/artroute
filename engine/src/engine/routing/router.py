"""包裝 valhalla.Actor：交通方式固定 bicycle，座標一律 (lon, lat)。"""

from dataclasses import dataclass
from pathlib import Path

from valhalla import Actor

from engine.routing.polyline import decode

type LonLat = tuple[float, float]

COSTING = "bicycle"


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
        if points and leg_points and points[-1] == leg_points[0]:
            leg_points = leg_points[1:]
        points.extend(leg_points)
    return RouteResult(points=points, length_km=trip["summary"]["length"])


class Router:
    def __init__(self, config_path: Path) -> None:
        self._actor = Actor(str(config_path))

    def _call(self, method: str, request: dict) -> dict:
        try:
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
        return _route_result(response)

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
