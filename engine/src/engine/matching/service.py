"""整個圖形的吸附流程：驗證 → 逐部件吸附 → 串接 → 剪小刺與小圈 → 評分。

逐部件吸附有兩種（/match 的 quality）：fast 是現行的分治吸附（stroke.match_stroke）；fine 先用描形規劃器在可騎路網圖上
規劃，再讓 Valhalla 照著走（planner.follow），規劃不出來或照著走失敗的範圍退回 fast（P2.5 spec §6）。
串接之後的步驟兩種完全相同。
"""

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from engine.matching.compose import Part, Rematch, compose, route_points
from engine.matching.deadline import Deadline, MatchCancelled, MatchTimeout
from engine.matching.join import adjacent, connect
from engine.matching.prune import prune_loops
from engine.matching.score import Score, score, target_extent_m
from engine.matching.stroke import NO_ROUTE_MESSAGE, PARTIAL_MESSAGE, StrokeMatch, match_stroke, trace_whole
from engine.matching.types import JOIN_TOLERANCE_M, MatchRouter, MatchWarning, Segment, StartHint
from engine.planner.follow import follow, start_loop_mid_side
from engine.planner.graph import RideGraph
from engine.planner.plan import PlannedPiece, PlanStep, StrokePlan, plan_shape, region_bounds
from engine.routing.geo import haversine_m, polyline_length_m
from engine.routing.router import LonLat
from engine.routing.tiles import MAX_SEARCH_RADIUS_M, MIN_SEARCH_RADIUS_M

TAIWAN_BBOX = (118.1, 21.8, 122.1, 26.4)  # (min_lon, min_lat, max_lon, max_lat)，含金門、馬祖
MIN_TARGET_LENGTH_M = 50.0
MIN_DIAGONAL_M = 2.0  # 範圍太小 score() 無法評分（它的下限是 1 m，量法不同，多留一倍餘裕）
RADIUS_RATIO = 0.035  # 自動半徑 = 圖形對角線 × 這個比例，夾在 MIN_SEARCH_RADIUS_M–MAX_SEARCH_RADIUS_M（品質調查：0.02 太小，trace 大量斷段）
PLANNER_BUDGET_S = 2.5  # fine：規劃＋照著走的預算（秒，從 match_shape 開始算；P2.5 計畫 B3），用完的部分交給 fast
FINE_RESERVE_S = 2.5  # fine：期限剩下的時間至少留這麼多給退回 fast、串接、剪小刺與評分（預算因此可能比 PLANNER_BUDGET_S 短）

type Quality = Literal["fast", "fine"]

logger = logging.getLogger(__name__)


class OutsideTaiwan(Exception):
    pass


class TargetTooSmall(Exception):
    pass


class DiscontinuousRoute(RuntimeError):
    """相鄰路段之間有缺口：程式錯誤。輸出的話缺口會變成一條地圖上不存在的直線，所以寧可回內部錯誤。"""


class AllStrokesFailed(Exception):
    def __init__(self, warnings: list[MatchWarning]) -> None:
        super().__init__("所有部件都找不到可騎的道路")
        self.warnings = warnings


@dataclass(frozen=True)
class StrokeInput:
    points: list[LonLat]
    closed: bool


@dataclass(frozen=True)
class MatchResult:
    route_points: list[LonLat]
    route_length_m: float
    segments: list[Segment]
    score: Score
    target_length_m: float
    warnings: list[MatchWarning]


def _target(stroke: StrokeInput) -> list[LonLat]:
    return stroke.points + [stroke.points[0]] if stroke.closed else stroke.points


def _diagonal_m(strokes: list[StrokeInput]) -> float:
    lons = [lon for s in strokes for lon, _ in s.points]
    lats = [lat for s in strokes for _, lat in s.points]
    return haversine_m((min(lons), min(lats)), (max(lons), max(lats)))


def default_search_radius(strokes: list[StrokeInput]) -> float:
    return float(min(MAX_SEARCH_RADIUS_M, max(MIN_SEARCH_RADIUS_M, _diagonal_m(strokes) * RADIUS_RATIO)))


