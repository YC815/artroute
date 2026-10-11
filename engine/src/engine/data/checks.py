"""台灣真實路網的驗證查詢：新版路網必須全部通過才會啟用。"""

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Protocol

from engine.data.bike_patch import STEPS, DeniedWay
from engine.data.build import load_denied
from engine.routing.geo import haversine_m
from engine.routing.router import EdgeInfo, LonLat, RouteResult, Router, RoutingError

# 洲美街單行段（OSM way 287016294，oneway=yes，節點順序由西往東）。座標取自該 way 的 geometry
ZHOUMEI_ONEWAY_WAY_ID = 287016294
ZHOUMEI_WEST: LonLat = (121.4994400, 25.1010677)
ZHOUMEI_EAST: LonLat = (121.5035557, 25.1011282)
ZHOUMEI_EXPRESSWAY_NAME = "洲美快速道路"
STEPS_SAMPLE = 20  # 樓梯檢查抽樣的 way 數（依 way id 排序後等距抽）：全台上萬條，每條都查太慢
# route 的起訖不會吸附到樓梯上（實測），從樓梯端點出發的路線一開始就只能走別條路，未修補時也不會走樓梯。
# 所以起訖改在兩端往外延伸這麼遠的地方，讓路線有機會經過樓梯
STEPS_PROBE_M = 15.0
STEPS_ERRORS_SHOWN = 5  # 失敗訊息最多列幾條找不到路的原因


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


@dataclass(frozen=True)
class _Avoidance:
    failures: list[str]  # 經過禁行路段、或配不出經過哪些路段
    errors: list[str]  # 找不到路、零長度：沒有證據，但也不是違規
    succeeded: int  # 配得出路段的路線數（含違規的）
    attempted: int


def _avoidance(router: RouterLike, ways: list[DeniedWay], denied_ids: set[int], subject: str, noun: str) -> _Avoidance:
    """每條 way 的首尾之間兩個方向各查一次自行車路線，不得經過任何被禁行的 way（ways 的端點座標都不是 None）。"""
    failures: list[str] = []
    errors: list[str] = []
    succeeded = 0
    # 兩個方向都要查：只查 first->last 會漏掉 oneway=-1 這類只在逆向才走上禁行路段的情形
    directions = [(f"way {way.id} {label}", start, end)
                  for way in ways
                  for label, start, end in (("順向", way.first, way.last), ("逆向", way.last, way.first))]
    for name, start, end in directions:
        try:
            route = router.route(start, end)
        except RoutingError as error:
            errors.append(f"{name}：{error}")
            continue
        try:
            edges = router.edges(route.points)
        except RoutingError as error:  # 有路線卻配不出經過哪些路段，就無法證明它避開了禁行路段；記成失敗，其他路線照查
            failures.append(f"{subject} {name} 的自行車路線無法判斷經過哪些路段：{error}")
            continue
        if not edges:  # 起訖吸附到同一點的零長度路線沒有經過任何路段，不能當成避開禁行路段的證據
            errors.append(f"{name}：路線長度為 0")
            continue
        succeeded += 1
        used = {e.way_id for e in edges} & denied_ids
        if used:
            failures.append(f"自行車路線（{subject} {name}）使用了被禁行的{noun} way {sorted(used)}")
    return _Avoidance(failures, errors, succeeded, len(directions))


def _expressway_checks(router: RouterLike, denied: list[DeniedWay]) -> list[str]:
    if not any(d.highway != STEPS for d in denied):
        return ["沒有任何快速道路被標記為禁行：資料或標記規則可能壞了"]
    # is_expressway 用子字串比對名稱，這裡也用子字串：真實資料可能叫「洲美快速道路（北上）」
    zhoumei = [d for d in denied if ZHOUMEI_EXPRESSWAY_NAME in (d.name or "")]
    if not zhoumei:
        return [f"找不到{ZHOUMEI_EXPRESSWAY_NAME}：OSM 資料可能變了，需要更新這項檢查"]
    # 端點節點不在 pbf 時座標是 None，這種 way 沒辦法查；全都沒座標就當失敗，不讓 None 進到 route
    checkable = [d for d in zhoumei if d.first is not None and d.last is not None]
    if not checkable:
        return [f"{ZHOUMEI_EXPRESSWAY_NAME}的 way 都缺少端點座標：無法驗證自行車路線是否避開快速道路"]
    result = _avoidance(router, checkable, {d.id for d in denied}, ZHOUMEI_EXPRESSWAY_NAME, "快速道路")
    if not result.succeeded and not result.failures:  # 沒有任何一條路線能證明避開，也沒有任何失敗：沒有證據不能算通過
        raise RoutingError("；".join(result.errors))
    return result.failures


def _beyond(a: LonLat, b: LonLat, meters: float) -> LonLat:
    """a→b 的直線延伸過 b 再走 meters 公尺的點（局部平面近似）；a、b 重合時就是 b。"""
    length = haversine_m(a, b)
    if length == 0:
        return b
    t = meters / length
    return (b[0] + (b[0] - a[0]) * t, b[1] + (b[1] - a[1]) * t)


def _steps_probe(way: DeniedWay) -> DeniedWay:
    first, last = way.first, way.last
    assert first is not None and last is not None
    return replace(way, first=_beyond(last, first, STEPS_PROBE_M), last=_beyond(first, last, STEPS_PROBE_M))


def steps_checks(router: RouterLike, denied: list[DeniedWay]) -> list[str]:
    """被標記禁行的樓梯至少一條；抽樣的樓梯兩端之間的自行車路線不得經過任何被禁行的 way，
    而且至少一半的路線要配得出路段（真實資料上很多樓梯夾在人行道網裡，只靠一兩條路線不算證據）。"""
    steps = sorted((d for d in denied if d.highway == STEPS), key=lambda d: d.id)
    if not steps:
        return ["沒有任何樓梯被標記為禁行：資料或標記規則可能壞了"]
    checkable = [d for d in steps if d.first is not None and d.last is not None]
    if not checkable:
        return ["被標記的樓梯都缺少端點座標：無法驗證自行車路線是否避開樓梯"]
    sample = checkable[:: max(1, len(checkable) // STEPS_SAMPLE)][:STEPS_SAMPLE]
    result = _avoidance(router, [_steps_probe(d) for d in sample], {d.id for d in denied}, "樓梯", "路段")
    if 2 * result.succeeded >= result.attempted:
        return result.failures
    shortfall = (f"樓梯檢查失敗：只有 {result.succeeded}/{result.attempted} 條路線配得出路段（至少要一半）："
                 + "；".join(result.errors[:STEPS_ERRORS_SHOWN]))
    return [*result.failures, shortfall]


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


def _build_checks(router: RouterLike, denied: list[DeniedWay]) -> list[str]:
    return taiwan_checks(router, denied) + steps_checks(router, denied)


def validate_build(build_dir: Path) -> list[str]:
    router, denied = Router.from_build(build_dir), load_denied(build_dir)
    shortest = [f"（shortest）{failure}" for failure in _build_checks(ShortestRoutes(router), denied)]
    return _build_checks(router, denied) + shortest
