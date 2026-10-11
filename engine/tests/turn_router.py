"""假 router：某些路口（blocked、also_blocked）不能轉彎，用來測接點轉彎檢查。

route：起點與終點都在同一個封鎖路口的 PROBE_RADIUS_M 內（也就是轉彎檢查的探針，接點前後各 8 m）時，
回傳往北繞一大圈（約 900 m）的路線，代表 Valhalla 不會直接騎過去；其他情況直接走直線。
trace：輸入點原樣回傳；封鎖路口是 piece 的中間點（不是頭尾）時失敗，所以在那裡轉彎的筆畫一定在那裡切開。
"""

from engine.matching.deadline import Deadline
from engine.matching.turns import head_points, rideable, tail_points
from engine.matching.types import Segment
from engine.routing.geo import haversine_m, polyline_length_m
from engine.routing.router import RouteResult, RoutingError

M_PER_DEG_LON = 100_900  # 緯度 25° 附近
M_PER_DEG_LAT = 110_574
PROBE_RADIUS_M = 10.0


def east(point, meters):
    return (point[0] + meters / M_PER_DEG_LON, point[1])


def north(point, meters):
    return (point[0], point[1] + meters / M_PER_DEG_LAT)


class TurnBlockRouter:
    def __init__(self, blocked=None, *, also_blocked=()) -> None:
        self.blocked = blocked
        self.corners = ([] if blocked is None else [blocked]) + list(also_blocked)
        self.route_calls: list[tuple] = []

    def trace(self, points, *, search_radius_m):
        if any(haversine_m(p, corner) < 0.01 for corner in self.corners for p in points[1:-1]):
            raise RoutingError("跨過不能轉彎的路口")
        return RouteResult(points=list(points), length_km=polyline_length_m(points) / 1000)

    def route(self, start, end, *, shortest=False):
        self.route_calls.append((start, end))
        blocked = any(
            haversine_m(start, corner) <= PROBE_RADIUS_M and haversine_m(end, corner) <= PROBE_RADIUS_M
            for corner in self.corners
        )
        points = [start, north(start, 440), north(end, 440), end] if blocked else [start, end]
        return RouteResult(points=points, length_km=polyline_length_m(points) / 1000)


def every_junction_rideable(router, segments: list[Segment]) -> bool:
    """每個相鄰路段的接點都用跨段的探針量（tail_points／head_points）：零長度或很短的片段藏不住轉彎。"""
    deadline = Deadline(60)
    return all(
        rideable(router, deadline, tail_points(segments[: i + 1]), head_points(segments[i + 1 :]))
        for i in range(len(segments) - 1)
    )
