"""規劃出的路徑交給 Valhalla 照著走（P2.5 spec §6.4）：輸出的點全部來自 Valhalla，合法性由 Valhalla 把關。

做法：規劃路徑依弧長加密到每 FOLLOW_STEP_M 一點，用 trace（map_snap、bicycle、搜尋半徑 FOLLOW_RADIUS_M）吸附；
trace 照舊經過 _ensure_matched（截斷、退化、端點離太遠都丟 RoutingError）。結果與規劃路徑的雙向 Hausdorff 距離
≤ MAX_HAUSDORFF_M 才收（kind="stroke"），否則回 None，由呼叫端把這段目標交給現行的分治吸附（fast）。

不用 edge_walk（spec 原本寫先試 edge_walk、失敗才 map_snap），因為實測 Valhalla 3.9.1 的兩個行為：
- edge_walk（thor::RouteMatcher）只拿起訖兩端的候選邊經過 loki 的通行過濾，中間的邊只比對幾何、不檢查通行方向與
  轉向限制：合成地圖上沿 oneway=yes、oneway=-1 逆向走的形狀照樣回傳成功（tests/test_fine.py 釘住這件事）。
  收下它就等於拿我們自己的路網圖當唯一的合法性依據，違反「合法性由 Valhalla 把關」。
- 它要求輸入點與 Valhalla 邊的形狀點逐一相同（check_shape），路網圖切長邊時插入的點就會讓它失敗，
  實際上大多會退回 map_snap。
map_snap 與現行 fast 吸附同一條路（meili 在候選之間用 bicycle 成本找路，單行道、禁行照舊），安全性與 fast 相同。
規劃路徑本來就貼在路上，所以搜尋半徑只要 FOLLOW_RADIUS_M；Valhalla 若繞路或抄捷徑，Hausdorff 會擋下來。
"""

import math

import numpy as np

from engine.matching.deadline import Deadline
from engine.matching.stroke import densify
from engine.matching.types import MatchRouter, Segment, segment_from
from engine.planner.plan import PlannedPiece
from engine.routing.geo import EARTH_RADIUS_M
from engine.routing.router import LonLat, RoutingError

FOLLOW_STEP_M = 15.0  # 交給 map_snap 的點最多相距這麼遠（spec §6.4：取樣 ≤ 15 m）
FOLLOW_RADIUS_M = 10.0  # map_snap 的搜尋半徑（spec §6.4）；_ensure_matched 的端點容許是它的 MAX_SNAP_GAP_RADII 倍
MAX_HAUSDORFF_M = 10.0  # Valhalla 結果與規劃路徑的雙向 Hausdorff 上限（spec §6.4）
HAUSDORFF_STEP_M = 1.0  # 量 Hausdorff 時沿折線的取樣間距
_CHUNK = 2048  # 點到線段距離分批算，每批這麼多個取樣點（記憶體上限約 _CHUNK × 線段數）


def _local(origin: LonLat, points: list[LonLat]) -> np.ndarray:
    """(lon, lat) → 以 origin 為原點的 (東, 北) 公尺（等距圓柱，同 search.geometry.lonlat_to_local）。"""
    xy = np.asarray(points, dtype=np.float64).reshape(-1, 2)
    rad = math.pi / 180
    return np.column_stack(((xy[:, 0] - origin[0]) * rad * EARTH_RADIUS_M * math.cos(origin[1] * rad),
                            (xy[:, 1] - origin[1]) * rad * EARTH_RADIUS_M))


def _samples(xy: np.ndarray, step: float) -> np.ndarray:
    """沿折線每 ≤ step 一點（含所有頂點）。"""
    if len(xy) < 2:
        return xy
    vec = xy[1:] - xy[:-1]
    counts = np.maximum(1, np.ceil(np.hypot(vec[:, 0], vec[:, 1]) / step).astype(np.int64))
    seg = np.repeat(np.arange(len(vec)), counts)
    frac = (np.arange(len(seg)) - np.repeat(np.cumsum(counts) - counts, counts)) / np.repeat(counts, counts)
    return np.vstack([xy[seg] + vec[seg] * frac[:, None], xy[-1:]])


