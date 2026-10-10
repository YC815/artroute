"""執行基準測試並與 baseline 比較。"""

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from engine.bench.cases import Case, place
from engine.matching.deadline import Deadline, MatchTimeout
from engine.matching.service import AllStrokesFailed, StrokeInput, match_shape
from engine.matching.types import JOIN_TOLERANCE_M, MatchRouter, Segment
from engine.routing.geo import haversine_m, polyline_length_m
from engine.routing.router import EdgeInfo, LonLat, RoutingError

# 正式環境 /match 的時間上限（engine.app.create_app 的 match_timeout_s 預設 5.0 s；app 裡不是具名常數，改那邊時要同步）。
# 超過的案例在表格標 SLOW，僅供參考，不算退步。
PRODUCTION_TIMEOUT_MS = 5000

# 安全斷言沒過時 CaseResult.error 的值：案例算失敗（不能更新 baseline、原本成功就算退步）
UNSAFE = "UnsafeRoute"  # 相鄰路段有缺口（會畫成直線），或經過被禁行的 way
UNDETERMINED = "SafetyUndetermined"  # edges() 配不出經過哪些路段：無法證明安全，不算通過


class BenchRouter(MatchRouter, Protocol):
    def edges(self, points: list[LonLat]) -> list[EdgeInfo]: ...


@dataclass(frozen=True)
class CaseResult:
    name: str
    coverage: int
    cleanliness: int
    overall: int
    length_m: float
    target_length_m: float
    ms: int  # 預熱之後的單次取樣，不是多次平均；小差異不要當成效能變化
    error: str | None  # 失敗時為例外類別名或 UNSAFE / UNDETERMINED，成功為 None
    safety: str | None = None  # 安全斷言沒過的說明


def _target_length_m(strokes: list[StrokeInput]) -> float:
    """與 matching.service 的目標長度同一算法：封閉筆畫含收尾那一段。"""
    return sum(polyline_length_m(s.points + [s.points[0]] if s.closed else s.points) for s in strokes)


def _ways(router: BenchRouter, points: list[LonLat]) -> set[int]:
    """折線經過的 way。edge_walk 整段配不上時（實測：吸附結果在死巷原地折返 A→B→A），從中間的點切成兩半各自配：
    兩半共用切點，經過的路段一條都不會漏。切到只剩兩點還配不上，或配到的路段是空的，就丟 RoutingError。"""
    try:
        edges = router.edges(points)
    except RoutingError:
        if len(points) <= 2:
            raise
        mid = len(points) // 2
        return _ways(router, points[: mid + 1]) | _ways(router, points[mid:])
    if not edges and len(set(points)) >= 2:
        raise RoutingError("沒有配到任何路段")
    return {e.way_id for e in edges}


def safety_problem(
    router: BenchRouter, segments: list[Segment], denied_ids: frozenset[int]
) -> tuple[str, str] | None:
    """吸附結果的安全不變式；通過回傳 None，否則回傳 (UNSAFE 或 UNDETERMINED, 說明)。

    1. 相鄰路段的接點相距 ≤ JOIN_TOLERANCE_M：超過的話輸出會把缺口畫成一條地圖上不存在的直線。
    2. 每一段經過的路段（edges）都不是被禁行的 way。逐段查：每段都是 Valhalla 自己的輸出；配不上就切開再配（_ways）。
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
            used |= _ways(router, segment.points) & denied_ids
        except RoutingError as error:
            return UNDETERMINED, f"第 {i} 段（{segment.kind}）配不出經過的路段：{error}"
        checked += 1
    if not checked:
        return UNDETERMINED, "路線沒有任何可檢查的路段"
    if used:
        return UNSAFE, f"經過被禁行的 way {sorted(used)}"
    return None


def _run_one(router: BenchRouter, case: Case, timeout_s: float, denied_ids: frozenset[int]) -> CaseResult:
    strokes = place(case.shape, case.center, case.size_m, case.rotation_deg)
    started = time.perf_counter()
    try:
        result = match_shape(router, strokes, search_radius_m=None, deadline=Deadline(timeout_s))
    except (AllStrokesFailed, MatchTimeout) as error:
        ms = round((time.perf_counter() - started) * 1000)
        return CaseResult(case.name, 0, 0, 0, 0.0, round(_target_length_m(strokes), 1), ms, type(error).__name__)
    ms = round((time.perf_counter() - started) * 1000)
    error, safety = safety_problem(router, result.segments, denied_ids) or (None, None)
    s = result.score
    return CaseResult(
        case.name, s.coverage, s.cleanliness, s.overall,
        round(result.route_length_m, 1), round(result.target_length_m, 1), ms, error, safety,
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


def _regression(r: CaseResult, base: object, tolerance: int) -> str | None:
    if not isinstance(base, dict) or not isinstance(base.get("overall"), int | float):
        return f"{r.name}：baseline 條目缺少數值的 overall，無法比較（請用 --update-baseline 重建）"
    if r.error and not base.get("error"):
        return f"{r.name}：原本成功，現在失敗（{r.error}）"
    if r.overall < base["overall"] - tolerance:
        return f"{r.name}：overall {base['overall']} → {r.overall}"
    coverage = base.get("coverage")
    if isinstance(coverage, int | float) and r.coverage < coverage - tolerance:
        return f"{r.name}：coverage {coverage} → {r.coverage}"
    return None


def compare(results: list[CaseResult], baseline: dict[str, dict], tolerance: int = 3) -> list[str]:
    """回傳退步說明：overall 或 coverage 比 baseline 低超過 tolerance，或從成功變失敗。新案例不算退步。

    只看 overall 會漏掉「cleanliness 上升抵掉 coverage 掉到 0」的退步（ledger L120）。
    """
    checked = ((r, _regression(r, baseline[r.name], tolerance)) for r in results if r.name in baseline)
    return [problem for _, problem in checked if problem]


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
    return f"{'name':34} {'overall':>7} {'cov':>4} {'clean':>5} {'len_m':>9} {'target_m':>9} {'ms':>7}  note"


def format_row(r: CaseResult) -> str:
    slow = "SLOW" if r.ms > PRODUCTION_TIMEOUT_MS else ""
    note = " ".join(filter(None, [slow, r.error, r.safety]))
    return (
        f"{r.name:34} {r.overall:7} {r.coverage:4} {r.cleanliness:5} "
        f"{r.length_m:9.0f} {r.target_length_m:9.0f} {r.ms:7}  {note}"
    ).rstrip()


def summarize(results: list[CaseResult]) -> str:
    ok = [r for r in results if not r.error]
    slow = sum(r.ms > PRODUCTION_TIMEOUT_MS for r in results)
    mean = f"{sum(r.overall for r in ok) / len(ok):.1f}" if ok else "-"
    unsafe = sum(r.error == UNSAFE for r in results)
    undetermined = sum(r.error == UNDETERMINED for r in results)
    return (
        f"共 {len(results)} 案例：成功 {len(ok)}、失敗 {len(results) - len(ok)}、"
        f"SLOW {slow}（> {PRODUCTION_TIMEOUT_MS} ms，僅供參考）；成功案例平均 overall {mean}；"
        f"安全斷言（接點 ≤ {JOIN_TOLERANCE_M:g} m、不經過禁行 way）不安全 {unsafe}、無法判斷 {undetermined}"
    )