def _resolve_radius(search_radius_m: float | None, strokes: list[StrokeInput]) -> float:
    if search_radius_m is None:
        return default_search_radius(strokes)
    if not MIN_SEARCH_RADIUS_M <= search_radius_m <= MAX_SEARCH_RADIUS_M:  # NaN 的比較恆為 False，也會被擋下
        raise ValueError(f"搜尋半徑必須在 {MIN_SEARCH_RADIUS_M}–{MAX_SEARCH_RADIUS_M} m 之間：{search_radius_m}")
    return search_radius_m


def _check_inside_taiwan(strokes: list[StrokeInput]) -> None:
    min_lon, min_lat, max_lon, max_lat = TAIWAN_BBOX
    points = [point for stroke in strokes for point in stroke.points]
    if not all(min_lon <= lon <= max_lon and min_lat <= lat <= max_lat for lon, lat in points):
        raise OutsideTaiwan("圖形不在台灣範圍內")


def _stitch_closed(
    router: MatchRouter, deadline: Deadline, chains: list[list[Segment]]
) -> tuple[list[list[Segment]], bool]:
    """封閉筆畫：照筆畫順序把相鄰的 chain 接起來（connect：相鄰直接串，否則 connector＋裁切重試，與串接器同一套規則）。

    回傳 (chains, closed)。全部接上、而且最後一點與第一點相鄰（≤ 1 m，且接縫的轉彎能直接騎）→ 一條 chain、closed=True。
    收尾只接受相鄰、不另外補 route：只有一個部件時那段路沒有用，只會讓路線變長。
    中間有接不上的地方 → 從那裡斷開；最後一條與第一條相鄰就合成一條（最後一條在前），
    斷點只剩真正接不上的地方，不會在收尾處多出一個部件（star@taipei_daan@r30）。
    """
    runs = [chains[0]]
    for chain in chains[1:]:
        merged = connect(router, deadline, runs[-1], chain, "connector", None)
        if merged is None:
            runs.append(chain)
        else:
            runs[-1] = merged
    if len(runs) == 1:
        return runs, adjacent(router, deadline, runs[0], runs[0])
    if adjacent(router, deadline, runs[-1], runs[0]):
        return [runs[-1] + runs[0], *runs[1:-1]], False
    return runs, False


def _check_continuous(segments: list[Segment]) -> None:
    for i, (a, b) in enumerate(zip(segments, segments[1:])):
        gap_m = haversine_m(a.points[-1], b.points[0])
        if gap_m > JOIN_TOLERANCE_M:
            logger.error("路線第 %d、%d 段（%s → %s）之間有 %.1f m 的缺口，不輸出", i, i + 1, a.kind, b.kind, gap_m)
            raise DiscontinuousRoute(f"路線第 {i}、{i + 1} 段之間有 {gap_m:.1f} m 的缺口")


def _match_parts(
    router: MatchRouter,
    strokes: list[StrokeInput],
    match: Callable[[int], StrokeMatch],
    deadline: Deadline,
) -> tuple[list[Part], list[int], list[MatchWarning]]:
    """逐筆畫吸附（match(筆畫索引)：fast 或 fine）。回傳 (部件, 每個部件所屬的筆畫索引, 吸附階段的警告)。

    部件一律取自吸附結果的 chain（端點在道路上），不用原始輸入點。
    封閉筆畫的 chain 先依筆畫順序接回（_stitch_closed）。
    """
    parts: list[Part] = []
    owners: list[int] = []
    warnings: list[MatchWarning] = []
    for index in range(len(strokes)):
        matched = match(index)
        warnings.extend(matched.warnings)
        chains, closed = matched.chains, False
        if strokes[index].closed and chains:
            chains, closed = _stitch_closed(router, deadline, chains)
        reversible = not strokes[index].closed and len(chains) == 1 and not matched.warnings
        parts.extend(Part(chain, closed, reversible) for chain in chains)
        owners.extend(index for _ in chains)
    return parts, owners, warnings


def _fast_matcher(
    router: MatchRouter, targets: list[list[LonLat]], radius: float, deadline: Deadline
) -> Callable[[int], StrokeMatch]:
    def match(index: int) -> StrokeMatch:
        return match_stroke(
            router, targets[index], stroke_index=index, search_radius_m=radius, min_piece_m=2 * radius, deadline=deadline
        )

    return match


