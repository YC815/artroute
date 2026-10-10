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
    zhoumei = [d for d in denied if d.name == ZHOUMEI_EXPRESSWAY_NAME]
    if not zhoumei:
        return [f"找不到{ZHOUMEI_EXPRESSWAY_NAME}：OSM 資料可能變了，需要更新這項檢查"]
    # 端點節點不在 pbf 時座標是 None；取第一條兩端都有座標的，都沒有就當失敗，不讓 None 進到 route
    endpoints = next(((d.first, d.last) for d in zhoumei if d.first is not None and d.last is not None), None)
    if endpoints is None:
        return [f"{ZHOUMEI_EXPRESSWAY_NAME}的 way 都缺少端點座標：無法驗證自行車路線是否避開快速道路"]
    denied_ids = {d.id for d in denied}
    route = router.route(*endpoints)
    used = {e.way_id for e in router.edges(route.points)} & denied_ids
    if used:
        return [f"自行車路線使用了被禁行的快速道路 way {sorted(used)}"]
    return []


def taiwan_checks(router: RouterLike, denied: list[DeniedWay]) -> list[str]:
    failures: list[str] = []
    for name, check in (("洲美街單行段檢查", lambda: _oneway_checks(router)),
                        ("洲美快速道路檢查", lambda: _expressway_checks(router, denied))):
        try:
            failures.extend(check())
        except RoutingError as error:
            failures.append(f"{name}失敗：{error}")
    return failures


def validate_build(build_dir: Path) -> list[str]:
    return taiwan_checks(Router.from_build(build_dir), load_denied(build_dir))
