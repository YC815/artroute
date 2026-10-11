"""執行基準測試並與 baseline 比較。"""

import json
import time
from collections.abc import Callable
from dataclasses import KW_ONLY, dataclass, field
from pathlib import Path
from typing import Protocol

from engine.bench.cases import Case, place
from engine.matching.deadline import Deadline, MatchTimeout
from engine.matching.score import SCORE_VERSION, score_v1
from engine.matching.service import AllStrokesFailed, StrokeInput, match_shape
from engine.matching.turns import head_points, rideable, tail_points
from engine.matching.types import JOIN_TOLERANCE_M, MatchRouter, Segment
from engine.routing.geo import haversine_m, polyline_length_m
from engine.routing.router import EdgeInfo, LonLat, RoutingError

# 正式環境 /match 的時間上限（engine.app.create_app 的 match_timeout_s 預設 5.0 s；app 裡不是具名常數，改那邊時要同步）。
# 超過的案例在表格標 SLOW，僅供參考，不算退步。
PRODUCTION_TIMEOUT_MS = 5000

# 安全斷言沒過時 CaseResult.error 的值：一律算退步（不看 baseline），也不能寫進 baseline（--force 也一樣）
UNSAFE = "UnsafeRoute"  # 相鄰路段有缺口（會畫成直線）、經過被禁行的 way，或接點轉彎 Valhalla 不會直接騎
UNDETERMINED = "SafetyUndetermined"  # edges() 配不出經過哪些路段：無法證明安全，不算通過
SAFETY_ERRORS = frozenset({UNSAFE, UNDETERMINED})
# 只量測、不改路線：要不要禁止這些路段待使用者決定（品質調查 §5.4）
REPORTED_USES = ("footway", "steps", "parking_aisle")


class BenchRouter(MatchRouter, Protocol):
    def edges(self, points: list[LonLat]) -> list[EdgeInfo]: ...


@dataclass(frozen=True)
class CaseResult:
    name: str
    coverage: int  # coverage／cleanliness／overall 是 score()（score_version 版）的分數
    cleanliness: int
    overall: int
    length_m: float
    target_length_m: float
    ms: int  # 預熱之後的單次取樣，不是多次平均；小差異不要當成效能變化
    error: str | None  # 失敗時為例外類別名或 UNSAFE / UNDETERMINED，成功為 None
    safety: str | None = None  # 安全斷言沒過的說明
    use_m: dict[str, float] = field(default_factory=dict)  # 走在 REPORTED_USES 各用途上的公尺數（只量測）
    bad_turns: int = 0  # 轉彎 Valhalla 不會直接騎的接點數（> 0 就是 UNSAFE）
    _: KW_ONLY
    # 同一條路線的 v1 分數（score_v1）：和還是 v1 的 baseline 比，路線退步才不會被評分改版蓋掉；失敗的案例為 0
    legacy_coverage: int
    legacy_cleanliness: int
    legacy_overall: int
    score_version: int = SCORE_VERSION


def _targets(strokes: list[StrokeInput]) -> list[list[LonLat]]:
    """與 matching.service 的目標同一算法：封閉筆畫含收尾那一段（長度與評分都用它）。"""
    return [s.points + [s.points[0]] if s.closed else s.points for s in strokes]


def _target_length_m(strokes: list[StrokeInput]) -> float:
    return sum(polyline_length_m(t) for t in _targets(strokes))


def _edges(router: BenchRouter, points: list[LonLat]) -> list[EdgeInfo]:
    """折線經過的路段（edge）。edge_walk 整段配不上時（實測：吸附結果在死巷原地折返 A→B→A），從中間的點切成兩半各自配：
    兩半共用切點，經過的路段一條都不會漏。切到只剩兩點還配不上，或配到的路段是空的，就丟 RoutingError。"""
    try:
        edges = router.edges(points)
    except RoutingError:
        if len(points) <= 2:
            raise
        mid = len(points) // 2
        return _edges(router, points[: mid + 1]) + _edges(router, points[mid:])
    if not edges and len(set(points)) >= 2:
        raise RoutingError("沒有配到任何路段")
    return edges


def safety_problem(
    router: BenchRouter, segments: list[Segment], denied_ids: frozenset[int]
) -> tuple[str, str] | None:
    """吸附結果的安全不變式；通過回傳 None，否則回傳 (UNSAFE 或 UNDETERMINED, 說明)。

    1. 相鄰路段的接點相距 ≤ JOIN_TOLERANCE_M：超過的話輸出會把缺口畫成一條地圖上不存在的直線。
    2. 每一段經過的路段（edges）都不是被禁行的 way。逐段查：每段都是 Valhalla 自己的輸出；配不上就切開再配（_edges）。
    """
    for i, (a, b) in enumerate(zip(segments, segments[1:])):
        gap_m = haversine_m(a.points[-1], b.points[0])
        if gap_m > JOIN_TOLERANCE_M:
            return UNSAFE, f"第 {i}、{i + 1} 段之間缺口 {gap_m:.1f} m"
    used: set[int] = set()
    checked = 0
    for i, segment in enumerate(segments):
        if len(set(segment.points)) < 2:
            continue  # 封閉圈旋轉切出的零長度片段：沒有經過任何路段
        try:
            used |= {e.way_id for e in _edges(router, segment.points)} & denied_ids
        except RoutingError as error:
            return UNDETERMINED, f"第 {i} 段（{segment.kind}）配不出經過的路段：{error}"
        checked += 1
    if not checked:
        return UNDETERMINED, "路線沒有任何可檢查的路段"
    if used:
        return UNSAFE, f"經過被禁行的 way {sorted(used)}"
    return None