def _rematcher(
    router: MatchRouter, targets: list[list[LonLat]], owners: list[int], radius: float, deadline: Deadline
) -> Rematch:
    """串接器選了反向時呼叫：把整筆畫的點倒過來，整段只 trace 一次（trace_whole）。
    逆向走不通（RoutingError，含錯向保護）就回傳 None，部件照畫的方向走；不對半切、不補繞路，
    不然反向會用一條沒騎到畫的那條街的繞路，換掉乾淨的正向吸附。每個反向部件最多多 1 次 trace。"""

    def rematch(part_index: int) -> list[Segment] | None:
        owner = owners[part_index]
        backwards = trace_whole(
            router, targets[owner][::-1], stroke_index=owner, search_radius_m=radius, deadline=deadline
        )
        return None if backwards is None else [backwards]

    return rematch


# ---- fine：描形規劃器 → Valhalla 照著走，失敗的範圍退回 fast（P2.5 spec §6.4） ----


@dataclass
class _Fine:
    """一個 fine 請求共用的東西。"""

    router: MatchRouter
    deadline: Deadline
    radius: float  # 退回 fast 時的搜尋半徑（與 fast 相同）
    plans: list[StrokePlan]
    stop_at: float  # time.monotonic()：預算到這個時刻為止，之後的 piece 不再照著走，交給 fast
    followed: int = 0  # 照著走收下的 piece 數（紀錄用）
    rejected: int = 0  # 照著走失敗、或預算用完沒照著走的 piece 數（紀錄用）
    planned_ms: float = 0.0  # 取 region＋規劃的耗時（紀錄用）


def _start_fine(
    router: MatchRouter, graph: RideGraph, strokes: list[StrokeInput], radius: float, deadline: Deadline
) -> _Fine | None:
    """規劃所有筆畫；回傳 None 表示整張圖改走 fast。

    預算從現在起算 min(PLANNER_BUDGET_S, 期限剩餘 − FINE_RESERVE_S)，規劃與照著走共用：規劃先用（plan_shape 超過預算
    就把剩下的記成沒規劃），剩下的給照著走。預算 ≤ 0 時直接走 fast，不碰路網圖。
    期限（逾時、取消）照常丟例外；其他例外（壞掉的路網圖、規劃器的 bug）記下來後走 fast：fine 是選用的改善，
    不能讓 /match 因此變成 500。
    """
    started = time.monotonic()
    budget = min(PLANNER_BUDGET_S, deadline.remaining() - FINE_RESERVE_S)
    if budget <= 0:
        return None
    stop_at = started + budget
    points = [s.points for s in strokes]
    try:
        region = graph.region(*region_bounds(points))
        plans = plan_shape(
            region, points, [s.closed for s in strokes], deadline=deadline, budget_s=max(0.0, stop_at - time.monotonic())
        )
    except (MatchTimeout, MatchCancelled):
        raise
    except Exception:
        logger.exception("fine 規劃失敗，整張圖改走 fast")
        return None
    deadline.check()
    return _Fine(router, deadline, radius, plans, stop_at, planned_ms=(time.monotonic() - started) * 1000)


@dataclass(frozen=True)
class _Fallback:
    """沒照著走的目標範圍（沒規劃到、照著走失敗、或預算用完）：輸入點索引，含頭含尾；封閉筆畫以 mod n 解讀。"""

    start: int
    end: int


type _Item = list[Segment] | _Fallback


def _follow_pieces(fine: _Fine, steps: list[PlanStep], index: int, loop_points: int | None) -> list[_Item]:
    """依規劃的先後（StrokePlan.steps，不能拿範圍排序：不同的步驟可能有一樣的範圍）：照著走收下的 piece 換成那段
    Segment 的 list，其餘是 _Fallback。相鄰的 _Fallback 合成一個（規劃器的範圍在交界共用那段輸入線段），
    所以兩個 _Fallback 之間一定隔著收下的段。loop_points：封閉筆畫的輸入點數（開放筆畫為 None）；整圈一段時從直路中間
    開始照著走（start_loop_mid_side）。"""
    items: list[_Item] = []
    for step in steps:
        followed = None
        if isinstance(step, PlannedPiece):
            if time.monotonic() < fine.stop_at:
                path = step if loop_points is None else start_loop_mid_side(step, loop_points)
                followed = follow(fine.router, path, fine.deadline, stroke_index=index)
            if followed is None:
                fine.rejected += 1
            else:
                fine.followed += 1
        start, end = step.target_range if isinstance(step, PlannedPiece) else step
        if followed is not None:
            items.append(followed)
        elif items and isinstance(items[-1], _Fallback):
            items[-1] = _Fallback(min(items[-1].start, start), max(items[-1].end, end))
        else:
            items.append(_Fallback(start, end))
    return items


