"""假 router：某個路口（blocked）不能轉彎，用來測接點轉彎檢查。

route：起點與終點都在 blocked 的 PROBE_RADIUS_M 內（也就是轉彎檢查的探針，接點前後各 8 m）時，
回傳往北繞一大圈（約 900 m）的路線，代表 Valhalla 不會直接騎過去；其他情況直接走直線。
trace：輸入點原樣回傳；blocked 是 piece 的中間點（不是頭尾）時失敗，所以在 blocked 轉彎的筆畫一定在那裡切開。
"""

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
    def __init__(self, blocked=None) -> None:
        self.blocked = blocked
        self.route_calls: list[tuple] = []

    def _near(self, point) -> bool:
        return self.blocked is not None and haversine_m(point, self.blocked) <= PROBE_RADIUS_M

    def trace(self, points, *, search_radius_m):
        if self.blocked is not None and any(haversine_m(p, self.blocked) < 0.01 for p in points[1:-1]):
            raise RoutingError("跨過不能轉彎的路口")
        return RouteResult(points=list(points), length_km=polyline_length_m(points) / 1000)

    def route(self, start, end, *, shortest=False):
        self.route_calls.append((start, end))
        if self._near(start) and self._near(end):
            points = [start, north(start, 440), north(end, 440), end]
        else:
            points = [start, end]
        return RouteResult(points=points, length_km=polyline_length_m(points) / 1000)
