"""接點轉彎檢查（品質調查 §5.3）：兩段 Valhalla 結果接起來的地方，Valhalla 自己會不會直接騎過去。

從接點沿路線往回走 TURN_PROBE_M 得到 p、往前走 TURN_PROBE_M 得到 q。p→接點→q 的轉角超過 TURN_CHECK_DEG 時，
route(p, q)（shortest）的起訖要落在 p、q 的 JOIN_TOLERANCE_M 內，長度不能比沿原路線經過接點多 TURN_SLACK_M 以上；
繞一大圈代表這個轉彎不能直接騎（轉向限制、立體交叉在平面上剛好是同一點……），接點視同沒接上。
兩個探針落在同一處（原地迴轉）沒辦法用 route 量，不檢查；可量的路線不到 JOIN_TOLERANCE_M 也不檢查。
探針折回接點（走了 TURN_PROBE_M 以內的路，終點卻落回接點 JOIN_TOLERANCE_M 內，走的路比直線距離多出超過
JOIN_TOLERANCE_M）時量不出通過接點的方向，但這不是迴轉，所以不放行：視同不能騎；兩邊都折回更是。
彎曲但很短的臂（走的路只比直線多一點）不算折回，照一般轉角量。
"""

import math

from engine.matching.deadline import Deadline
from engine.matching.types import JOIN_TOLERANCE_M, MatchRouter, Segment, legal_route
from engine.routing.geo import haversine_m, polyline_length_m
from engine.routing.router import LonLat

TURN_CHECK_DEG = 30.0
TURN_PROBE_M = 8.0
TURN_SLACK_M = 3.0


def turn_deg(before: LonLat, at: LonLat, after: LonLat) -> float:
    """局部平面上 before→at 與 at→after 兩個方向的夾角：0 = 直行，180 = 原地迴轉。
    有一邊長度為 0（方向不存在）時回傳 180：量不出來就當最壞的情況，不能當成直行。"""
    kx, ky = 111_320.0 * math.cos(math.radians(at[1])), 110_574.0
    ux, uy = (at[0] - before[0]) * kx, (at[1] - before[1]) * ky
    vx, vy = (after[0] - at[0]) * kx, (after[1] - at[1]) * ky
    nu, nv = math.hypot(ux, uy), math.hypot(vx, vy)
    if nu == 0 or nv == 0:
        return 180.0
    return math.degrees(math.acos(max(-1.0, min(1.0, (ux * vx + uy * vy) / (nu * nv)))))


def _walk(points: list[LonLat], meters: float) -> tuple[LonLat, float]:
    """從 points[0] 沿折線走 meters 公尺（不夠長就停在最後一點）；回傳 (到達的點, 實際走的距離)。"""
    walked = 0.0
    for a, b in zip(points, points[1:]):
        step = haversine_m(a, b)
        if step > 0 and walked + step >= meters:
            t = (meters - walked) / step
            return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t), meters
        walked += step
    return points[-1], walked


def tail_points(segments: list[Segment]) -> list[LonLat]:
    """路段串最後的 TURN_PROBE_M 公尺（不夠長就往前跨段）：rideable 的 incoming。"""
    out: list[LonLat] = []
    for segment in reversed(segments):
        out = segment.points + out
        if polyline_length_m(out) >= TURN_PROBE_M:
            break
    return out


def head_points(segments: list[Segment]) -> list[LonLat]:
    """路段串最前的 TURN_PROBE_M 公尺（不夠長就往後跨段）：rideable 的 outgoing。"""
    out: list[LonLat] = []
    for segment in segments:
        out = out + segment.points
        if polyline_length_m(out) >= TURN_PROBE_M:
            break
    return out


def _folds_back(walked_m: float, chord_m: float) -> bool:
    """探針走了 walked_m 公尺，終點離接點只有 chord_m：路線折回接點本身（彎曲的短臂走的路只比直線多一點，不算）。"""
    return chord_m <= JOIN_TOLERANCE_M and walked_m - chord_m > JOIN_TOLERANCE_M


def rideable(router: MatchRouter, deadline: Deadline, incoming: list[LonLat], outgoing: list[LonLat]) -> bool:
    """incoming 的終點接 outgoing 的起點（相距 ≤ JOIN_TOLERANCE_M）時，Valhalla 會不會直接騎過這個接點。"""
    p, back_m = _walk(incoming[::-1], TURN_PROBE_M)
    q, ahead_m = _walk(outgoing, TURN_PROBE_M)
    if min(back_m, ahead_m) < JOIN_TOLERANCE_M:
        return True
    at = incoming[-1]
    folded_p, folded_q = _folds_back(back_m, haversine_m(p, at)), _folds_back(ahead_m, haversine_m(q, at))
    if folded_p and folded_q:
        return False  # 兩邊都折回：p≈q 看起來像迴轉，其實兩條臂都量不出方向
    if haversine_m(p, q) <= JOIN_TOLERANCE_M:
        return True
    if folded_p or folded_q:
        return False  # 臂長 0：探針折回接點，量不出方向（不是迴轉），不放行
    if turn_deg(p, at, q) <= TURN_CHECK_DEG:
        return True
    result = legal_route(router, deadline, p, q, JOIN_TOLERANCE_M)
    direct_m = back_m + haversine_m(at, outgoing[0]) + ahead_m
    return result is not None and result.length_km * 1000 <= direct_m + TURN_SLACK_M
