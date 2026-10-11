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
_SHAPE_ALIGN_M = 0.5  # edge_walk 回應的形狀與輸入逐點對得上的容許誤差（polyline6 編碼本身的誤差約 0.1 m）
# fast 吸附的轉彎懲罰（唯一定義處）。依據 P2.5 spec §5.1：使用者盲測 V2（加 500）對現行 2 勝 0 負 10 平，
# 減少鑽進旁邊巷子又退出來的小刺。不帶時由 meili 設定決定（設定檔的 bicycle 模式是 140）
FAST_TURN_PENALTY_FACTOR = 500.0


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
    # 這次經過的方向自行車不能騎（逆向，見 _wrong_way）；None = Valhalla 沒給方向資訊、判斷不了。
    # 預設 False 給測試的假 router 用；Router.edges 一律明確填
    wrong_way: bool | None = False
    # 這條 edge 在 Router.edges 的 points 參數（折線）上的範圍：begin_shape_index..end_shape_index 是頂點編號（含兩端）。
    # None = 不知道（Valhalla 沒給、值不合理，或 edge_walk 的形狀與輸入對不上）；缺漏時 bench 的合法重現（bench.replay）
    # 重現不了，禁行與逆向的判定就不會被豁免
    begin_shape_index: int | None = None
    end_shape_index: int | None = None


class RoutingError(Exception):
    pass


def _wrong_way(edge: dict) -> bool | None:
    """trace_attributes 的一條 edge 是不是逆向騎：edge.traversability 是自行車能騎的方向，相對 OSM way 的方向
    （forward／backward／both／none，Valhalla 依這條路的通行權算，含 oneway:bicycle、cycleway=opposite*），
    edge.forward 是這次是否順著 way 的方向走。兩者不合就是逆向。缺欄位或不認得的值回傳 None（判斷不了，不當成安全）。
    合成地圖實測（tests/test_wrong_way.py）：oneway=yes、-1、短單行道逆向都抓得到，順向與自行車雙向不誤報。"""
    traversability, forward = edge.get("traversability"), edge.get("forward")
    if traversability == "both":
        return False
    if traversability == "none":
        return True
    if traversability not in ("forward", "backward") or not isinstance(forward, bool):
        return None
    return forward != (traversability == "forward")


def _align_shape(matched: list[LonLat], shape: list[LonLat]) -> list[int] | None:
    """回應形狀（matched）的每個點對到輸入形狀（shape，已去重）的第幾個點；對不上回傳 None。
    實測：edge_walk 的形狀就是輸入的形狀，但路徑終點是路口時，回應尾端會多一條長度 0 的 edge，形狀多一個重複的終點，
    所以先去掉 matched 的連續重複點再逐點比對（容許 _SHAPE_ALIGN_M 的編碼誤差）。"""
    index: list[int] = []
    deduped: list[LonLat] = []
    for point in matched:
        if not deduped or point != deduped[-1]:
            deduped.append(point)
        index.append(len(deduped) - 1)
    if len(deduped) != len(shape) or any(haversine_m(m, p) > _SHAPE_ALIGN_M for m, p in zip(deduped, shape)):
        return None
    return index


def _edge_span(edge: dict, aligned: list[int] | None, kept: list[int], n_points: int) -> dict[str, int | None]:
    """trace_attributes 的 edge 範圍（回應形狀上的頂點編號）換算成輸入折線（含重複點）的編號。
    aligned 是 _align_shape 的結果；kept[i] 是去重後第 i 個點在輸入折線的編號，n_points 是輸入折線的點數。
    形狀對不上、缺欄位、不是整數、反向或越界都不給範圍（None）。"""
    begin, end = edge.get("begin_shape_index"), edge.get("end_shape_index")
    ok = all(isinstance(i, int) and not isinstance(i, bool) for i in (begin, end))
    if aligned is None or not ok or not 0 <= begin <= end < len(aligned):
        return {"begin_shape_index": None, "end_shape_index": None}
    first, last = aligned[begin], aligned[end]
    return {
        "begin_shape_index": kept[first],
        "end_shape_index": kept[last + 1] - 1 if last + 1 < len(kept) else n_points - 1,  # 終點連同後面重複的點
    }


