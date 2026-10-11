"""手動微調測試用的假 router（比照 tests/turn_router.py）。

route／route_through 直接走直線（依序經過 via）；blocked 列出的 (起點, 終點) 要往北繞一大圈（不能直接騎的轉彎）；
blocked_from 列出的點：從那裡出發的 route 都繞一大圈；snap 決定 route 的端點被吸到哪（模擬 Valhalla 把起訖點吸到別的
edge 上）；ignore_via：route_through 不經過 via（模擬吸附不到放開的位置卻照樣回傳）。
trace：給了就用它把輸入點換成吸附結果（例如原樣回傳），沒給就不准呼叫。
"""

from engine.routing.geo import haversine_m, polyline_length_m
from engine.routing.router import RouteResult, RoutingError
from turn_router import north

BLOCK_RADIUS_M = 0.5
BLOCKED_FROM_RADIUS_M = 1.0


class FakeRouter:
    def __init__(
        self, *, blocked=(), blocked_from=(), snap=lambda p: p, fail: bool = False, trace=None, ignore_via: bool = False
    ) -> None:
        self.blocked, self.blocked_from, self.snap, self.fail = list(blocked), list(blocked_from), snap, fail
        self.trace_points, self.ignore_via = trace, ignore_via
        self.calls: list[tuple] = []

    def trace(self, points, *, search_radius_m):
        if self.trace_points is None:
            raise AssertionError("不應該呼叫 trace")
        out = self.trace_points(list(points))
        return RouteResult(points=out, length_km=polyline_length_m(out) / 1000)

    def route(self, start, end, *, shortest=False):
        self.calls.append(("route", start, end, shortest))
        return self._result([start, end])

    def route_through(self, start, via, end, *, shortest=False):
        self.calls.append(("route_through", start, via, end, shortest))
        return self._result([start, end] if self.ignore_via else [start, via, end])

    def _result(self, points):
        if self.fail:
            raise RoutingError("no path")
        a, b = self.snap(points[0]), self.snap(points[-1])
        blocked = any(
            haversine_m(a, f) <= BLOCK_RADIUS_M and haversine_m(b, t) <= BLOCK_RADIUS_M for f, t in self.blocked
        ) or any(haversine_m(a, f) <= BLOCKED_FROM_RADIUS_M for f in self.blocked_from)
        out = [a, north(a, 440), north(b, 440), b] if blocked else [a, *points[1:-1], b]
        return RouteResult(points=out, length_km=polyline_length_m(out) / 1000)