def _fallback_points(points: list[LonLat], items: list[_Item], k: int, closed: bool) -> list[LonLat]:
    """items[k]（_Fallback）要交給 fast 的目標點：範圍內的輸入點，頭尾換成相鄰收下那段的實際端點（已經在路上），
    所以 fast 從照著走停下的地方接著畫，不會從交界那段輸入線段的起點倒回去（同 stroke._solve 的串接）。
    封閉筆畫首尾相鄰：第一個與最後一個 item 互為前後。"""
    item = items[k]
    assert isinstance(item, _Fallback)
    n = len(points)
    before = items[k - 1] if k > 0 or (closed and len(items) > 1) else None
    after = items[(k + 1) % len(items)] if k + 1 < len(items) or (closed and len(items) > 1) else None
    head = before[-1].points[-1] if isinstance(before, list) else points[item.start % n]
    tail = after[0].points[0] if isinstance(after, list) else points[item.end % n]
    return [head, *(points[i % n] for i in range(item.start + 1, item.end)), tail]


def _wrap_fallbacks(items: list[_Item], n: int) -> list[_Item]:
    """封閉筆畫首尾相鄰：第一個與最後一個都是退回範圍時（規劃的起點落在兩個輸入點之間，交界那段兩邊都含），
    跨過起點合成一個 (最後一個的頭, 第一個的尾 + n)、放到最後，交給 fast 一次；不然那段會被前後兩次 fast 各畫一遍。
    每個退回範圍也截到最多一整圈（n）。"""
    if len(items) > 1 and isinstance(items[0], _Fallback) and isinstance(items[-1], _Fallback):
        items = [*items[1:-1], _Fallback(items[-1].start, items[0].end + n)]
    # 一個退回範圍最多一整圈：整圈規劃成一段（長 n + 1，起點在兩個輸入點之間）卻沒照著走時，不截的話交界那段會畫兩次
    return [_Fallback(i.start, min(i.end, i.start + n)) if isinstance(i, _Fallback) else i for i in items]


def _link_blocks(fine: _Fine, blocks: list[tuple[list[list[Segment]], bool]], index: int) -> list[list[Segment]]:
    """依目標順序把各段（照著走收下的段、退回 fast 的 chain）串成 chain。blocks 是 (chain 的 list, 這段有沒有警告)。

    只在段與段的交界用 join.connect 接（與 stroke._link 同一套：接點 ≤ 1 m、轉彎檢查、裁切重試，接不上就斷開），
    絕不直接串。同一個 fast 結果裡的多條 chain 是 match_stroke 刻意斷開的，不再接；有警告的 fast 結果兩側也不接：
    看不出它的斷點是不是在頭尾，可能正是被拒絕的繞路，不能拿 connect 補同一個缺口（stroke._link）。
    沒有 chain 的段（fast 也找不到路）同樣斷開。斷開的部分交給串接器。
    """
    chains: list[list[Segment]] = []
    joinable = False
    for block, warned in blocks:
        if not block:
            joinable = False
            continue
        first, *rest = block
        joined = connect(fine.router, fine.deadline, chains[-1], first, "detour", index) if joinable and not warned else None
        if joined is None:
            chains.append(first)
        else:
            chains[-1] = joined
        chains.extend(rest)
        joinable = not warned
    return chains


