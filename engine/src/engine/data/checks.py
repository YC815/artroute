"""台灣真實路網的驗證查詢：新版路網必須全部通過才會啟用。"""

from pathlib import Path
from typing import Protocol

from engine.routing.router import EdgeInfo, LonLat, RouteResult, Router

# 洲美街單行段（OSM way 287016294，oneway=yes，節點順序由西往東）。座標取自該 way 的 geometry
ZHOUMEI_ONEWAY_WAY_ID = 287016294
ZHOUMEI_WEST: LonLat = (121.4994400, 25.1010677)
ZHOUMEI_EAST: LonLat = (121.5035557, 25.1011282)


class RouterLike(Protocol):
    def route(self, start: LonLat, end: LonLat) -> RouteResult: ...
    def edges(self, points: list[LonLat]) -> list[EdgeInfo]: ...


def taiwan_checks(router: RouterLike) -> list[str]:
    failures: list[str] = []
    forward = router.edges(router.route(ZHOUMEI_WEST, ZHOUMEI_EAST).points)
    if not any(e.way_id == ZHOUMEI_ONEWAY_WAY_ID for e in forward):
        failures.append(
            f"順向路線沒有走洲美街單行段（way {ZHOUMEI_ONEWAY_WAY_ID}）：OSM 資料可能變了，需要更新這項檢查"
        )
    for edge in router.edges(router.route(ZHOUMEI_EAST, ZHOUMEI_WEST).points):
        if edge.way_id == ZHOUMEI_ONEWAY_WAY_ID and not 45 <= edge.begin_heading <= 135:
            failures.append(f"逆向騎上洲美街單行段（heading {edge.begin_heading}）")
    return failures


def validate_build(build_dir: Path) -> list[str]:
    return taiwan_checks(Router.from_build(build_dir))
