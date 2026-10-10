"""包裝 valhalla.Actor：交通方式固定 bicycle，座標一律 (lon, lat)。"""

import json
import threading
from dataclasses import dataclass
from pathlib import Path

from valhalla import Actor

from engine.routing.geo import haversine_m, polyline_length_m
from engine.routing.polyline import decode
from engine.routing.tiles import MAX_SEARCH_RADIUS_M, config_path

type LonLat = tuple[float, float]

COSTING = "bicycle"

MIN_LENGTH_RATIO = 0.7  # 匹配長度至少要有輸入折線的這個比例；P1 benchmark 會調這個值
JOIN_TOLERANCE_M = 1.0  # 相鄰路段（leg、segment）接點的容許誤差（唯一定義處）：超過代表中間有跳點，絕不能用直線接
MAX_SNAP_GAP_RADII = 2  # 吸附結果、route 端點離原始輸入點的上限（搜尋半徑的倍數，唯一定義處）
# route 預設 node_snap_tolerance = 5 m：離路口 5 m 內的端點會被拉到路口，而 trace 只吸附 1 m 內的節點。
# 以 trace 端點當 route 起訖時，連線就從路口出發，和端點之間留下最多 5 m 的缺口；兩個端點又可能被拉到同一個路口而塌縮。
ROUTE_NODE_SNAP_TOLERANCE_M = 0
CACHE_SIZE_BYTES = 256 * 1024 * 1024  # 每個 worker 各有一份 tile 快取（Valhalla 預設 1 GB）


@dataclass(frozen=True)
class RouteResult:
    points: list[LonLat]
    length_km: float


@dataclass(frozen=True)
class EdgeInfo:
    way_id: int
    begin_heading: float
    use: str = ""  # Valhalla 的 edge.use，例如 road、cycleway、footway、steps、parking_aisle
    length_km: float = 0.0  # 折線經過這條 edge 的長度


class RoutingError(Exception):
    pass


def load_config(build_dir: Path) -> dict:
    """讀 build 目錄的設定，並在載入時覆寫與部署位置有關的值，改這些值不必重建 tiles。"""
    path = config_path(build_dir)
    try:
        config = json.loads(path.read_text())
        config["mjolnir"]["tile_dir"] = str((build_dir / "tiles").resolve())
        config["mjolnir"]["max_cache_size"] = CACHE_SIZE_BYTES
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise RoutingError(f"讀不到路網設定 {path}：{error}") from error
    return config


def _locations(points: list[LonLat]) -> list[dict[str, float]]:
    return [{"lon": lon, "lat": lat} for lon, lat in points]


def _route_result(response: dict) -> RouteResult:
    try:
        legs = [decode(leg["shape"]) for leg in response["trip"]["legs"]]
        length_km = response["trip"]["summary"]["length"]
    except (KeyError, IndexError, TypeError) as error:
        raise RoutingError(f"Valhalla 回應格式不符：{error!r}") from error
    points: list[LonLat] = []
    for leg_points in legs:
        if points and leg_points:
            gap_m = haversine_m(points[-1], leg_points[0])
            if gap_m > JOIN_TOLERANCE_M:
                # 直接串起來會在地圖上憑空畫出一條直線
                raise RoutingError(f"路線的 leg 之間不連續，缺口 {gap_m:.1f} m")
            leg_points = leg_points[1:]
        points.extend(leg_points)
    return RouteResult(points=points, length_km=length_km)


def _ensure_matched(result: RouteResult, points: list[LonLat], search_radius_m: float) -> None:
    """map_snap 遇到非法路段不會報錯，只會悄悄退化或截斷；這裡把這些情況擋下來。"""
    if len(set(result.points)) < 2:
        raise RoutingError("吸附結果少於 2 個不同的點")
    input_m = polyline_length_m(points)
    matched_m = result.length_km * 1000
    if matched_m < MIN_LENGTH_RATIO * input_m:
        raise RoutingError(
            f"吸附只覆蓋輸入的 {matched_m / input_m:.0%}（{matched_m:.0f} m / {input_m:.0f} m），"
            f"低於下限 {MIN_LENGTH_RATIO:.0%}"
        )
    max_gap_m = MAX_SNAP_GAP_RADII * search_radius_m
    if haversine_m(result.points[0], points[0]) > max_gap_m:
        raise RoutingError(f"吸附結果的起點離輸入起點超過 {max_gap_m:.0f} m")
    if haversine_m(result.points[-1], points[-1]) > max_gap_m:
        raise RoutingError(f"吸附結果的終點離輸入終點超過 {max_gap_m:.0f} m")