def _fine_matcher(fine: _Fine, strokes: list[StrokeInput]) -> Callable[[int], StrokeMatch]:
    def match(index: int) -> StrokeMatch:
        """forward：照著走收下的段＋退回 fast（match_stroke）的範圍，依目標順序串接。警告的語意同 match_stroke：
        一條 chain 都沒有是 no_route；有段找不到路、或斷成多條 chain 是 partial。
        整個筆畫都沒規劃時，交給 fast 的就是整個目標，結果與 fast 相同。"""
        stroke, plan = strokes[index], fine.plans[index]
        items = _follow_pieces(fine, plan.steps, index, len(stroke.points) if stroke.closed else None)
        if stroke.closed:
            items = _wrap_fallbacks(items, len(stroke.points))
        blocks: list[tuple[list[list[Segment]], bool]] = []
        for k, item in enumerate(items):
            if not isinstance(item, _Fallback):
                blocks.append(([item], False))
                continue
            points = _fallback_points(stroke.points, items, k, stroke.closed)
            if len(set(points)) < 2:
                continue  # 前後收下的兩段之間沒有要畫的：交給 connect 直接接
            matched = match_stroke(
                fine.router, points, stroke_index=index, search_radius_m=fine.radius, min_piece_m=2 * fine.radius,
                deadline=fine.deadline,
            )
            blocks.append((matched.chains, bool(matched.warnings)))
        chains = _link_blocks(fine, blocks, index)
        if not chains:
            return StrokeMatch(chains=[], warnings=[MatchWarning(index, "no_route", NO_ROUTE_MESSAGE)])
        if len(chains) > 1 or any(warned or not block for block, warned in blocks):
            return StrokeMatch(chains=chains, warnings=[MatchWarning(index, "partial", PARTIAL_MESSAGE)])
        return StrokeMatch(chains=chains, warnings=[])

    return match


def _fine_backward(fine: _Fine, stroke: StrokeInput, plan: StrokePlan, index: int) -> list[Segment] | None:
    """開放筆畫反向（從終點畫到起點）：規劃器的 backward 照著走，沒照著走的範圍各 trace_whole 一次（同 fast 的反向：
    只 trace 一次、不對半切、不補繞路，免得反向用繞路換掉乾淨的正向）。任何一段走不通、串不成一條 chain、或段與段之間
    要靠 connect 補一段 route（kind="detour"）才接得上，都回傳 None（部件照畫的方向走）。整個筆畫都沒規劃時就是 fast 的反向（整段倒過來 trace_whole 一次）。"""
    if plan.backward_steps is None:
        return None
    last = len(stroke.points) - 1
    points = stroke.points[::-1]  # 依反向畫的先後；範圍換成這個點列的索引

    def flip(step: PlanStep) -> PlanStep:
        if isinstance(step, PlannedPiece):
            start, end = step.target_range
            return PlannedPiece(step.points, step.edges, (last - start, last - end), step.cost)
        return (last - step[0], last - step[1])

    items = _follow_pieces(fine, [flip(step) for step in plan.backward_steps], index, None)
    blocks: list[tuple[list[list[Segment]], bool]] = []
    for k, item in enumerate(items):
        if not isinstance(item, _Fallback):
            blocks.append(([item], False))
            continue
        target = _fallback_points(points, items, k, False)
        if len(set(target)) < 2:
            continue
        whole = trace_whole(fine.router, target, stroke_index=index, search_radius_m=fine.radius, deadline=fine.deadline)
        if whole is None:
            return None
        blocks.append(([[whole]], False))
    chains = _link_blocks(fine, blocks, index)
    if len(chains) != 1 or any(segment.kind == "detour" for segment in chains[0]):
        return None  # 串不成一條，或要靠 connect 補一段 route（kind="detour"）才接得上：反向不補繞路
    return chains[0]


def _fine_rematcher(fine: _Fine, strokes: list[StrokeInput], owners: list[int]) -> Rematch:
    """串接器選了反向時才算（每個反向部件一次）：用規劃器的 backward，不再 trace_whole 整個筆畫。"""

    def rematch(part_index: int) -> list[Segment] | None:
        owner = owners[part_index]
        return _fine_backward(fine, strokes[owner], fine.plans[owner], owner)

    return rematch


def _dropped_warnings(owners: list[int], dropped: list[int]) -> list[MatchWarning]:
    """串接器丟掉的部件，每個筆畫一則：整個筆畫的部件都被丟光是 no_route，只丟一部分是 partial。"""
    gone = set(dropped)
    kept = {owner for i, owner in enumerate(owners) if i not in gone}
    out: list[MatchWarning] = []
    for index in sorted({owners[i] for i in dropped}):
        if index in kept:
            out.append(MatchWarning(index, "partial", "這段無法與其他部分相連，已略過"))
        else:
            out.append(MatchWarning(index, "no_route", "這個部件無法與其他部分相連，已略過"))
    return out


