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
from scipy.spatial import cKDTree

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
_BATCH = 65_536  # KD-tree 每批查這麼多個取樣點，批次之間檢查期限


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


def _farthest(points: np.ndarray, tree: cKDTree, deadline: Deadline | None) -> float:
    """points 裡離 tree 的點最遠的那一點的距離。分批查，批次之間檢查期限。"""
    worst = 0.0
    for start in range(0, len(points), _BATCH):
        if deadline is not None:
            deadline.check()
        distance, _ = tree.query(points[start:start + _BATCH])
        worst = max(worst, float(distance.max()))
    return worst


def hausdorff_m(
    a: list[LonLat], b: list[LonLat], *, step_m: float = HAUSDORFF_STEP_M, deadline: Deadline | None = None
) -> float:
    """兩條折線的雙向 Hausdorff 距離（公尺，局部平面）的上界，O(N log N)。

    做法：兩條折線各沿弧長每 ≤ step_m 取樣（含所有頂點），用 KD-tree 量 A 的每個取樣點到 B 最近的取樣點（反之亦然），
    取最大，再加 step_m / 2。設 s = step_m，S_A、S_B 是兩邊的取樣點：
    - 不低估：A 上任一點 a 離某個取樣點 a_i 不到 s / 2（每段等分、間距 ≤ s），到折線的距離是 1-Lipschitz，
      所以 d(a, B) ≤ d(a_i, B) + s / 2 ≤ d(a_i, S_B) + s / 2（S_B 在 B 上）。反方向同理。
    - 最多高估 s：B 上離 a_i 最近的點 q 離某個取樣點不到 s / 2，所以 d(a_i, S_B) ≤ d(a_i, B) + s / 2。
    deadline：批次之間檢查（逾時、取消照常丟例外）。
    """
    origin = a[0]
    sa, sb = _samples(_local(origin, a), step_m), _samples(_local(origin, b), step_m)
    forward = _farthest(sa, cKDTree(sb), deadline)
    backward = _farthest(sb, cKDTree(sa), deadline)
    return max(forward, backward) + step_m / 2


def start_loop_mid_side(piece: PlannedPiece, n: int) -> PlannedPiece:
    """封閉筆畫（n 個輸入點）整圈規劃成一段時（頭尾同一點，而且目標範圍涵蓋整圈：end − start ≥ n），
    改從最長那段的中點開始、繞一圈回到那裡（同一條路徑，只換起點）。只有頭尾同一點不夠：一段只畫半圈的路徑也可能剛好
    繞回起點，換了起點它在目標上的先後就亂了。

    規劃器的圈從最尖的轉角開始；map_snap 在轉角上的起點可以吸附到兩條路的任一條（轉彎懲罰偏好不轉彎的那條），
    頭尾就吸到不同處，圈接不回來（合成正方形實測頭尾差 10 m）。從直路中間開始，頭尾只有一個吸附位置。
    edges 不跟著轉（follow 只用 points）。
    """
    points = piece.points
    start, end = piece.target_range
    if len(points) < 3 or points[0] != points[-1] or end - start < n:
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
    if hausdorff_m(result.points, piece.points, deadline=deadline) > MAX_HAUSDORFF_M:
        return None
    return [segment_from("stroke", stroke_index, result)]
