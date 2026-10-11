"""合法重現：edge_walk 把折線配到禁行／逆向的 way 時，確認同一段幾何是不是可以合法地騎。

edge_walk 在兩條 way 共用同一段幾何時可能配錯 way（例如 footway 與 cycleway 完全重疊，配到 traversability: none 的
footway），bench 的 safety_problem 與 /verify 因此誤報。不能用 map_snap 複查：它遵守通行權，永遠不會判出逆向，複查等於空洞。
這裡的做法是把被判違規的那條 edge 在折線上的範圍取出來，用 router.route（bicycle，遵守單行與禁行）規劃一次：
合法的路線若與該段折線在幾何上完全重合，這段幾何就能合法騎，違規是 edge_walk 配錯 way。真的逆向時，
合法路線必須繞路（或失敗），對不上折線。重現不了一律算不安全。
"""

import math
from collections.abc import Sequence
from typing import Protocol

from engine.matching.types import JOIN_TOLERANCE_M, MatchRouter
from engine.routing.geo import EARTH_RADIUS_M, haversine_m, polyline_length_m
from engine.routing.router import EdgeInfo, LonLat, RoutingError

REPLAY_SAMPLE_M = 0.5  # 量距離時，折線上取樣點的最大間距：取樣比容許誤差（1 m）稀疏就會漏掉中間的偏離
MAX_REPLAY_WIDEN = 3  # 重現不出來時，範圍前後各擴一個點再試的次數上限（每次多一次 route）


class ReplayRouter(MatchRouter, Protocol):
    def edges(self, points: list[LonLat]) -> list[EdgeInfo]: ...


type Plane = list[tuple[float, float]]


def _to_plane(points: Sequence[LonLat], origin: LonLat) -> Plane:
    """(lon, lat) → 以 origin 為原點的平面公尺（等距圓柱投影）；重現的範圍至多幾公里，誤差遠小於 1 mm。"""
    kx = EARTH_RADIUS_M * math.pi / 180 * math.cos(math.radians(origin[1]))
    ky = EARTH_RADIUS_M * math.pi / 180
    return [((lon - origin[0]) * kx, (lat - origin[1]) * ky) for lon, lat in points]


def _densify(plane: Plane, step_m: float) -> Plane:
    """沿折線取樣，相鄰取樣點間距 ≤ step_m（含所有原有頂點）。"""
    if not plane:
        return []
    samples = [plane[0]]
    for (x0, y0), (x1, y1) in zip(plane, plane[1:]):
        pieces = max(1, math.ceil(math.hypot(x1 - x0, y1 - y0) / step_m))
        samples.extend((x0 + (x1 - x0) * k / pieces, y0 + (y1 - y0) * k / pieces) for k in range(1, pieces + 1))
    return samples


def _distance_to_segment(p: tuple[float, float], a: tuple[float, float], b: tuple[float, float]) -> float:
    (px, py), (ax, ay), (bx, by) = p, a, b
    dx, dy = bx - ax, by - ay
    length_sq = dx * dx + dy * dy
    t = 0.0 if length_sq == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length_sq))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _within(samples: Plane, line: Plane, tolerance_m: float) -> bool:
    """每個取樣點到折線（量到線段上最近的點，不是頂點）的距離都 ≤ tolerance_m。"""
    if len(line) == 1:
        return all(math.hypot(x - line[0][0], y - line[0][1]) <= tolerance_m for x, y in samples)
    return all(
        any(_distance_to_segment(p, a, b) <= tolerance_m for a, b in zip(line, line[1:])) for p in samples
    )