TURN_CHECK_TIMEOUT_S = 60.0  # 每個案例檢查接點轉彎的時間上限（bench 不計時）


def bad_turns(router: BenchRouter, segments: list[Segment]) -> int:
    """相鄰路段的接點中，轉彎 Valhalla 不會直接騎的個數（matching.turns.rideable）。
    探針跨段，所以零長度片段或很短的 link 兩側的轉彎也量得到。"""
    deadline = Deadline(TURN_CHECK_TIMEOUT_S)
    return sum(
        not rideable(router, deadline, tail_points(segments[: i + 1]), head_points(segments[i + 1 :]))
        for i in range(len(segments) - 1)
    )


def _turn_problem(count: int) -> tuple[str, str] | None:
    return (UNSAFE, f"{count} 個接點的轉彎 Valhalla 不會直接騎") if count else None


def _measurable_edges(router: BenchRouter, segment: Segment) -> list[EdgeInfo]:
    if len(set(segment.points)) < 2:
        return []  # 零長度片段沒有經過任何路段
    try:
        return _edges(router, segment.points)
    except RoutingError:
        return []  # 配不出路段的段落略過；safety_problem 已經把這種案例記成 UNDETERMINED


def use_meters(router: BenchRouter, segments: list[Segment]) -> dict[str, float]:
    """路線走在 REPORTED_USES 各用途上的公尺數（edge_walk 的 edge.length 加總，四捨五入到 0.1 m）。"""
    edges = [edge for segment in segments for edge in _measurable_edges(router, segment)]
    return {use: round(sum((e.length_km for e in edges if e.use == use), 0.0) * 1000, 1) for use in REPORTED_USES}


def _run_one(router: BenchRouter, case: Case, timeout_s: float, denied_ids: frozenset[int]) -> CaseResult:
    strokes = place(case.shape, case.center, case.size_m, case.rotation_deg)
    started = time.perf_counter()
    try:
        result = match_shape(router, strokes, search_radius_m=None, deadline=Deadline(timeout_s))
    except (AllStrokesFailed, MatchTimeout) as error:
        ms = round((time.perf_counter() - started) * 1000)
        return CaseResult(
            case.name, 0, 0, 0, 0.0, round(_target_length_m(strokes), 1), ms, type(error).__name__,
            legacy_coverage=0, legacy_cleanliness=0, legacy_overall=0,
        )
    ms = round((time.perf_counter() - started) * 1000)
    turns = bad_turns(router, result.segments)
    error, safety = safety_problem(router, result.segments, denied_ids) or _turn_problem(turns) or (None, None)
    s, legacy = result.score, score_v1(_targets(strokes), result.route_points)
    return CaseResult(
        case.name, s.coverage, s.cleanliness, s.overall,
        round(result.route_length_m, 1), round(result.target_length_m, 1), ms, error, safety,
        use_m=use_meters(router, result.segments), bad_turns=turns,
        legacy_coverage=legacy.coverage, legacy_cleanliness=legacy.cleanliness, legacy_overall=legacy.overall,
    )


def _warm_up(router: BenchRouter, cases: list[Case], timeout_s: float, denied_ids: frozenset[int]) -> None:
    """每個不同的地點先跑一次、不計時：讓 Valhalla 載入該處的 tiles，之後量到的 ms 才是熱的。"""
    first_at: dict[tuple[float, float], Case] = {}
    for case in cases:
        first_at.setdefault(case.center, case)
    for case in first_at.values():
        _run_one(router, case, timeout_s, denied_ids)


def run_cases(
    router: BenchRouter,
    cases: list[Case],
    timeout_s: float = 30.0,
    *,
    denied_ids: frozenset[int],
    warm_up: bool = True,
    on_result: Callable[[CaseResult], None] | None = None,
) -> list[CaseResult]:
    """依序跑案例；每跑完一個立刻呼叫 on_result（讓呼叫端邊跑邊印）。denied_ids 是 build 標記為禁行的 way。"""
    if warm_up:
        _warm_up(router, cases, timeout_s, denied_ids)
    results: list[CaseResult] = []
    for case in cases:
        result = _run_one(router, case, timeout_s, denied_ids)
        results.append(result)
        if on_result is not None:
            on_result(result)
    return results


def is_legacy(entry: object) -> bool:
    """baseline 條目是不是 v1 評分（沒有 score_version，或版本不是現在的 SCORE_VERSION）。"""
    return not (isinstance(entry, dict) and entry.get("score_version") == SCORE_VERSION)


