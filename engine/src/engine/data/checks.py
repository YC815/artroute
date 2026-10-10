"""台灣真實路網的驗證查詢：新版路網必須全部通過才會啟用。"""

from pathlib import Path
from typing import Protocol

from engine.data.bike_patch import DeniedWay
from engine.data.build import load_denied
from engine.routing.router import EdgeInfo, LonLat, RouteResult, Router, RoutingError

# 洲美街單行段（OSM way 287016294，oneway=yes，節點順序由西往東）。座標取自該 way 的 geometry
ZHOUMEI_ONEWAY_WAY_ID = 287016294
ZHOUMEI_WEST: LonLat = (121.4994400, 25.1010677)
ZHOUMEI_EAST: LonLat = (121.5035557, 25.1011282)
ZHOUMEI_EXPRESSWAY_NAME = "洲美快速道路"


class RouterLike(Protocol):
    def route(self, start: LonLat, end: LonLat) -> RouteResult: ...
    def edges(self, points: list[LonLat]) -> list[EdgeInfo]: ...


def _oneway_checks(router: RouterLike) -> list[str]:
    failures: list[str] = []
    forward = router.route(ZHOUMEI_WEST, ZHOUMEI_EAST)
    if not any(e.way_id == ZHOUMEI_ONEWAY_WAY_ID for e in router.edges(forward.points)):
        failures.append(
            f"順向路線沒有走洲美街單行段（way {ZHOUMEI_ONEWAY_WAY_ID}）：OSM 資料可能變了，需要更新這項檢查"
        )
    backward = router.route(ZHOUMEI_EAST, ZHOUMEI_WEST)
    if backward.length_km <= forward.length_km:
        failures.append("洲美街逆向路線沒有比順向長：可能沒有真的繞路")
    for edge in router.edges(backward.points):
        if edge.way_id == ZHOUMEI_ONEWAY_WAY_ID and not 45 <= edge.begin_heading <= 135:
            failures.append(f"逆向騎上洲美街單行段（heading {edge.begin_heading}）")
    return failures


def _expressway_checks(router: RouterLike, denied: list[DeniedWay]) -> list[str]:
    if not denied:
        return ["沒有任何快速道路被標記為禁行：資料或標記規則可能壞了"]
    # is_expressway 用子字串比對名稱，這裡也用子字串：真實資料可能叫「洲美快速道路（北上）」
    zhoumei = [d for d in denied if ZHOUMEI_EXPRESSWAY_NAME in (d.name or "")]
    if not zhoumei:
        return [f"找不到{ZHOUMEI_EXPRESSWAY_NAME}：OSM 資料可能變了，需要更新這項檢查"]
    # 端點節點不在 pbf 時座標是 None，這種 way 沒辦法查；全都沒座標就當失敗，不讓 None 進到 route
    checkable = [(d, d.first, d.last) for d in zhoumei if d.first is not None and d.last is not None]
    if not checkable:
        return [f"{ZHOUMEI_EXPRESSWAY_NAME}的 way 都缺少端點座標：無法驗證自行車路線是否避開快速道路"]
    denied_ids = {d.id for d in denied}
    failures: list[str] = []
    errors: list[str] = []
    succeeded = 0
    # 兩個方向都要查：只查 first->last 會漏掉 oneway=-1 這類只在逆向才走上快速道路的情形
    directions = [(f"way {way.id} {label}", start, end)
                  for way, first, last in checkable
                  for label, start, end in (("順向", first, last), ("逆向", last, first))]
    for name, start, end in directions:
        try:
            route = router.route(start, end)
        except RoutingError as error:
            errors.append(f"{name}：{error}")
            continue
        try:
            edges = router.edges(route.points)
        except RoutingError as error:  # 有路線卻配不出經過哪些路段，就無法證明它避開了快速道路；記成失敗，其他路線照查
            failures.append(f"{ZHOUMEI_EXPRESSWAY_NAME} {name} 的自行車路線無法判斷經過哪些路段：{error}")
            continue
        if not edges:  # 起訖吸附到同一點的零長度路線沒有經過任何路段，不能當成避開快速道路的證據
            errors.append(f"{name}：路線長度為 0")
            continue
        succeeded += 1
        used = {e.way_id for e in edges} & denied_ids
        if used:
            failures.append(f"自行車路線（{ZHOUMEI_EXPRESSWAY_NAME} {name}）使用了被禁行的快速道路 way {sorted(used)}")
    if not succeeded and not failures:  # 沒有任何一條路線能證明避開了快速道路
        raise RoutingError("；".join(errors))
    return failures


def taiwan_checks(router: RouterLike, denied: list[DeniedWay]) -> list[str]:
    failures: list[str] = []
    for name, check in (("洲美街單行段檢查", lambda: _oneway_checks(router)),
                        ("洲美快速道路檢查", lambda: _expressway_checks(router, denied))):
        try:
            failures.extend(check())
        except RoutingError as error:
            failures.append(f"{name}失敗：{error}")
    return failures


class ShortestRoutes:
    """吸附流程實際用的 route 設定（shortest=True）。驗證查詢也要對這個設定跑：吸附用的就是它。"""

    def __init__(self, router: Router) -> None:
        self._router = router

    def route(self, start: LonLat, end: LonLat) -> RouteResult:
        return self._router.route(start, end, shortest=True)

    def edges(self, points: list[LonLat]) -> list[EdgeInfo]:
        return self._router.edges(points)


def validate_build(build_dir: Path) -> list[str]:
    router, denied = Router.from_build(build_dir), load_denied(build_dir)
    shortest = [f"（shortest）{failure}" for failure in taiwan_checks(ShortestRoutes(router), denied)]
    return taiwan_checks(router, denied) + shortest