def same_geometry(a: Sequence[LonLat], b: Sequence[LonLat], tolerance_m: float = JOIN_TOLERANCE_M) -> bool:
    """兩條折線幾何相同：雙向（a 的每一處離 b ≤ tolerance_m、b 的每一處離 a ≤ tolerance_m）且長度差 ≤ tolerance_m。
    只量單向會把「b 多繞出去一段」或「a 只是 b 的一部分」當成相同，所以兩個方向都要量；
    量的是點到折線（線段）的距離，兩條折線都以 ≤ REPLAY_SAMPLE_M 的間距密採樣，頂點稀疏也量得到中間的偏離。"""
    if not a or not b:
        return False
    if abs(polyline_length_m(list(a)) - polyline_length_m(list(b))) > tolerance_m:
        return False
    plane_a, plane_b = _to_plane(a, a[0]), _to_plane(b, a[0])
    return _within(_densify(plane_a, REPLAY_SAMPLE_M), plane_b, tolerance_m) and _within(
        _densify(plane_b, REPLAY_SAMPLE_M), plane_a, tolerance_m
    )


def _replay_range(points: Sequence[LonLat], begin: object, end: object) -> tuple[int, int] | None:
    """edge 的範圍 [begin, end]（折線的頂點編號）；起訖點相距不到 JOIN_TOLERANCE_M 時往外各擴一個點，
    直到夠長。欄位缺漏、不是整數、越界、反向、擴到整條折線還是太短，都回傳 None（重現不了）。"""
    if not (isinstance(begin, int) and isinstance(end, int)) or isinstance(begin, bool) or isinstance(end, bool):
        return None
    if not 0 <= begin <= end < len(points):
        return None
    while haversine_m(points[begin], points[end]) <= JOIN_TOLERANCE_M:
        if begin == 0 and end == len(points) - 1:
            return None
        begin, end = max(0, begin - 1), min(len(points) - 1, end + 1)
    return begin, end


def _route_is_legal(router: ReplayRouter, route: Sequence[LonLat], denied_ids: frozenset[int]) -> bool:
    """重現出來的路線不經過被禁行的 way：未修補的路網（對照組）上 Valhalla 的 route 會沿被禁行的 way 走，
    只比對幾何會把真的違規放行。方向不查：route 本身遵守單行，對它的逆向判定只可能是 edge_walk 又配錯
    （真實路網實測：與自行車道共用節點的單行道，重現路線一樣被判逆向，查方向會讓誤報永遠消不掉）。"""
    try:
        edges = router.edges(list(route))
    except RoutingError:
        return False
    return bool(edges) and all(e.way_id not in denied_ids for e in edges)


def _reproduces(router: ReplayRouter, piece: list[LonLat], denied_ids: frozenset[int]) -> bool:
    try:
        route = router.route(piece[0], piece[-1]).points
    except RoutingError:
        return False
    if len(set(route)) < 2 or not same_geometry(piece, route):
        return False
    return _route_is_legal(router, route, denied_ids)


def legal_replay(
    router: ReplayRouter,
    points: Sequence[LonLat],
    begin: int | None,
    end: int | None,
    denied_ids: frozenset[int],
) -> bool:
    """points[begin..end]（被 edge_walk 判成禁行或逆向的那條 edge 的範圍）能不能被合法的路線重現。

    用 router.route(起點, 終點)（bicycle，遵守單行與禁行）規劃；route 丟 RoutingError、少於 2 個不同的點、
    路線經過被禁行的 way（_route_is_legal），或與該段折線不是同一個幾何（same_geometry：雙向 ≤ 1 m、長度差 ≤ 1 m），
    就把範圍前後各擴一個點再試，最多 MAX_REPLAY_WIDEN 次：從兩條 way 共用的節點出發時 Valhalla 可能選錯起始的 edge
    而繞一大圈（真實路網實測），擴大後就重合。擴大是安全的：整段都被合法路線重合，其中被判違規的那一小段也在合法的幾何上。
    範圍缺漏、越界、太短，或擴到上限仍重現不了，都回傳 False：重現不了就算不安全。"""
    span = _replay_range(points, begin, end)
    if span is None:
        return False
    tried: set[tuple[int, int]] = set()
    for widen in range(MAX_REPLAY_WIDEN + 1):
        first, last = max(0, span[0] - widen), min(len(points) - 1, span[1] + widen)
        if (first, last) in tried:
            break
        tried.add((first, last))
        if _reproduces(router, list(points[first : last + 1]), denied_ids):
            return True
    return False