class Router:
    """包裝單一個 valhalla.Actor。

    Actor 不是 thread-safe：binding 會釋放 GIL，多個 thread 同時呼叫同一個 Actor 會 segfault。
    所以同一個 Router 上的呼叫由鎖序列化，同時間只有一個在跑。
    要平行就開多個 Router 實例（每個各自載入 Actor）或多個行程。
    """

    def __init__(self, config: Path | dict) -> None:
        try:
            self._actor = Actor(config if isinstance(config, dict) else str(config))
        except (OSError, RuntimeError) as error:  # tiles 目錄不存在（FileNotFoundError 是 OSError）、設定不合法
            raise RoutingError(f"無法載入路網：{error}") from error
        self._lock = threading.Lock()

    @classmethod
    def from_build(cls, build_dir: Path) -> "Router":
        return cls(load_config(build_dir))

    def _call(self, method: str, request: dict) -> dict:
        try:
            with self._lock:
                return getattr(self._actor, method)(request)
        except RuntimeError as error:
            raise RoutingError(str(error)) from error

    def trace(self, points: list[LonLat], *, search_radius_m: float = 50.0) -> RouteResult:
        if len(points) < 2:
            raise RoutingError("吸附至少需要 2 個點")
        if not 0 < search_radius_m <= MAX_SEARCH_RADIUS_M:
            raise RoutingError(f"search_radius_m 必須在 0–{MAX_SEARCH_RADIUS_M} m 之間")
        response = self._call(
            "trace_route",
            {
                "shape": _locations(points),
                "costing": COSTING,
                "shape_match": "map_snap",
                "trace_options": {"search_radius": search_radius_m},
            },
        )
        result = _route_result(response)
        _ensure_matched(result, points, search_radius_m)
        return result

    def route(self, start: LonLat, end: LonLat) -> RouteResult:
        locations = [{**loc, "node_snap_tolerance": ROUTE_NODE_SNAP_TOLERANCE_M} for loc in _locations([start, end])]
        response = self._call("route", {"locations": locations, "costing": COSTING})
        return _route_result(response)

    def edges(self, points: list[LonLat]) -> list[EdgeInfo]:
        # edge_walk 把折線的點逐一對上路段形狀的點，多一個重複點整條就配不上。
        # route 在 node_snap_tolerance=0 時，端點剛好落在路口會重複路口那個點；兩端吸附到同一點時整條只剩一個點。
        # 重複點沒有任何幾何資訊，去掉不影響結果；只剩一個點代表沒有經過任何路段
        shape = [p for i, p in enumerate(points) if i == 0 or p != points[i - 1]]
        if len(shape) < 2:
            return []
        response = self._call(
            "trace_attributes",
            {
                "shape": _locations(shape),
                "costing": COSTING,
                "shape_match": "edge_walk",
                "filters": {
                    "attributes": ["edge.way_id", "edge.begin_heading", "edge.use", "edge.length"],
                    "action": "include",
                },
            },
        )
        try:
            return [
                EdgeInfo(
                    way_id=e["way_id"],
                    begin_heading=e["begin_heading"],
                    use=e.get("use", ""),  # 量測用的欄位：缺了用預設值，建圖驗證不能因此失敗
                    length_km=e.get("length", 0.0),
                )
                for e in response["edges"]
            ]
        except (KeyError, TypeError, AttributeError) as error:
            raise RoutingError(f"Valhalla 回應格式不符：{error!r}") from error
