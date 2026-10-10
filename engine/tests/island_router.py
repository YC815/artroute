"""假 router：路網西半邊是 route 到不了的小島（像沒接上路網的自行車道片段），東半邊是主路網。

模擬 loki 的 minimum_reachability：trace 可以吸附到島上，route 卻會把島上的起點吸到最近的主路、
島上的終點則找不到路。最終審查 I-1 用它重現「橋接線從別處出發 → 接點留下 36 m 直線」。
"""

from engine.routing.geo import haversine_m, polyline_length_m
from engine.routing.router import RouteResult, RoutingError

LAT = 25.05
ISLAND_EDGE_LON = 121.505  # 以西是小島
ISLAND_DLAT = 0.00018  # 島在主路北邊約 20 m


def on_island(point: tuple[float, float]) -> bool:
    return point[1] > LAT + ISLAND_DLAT / 2


class IslandRouter:
    def __init__(self) -> None:
        self.route_calls = 0

    def trace(self, points, *, search_radius_m):
        west = [p for p in points if p[0] < ISLAND_EDGE_LON]
        east = [p for p in points if p[0] >= ISLAND_EDGE_LON]
        if west and east:
            raise RoutingError("島與主路網不相連")
        shift = ISLAND_DLAT if west else 0.0
        snapped = [(lon, lat + shift) for lon, lat in points]
        return RouteResult(points=snapped, length_km=polyline_length_m(snapped) / 1000)

    def route(self, start, end):
        self.route_calls += 1
        if on_island(end):
            raise RoutingError("終點在島上：route 到不了")
        if on_island(start):
            start = (max(start[0], ISLAND_EDGE_LON) + 0.00005, LAT)  # 島上的起點被吸到最近的主路（往東南約 25–35 m）
        return RouteResult(points=[start, end], length_km=haversine_m(start, end) / 1000)


def across_island(n: int = 40) -> list[tuple[float, float]]:
    """從島上往東畫到主路網的一條 1 km 直線。"""
    return [(121.500 + 0.010 * i / n, LAT) for i in range(n + 1)]