def _regression(r: CaseResult, base: object, tolerance: int) -> str | None:
    if not isinstance(base, dict) or not isinstance(base.get("overall"), int | float):
        return f"{r.name}：baseline 條目缺少數值的 overall，無法比較（請用 --update-baseline 重建）"
    if r.error and not base.get("error"):
        return f"{r.name}：原本成功，現在失敗（{r.error}）"
    # v1 baseline 比本次路線的 v1 分數：評分改版不會被當成退步，路線本身的退步也不會被新評分蓋掉
    if is_legacy(base):
        overall, coverage, label = r.legacy_overall, r.legacy_coverage, "（v1 評分）"
    else:
        overall, coverage, label = r.overall, r.coverage, " "
    if overall < base["overall"] - tolerance:
        return f"{r.name}：overall{label}{base['overall']} → {overall}"
    base_coverage = base.get("coverage")
    if isinstance(base_coverage, int | float) and coverage < base_coverage - tolerance:
        return f"{r.name}：coverage{label}{base_coverage} → {coverage}"
    return None


def compare(results: list[CaseResult], baseline: dict[str, dict], tolerance: int = 3) -> list[str]:
    """回傳退步說明。

    安全斷言沒過（SAFETY_ERRORS）一律算，不看 baseline：新案例、baseline 原本就失敗的案例也算，
    否則一筆寫進 baseline 的 UnsafeRoute 之後就永遠不會再被抓到。
    其餘只比 baseline 有的案例：overall 或 coverage 比 baseline 低超過 tolerance，或從成功變失敗。
    只看 overall 會漏掉「cleanliness 上升抵掉 coverage 掉到 0」的退步（ledger L120）。
    條目有 score_version == SCORE_VERSION 就比 v2 分數，否則（v1 baseline）比本次的 legacy（v1）分數。
    """
    problems: list[str] = []
    for r in results:
        if r.error in SAFETY_ERRORS:
            problems.append(f"{r.name}：安全斷言沒過（{r.error}：{r.safety}）")
        elif r.name in baseline and (problem := _regression(r, baseline[r.name], tolerance)):
            problems.append(problem)
    return problems


def case_set_changes(results: list[CaseResult], baseline: dict[str, dict]) -> list[str]:
    """案例集合的差異（只是提示，不算退步）：baseline 有但本次沒跑的、本次有但 baseline 沒有的。"""
    ran = {r.name for r in results}
    gone = [f"baseline 有、本次沒跑：{name}" for name in sorted(set(baseline) - ran)]
    fresh = [f"本次新增、baseline 沒有：{name}" for name in sorted(ran - set(baseline))]
    return gone + fresh


def load_baseline(path: Path) -> dict[str, dict]:
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"讀不到 baseline {path}：{error}") from error
    if not isinstance(data, dict):
        raise ValueError(f"baseline {path} 的最上層必須是 JSON 物件")
    return data


def format_header() -> str:
    return (
        f"{'name':34} {'overall':>7} {'v1':>4} {'cov':>4} {'clean':>5} {'len_m':>9} {'target_m':>9} {'ms':>7} "
        f"{'foot_m':>7} {'steps_m':>7} {'park_m':>7}  note"
    )


def format_row(r: CaseResult) -> str:
    slow = "SLOW" if r.ms > PRODUCTION_TIMEOUT_MS else ""
    note = " ".join(filter(None, [slow, r.error, r.safety]))
    uses = " ".join(f"{r.use_m.get(use, 0.0):7.0f}" for use in REPORTED_USES)
    return (
        f"{r.name:34} {r.overall:7} {r.legacy_overall:4} {r.coverage:4} {r.cleanliness:5} "
        f"{r.length_m:9.0f} {r.target_length_m:9.0f} {r.ms:7} {uses}  {note}"
    ).rstrip()


def summarize(results: list[CaseResult]) -> str:
    ok = [r for r in results if not r.error]
    slow = sum(r.ms > PRODUCTION_TIMEOUT_MS for r in results)
    mean = f"{sum(r.overall for r in ok) / len(ok):.1f}" if ok else "-"
    mean_v1 = f"{sum(r.legacy_overall for r in ok) / len(ok):.1f}" if ok else "-"
    unsafe = sum(r.error == UNSAFE for r in results)
    undetermined = sum(r.error == UNDETERMINED for r in results)
    uses = "／".join(f"{sum(r.use_m.get(use, 0.0) for r in ok):.0f}" for use in REPORTED_USES)
    return (
        f"共 {len(results)} 案例：成功 {len(ok)}、失敗 {len(results) - len(ok)}、"
        f"SLOW {slow}（> {PRODUCTION_TIMEOUT_MS} ms，僅供參考）；成功案例平均 overall {mean}（v1 評分 {mean_v1}）；"
        f"安全斷言（接點 ≤ {JOIN_TOLERANCE_M:g} m、不經過禁行 way、接點轉彎能直接騎）不安全 {unsafe}、無法判斷 {undetermined}；"
        f"轉彎不能直接騎的接點 {sum(r.bad_turns for r in results)} 個；"
        f"footway／steps／parking_aisle 合計 {uses} m（成功案例，只量測）"
    )