def _distances(points: np.ndarray, poly: np.ndarray) -> np.ndarray:
    """每個點到折線 poly（線段，不是頂點）的最短距離；poly 只有一點時就是到那一點。"""
    if len(poly) < 2:
        return np.hypot(*(points - poly[0]).T)
    a, vec = poly[:-1], poly[1:] - poly[:-1]
    length2 = np.einsum("ij,ij->i", vec, vec)
    out = np.empty(len(points))
    for start in range(0, len(points), _CHUNK):
        rel = points[start:start + _CHUNK, None, :] - a[None]  # (k, m, 2)
        with np.errstate(invalid="ignore", divide="ignore"):
            t = np.einsum("kmj,mj->km", rel, vec) / length2
        t = np.clip(np.nan_to_num(t, nan=0.0), 0.0, 1.0)  # 零長度的線段：就是到它的端點
        diff = rel - vec[None] * t[..., None]
        out[start:start + _CHUNK] = np.hypot(diff[..., 0], diff[..., 1]).min(axis=1)
    return out


def hausdorff_m(a: list[LonLat], b: list[LonLat], *, step_m: float = HAUSDORFF_STEP_M) -> float:
    """兩條折線的雙向 Hausdorff 距離（公尺，局部平面）的上界。

    每條折線沿弧長每 ≤ step_m 取樣，量取樣點到另一條折線（線段）的距離取最大，再加 step_m / 2：
    到折線的距離沿折線走是 1-Lipschitz，取樣點之間最多再遠半個間距，所以結果不會低估。
    """
    origin = a[0]
    xa, xb = _local(origin, a), _local(origin, b)
    forward = float(_distances(_samples(xa, step_m), xb).max())
    backward = float(_distances(_samples(xb, step_m), xa).max())
    return max(forward, backward) + step_m / 2


def start_loop_mid_side(piece: PlannedPiece) -> PlannedPiece:
    """封閉筆畫整圈規劃成一段時（頭尾同一點），改從最長那段的中點開始、繞一圈回到那裡（同一條路徑，只換起點）。

    規劃器的圈從最尖的轉角開始；map_snap 在轉角上的起點可以吸附到兩條路的任一條（轉彎懲罰偏好不轉彎的那條），
    頭尾就吸到不同處，圈接不回來（合成正方形實測頭尾差 10 m）。從直路中間開始，頭尾只有一個吸附位置。
    edges 不跟著轉（follow 只用 points）。
    """
    points = piece.points
    if len(points) < 3 or points[0] != points[-1]:
        return piece
    origin = points[0]
    xy = _local(origin, points)
    longest = int(np.argmax(np.hypot(*(xy[1:] - xy[:-1]).T)))
    (ax, ay), (bx, by) = points[longest], points[longest + 1]
    middle = ((ax + bx) / 2, (ay + by) / 2)  # 等距圓柱是仿射轉換：經緯度的中點就在這段上
    loop = points[:-1]  # 去掉重複的終點
    return PlannedPiece([middle, *loop[longest + 1 :], *loop[: longest + 1], middle], piece.edges, piece.target_range,
                        piece.cost)


def follow(router: MatchRouter, piece: PlannedPiece, deadline: Deadline, *, stroke_index: int) -> list[Segment] | None:
    """讓 Valhalla 照著規劃路徑走一次。收下時回傳一段 kind="stroke"（點全部是 Valhalla 的結果）；
    Valhalla 走不了（RoutingError）或偏離規劃超過 MAX_HAUSDORFF_M 回傳 None。先檢查期限（逾時、取消照樣丟例外）。"""
    deadline.check()
    try:
        result = router.trace(densify(piece.points, FOLLOW_STEP_M), search_radius_m=FOLLOW_RADIUS_M)
    except RoutingError:
        return None
    if hausdorff_m(result.points, piece.points) > MAX_HAUSDORFF_M:
        return None
    return [segment_from("stroke", stroke_index, result)]