def load_config(build_dir: Path, *, sigma_z: float | None = None) -> dict:
    """讀 build 目錄的設定，並在載入時覆寫與部署位置有關的值，改這些值不必重建 tiles。

    sigma_z：meili 量測誤差的標準差（m），有值才覆寫 `meili.default.sigma_z`，None 用建圖時的設定（Valhalla 預設 4.07）。
    """
    path = config_path(build_dir)
    try:
        config = json.loads(path.read_text())
        config["mjolnir"]["tile_dir"] = str((build_dir / "tiles").resolve())
        config["mjolnir"]["max_cache_size"] = CACHE_SIZE_BYTES
        if sigma_z is not None:
            config["meili"]["default"]["sigma_z"] = sigma_z
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

    turn_penalty_factor：吸附（trace）的轉彎懲罰，預設 FAST_TURN_PENALTY_FACTOR；明確傳 None 才不帶，由 meili 設定決定。
    只影響 trace，route 與 edges 的請求不變。
    """

    def __init__(self, config: Path | dict, *, turn_penalty_factor: float | None = FAST_TURN_PENALTY_FACTOR) -> None:
        try:
            self._actor = Actor(config if isinstance(config, dict) else str(config))
        except (OSError, RuntimeError) as error:  # tiles 目錄不存在（FileNotFoundError 是 OSError）、設定不合法
            raise RoutingError(f"無法載入路網：{error}") from error
        self._lock = threading.Lock()
        self._turn_penalty_factor = turn_penalty_factor

    @classmethod
    def from_build(
        cls,
        build_dir: Path,
        *,
        sigma_z: float | None = None,
        turn_penalty_factor: float | None = FAST_TURN_PENALTY_FACTOR,
    ) -> "Router":
        return cls(load_config(build_dir, sigma_z=sigma_z), turn_penalty_factor=turn_penalty_factor)

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
        trace_options: dict[str, float] = {"search_radius": search_radius_m}
        if self._turn_penalty_factor is not None:
            trace_options["turn_penalty_factor"] = self._turn_penalty_factor
        response = self._call(
            "trace_route",
            {"shape": _locations(points), "costing": COSTING, "shape_match": "map_snap", "trace_options": trace_options},
        )
        result = _route_result(response)
        _ensure_matched(result, points, search_radius_m)
        return result

    def route(self, start: LonLat, end: LonLat, *, shortest: bool = False) -> RouteResult:
        """shortest=True：bicycle 成本改成取最短距離（costing_options.bicycle.shortest）；
        只換成本函數，通行權、單行道、轉向限制照舊。"""
        locations = [{**loc, "node_snap_tolerance": ROUTE_NODE_SNAP_TOLERANCE_M} for loc in _locations([start, end])]
        request: dict = {"locations": locations, "costing": COSTING}
        if shortest:
            request["costing_options"] = {COSTING: {"shortest": True}}
        return _route_result(self._call("route", request))

    def edges(self, points: list[LonLat]) -> list[EdgeInfo]:
        # edge_walk 把折線的點逐一對上路段形狀的點，多一個重複點整條就配不上。
        # route 在 node_snap_tolerance=0 時，端點剛好落在路口會重複路口那個點；兩端吸附到同一點時整條只剩一個點。
        # 重複點沒有任何幾何資訊，去掉不影響結果；只剩一個點代表沒有經過任何路段
        kept = [i for i, p in enumerate(points) if i == 0 or p != points[i - 1]]  # 去重後的點在 points 的編號
        shape = [points[i] for i in kept]
        if len(shape) < 2:
            return []
        response = self._call(
            "trace_attributes",
            {
                "shape": _locations(shape),
                "costing": COSTING,
                "shape_match": "edge_walk",
                "filters": {
                    "attributes": ["edge.way_id", "edge.begin_heading", "edge.use", "edge.length", "edge.traversability",
                                   "edge.forward", "edge.begin_shape_index", "edge.end_shape_index", "shape"],
                    "action": "include",
                },
            },
        )
        try:
            matched = decode(response["shape"]) if "shape" in response else []
        except (TypeError, ValueError, IndexError) as error:
            raise RoutingError(f"Valhalla 回應格式不符：{error!r}") from error
        # edge 的範圍是 Valhalla 回應的形狀上的頂點編號。只有回應的形狀與輸入逐點對得上時，編號才能換算成 points 的編號；
        # 對不上就不給範圍，免得把別處的幾何當成這條 edge
        aligned = _align_shape(matched, shape)
        try:
            return [
                EdgeInfo(
                    way_id=e["way_id"],
                    begin_heading=e["begin_heading"],
                    use=e.get("use", ""),  # 量測用的欄位：缺了用預設值，建圖驗證不能因此失敗
                    length_km=e.get("length", 0.0),
                    wrong_way=_wrong_way(e),
                    **_edge_span(e, aligned, kept, len(points)),
                )
                for e in response["edges"]
            ]
        except (KeyError, TypeError, AttributeError) as error:
            raise RoutingError(f"Valhalla 回應格式不符：{error!r}") from error