def _merge_warnings(matching: list[MatchWarning], dropped: list[MatchWarning]) -> list[MatchWarning]:
    """同一個筆畫只留一則：no_route 比 partial 嚴重（整個筆畫都不在路線上），其餘先到先得。"""
    merged = {w.stroke_index: w for w in matching}
    for w in dropped:
        if w.code == "no_route" or w.stroke_index not in merged:
            merged[w.stroke_index] = w
    return [merged[index] for index in sorted(merged)]


def _never_raising(rematch: Rematch) -> Rematch:
    """fine 的反向重新吸附出非期限的例外時記下來、照畫的方向走（回 None），不讓 /match 變成 500。"""

    def guarded(part_index: int) -> list[Segment] | None:
        try:
            return rematch(part_index)
        except (MatchTimeout, MatchCancelled):
            raise
        except Exception:
            logger.exception("fine 的反向重新吸附失敗，照畫的方向走")
            return None

    return guarded


def match_shape(
    router: MatchRouter,
    strokes: list[StrokeInput],
    *,
    search_radius_m: float | None,
    deadline: Deadline,
    start: StartHint | None = None,
    prune: bool = True,
    quality: Quality | None = None,
    graph: RideGraph | None = None,
) -> MatchResult:
    """prune：串接之後剪小刺與小圈（prune_loops）。False 時與沒有這一步的版本完全相同（bench 與標註工具產生「改前」用）。

    quality="fine" 而且有 graph（current build 的可騎路網圖）時逐部件吸附改用規劃器（見模組說明），否則就是 fast：
    與沒有這兩個參數的版本完全相同。fine 的預算是 0 或規劃失敗（非期限的例外）時也整張圖走 fast（_start_fine）。串接、剪小刺、連續性檢查與評分兩種都一樣。
    """
    _check_inside_taiwan(strokes)
    targets = [_target(s) for s in strokes]
    target_length = sum(polyline_length_m(t) for t in targets)
    if target_length < MIN_TARGET_LENGTH_M or _diagonal_m(strokes) < MIN_DIAGONAL_M:
        raise TargetTooSmall(f"圖形太小：總長須至少 {MIN_TARGET_LENGTH_M:.0f} m，且範圍須至少 {MIN_DIAGONAL_M:.0f} m")
    radius = _resolve_radius(search_radius_m, strokes)

    rematch: Rematch
    fine = _start_fine(router, graph, strokes, radius, deadline) if quality == "fine" and graph is not None else None
    if fine is not None:
        try:
            parts, owners, warnings = _match_parts(router, strokes, _fine_matcher(fine, strokes), deadline)
        except (MatchTimeout, MatchCancelled):
            raise
        except Exception:  # 照著走、串接的程式錯誤：fine 是選用的改善，不能讓 /match 變成 500，整張圖改走 fast
            logger.exception("fine 吸附失敗，整張圖改走 fast")
            fine = None
    if fine is not None:
        rematch = _never_raising(_fine_rematcher(fine, strokes, owners))
    else:
        parts, owners, warnings = _match_parts(router, strokes, _fast_matcher(router, targets, radius, deadline), deadline)
        rematch = _rematcher(router, targets, owners, radius, deadline)
    if not parts:
        raise AllStrokesFailed(warnings)

    composition = compose(router, parts, deadline, rematch=rematch, start=start)
    if fine is not None:  # 反向的照著走在串接器裡，所以串接之後才記
        logger.info("fine：取 region＋規劃 %.0f ms，照著走收下 %d 段、沒收（退回 fast）%d 段",
                    fine.planned_ms, fine.followed, fine.rejected)
    warnings = _merge_warnings(warnings, _dropped_warnings(owners, composition.dropped))
    segments = composition.segments
    if prune:
        _check_continuous(segments)
        segments = prune_loops(router, segments, targets, deadline, extent_m=target_extent_m(targets))
    _check_continuous(segments)
    route = route_points(segments)
    return MatchResult(
        route_points=route,
        route_length_m=sum(s.length_m for s in segments),
        segments=segments,
        score=score(targets, route),
        target_length_m=target_length,
        warnings=warnings,
    )
