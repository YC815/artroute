"""執行基準測試並與 baseline 比較。"""

import time
from dataclasses import dataclass

from engine.bench.cases import Case, place
from engine.matching.deadline import Deadline, MatchTimeout
from engine.matching.service import AllStrokesFailed, match_shape
from engine.matching.types import MatchRouter


@dataclass(frozen=True)
class CaseResult:
    name: str
    coverage: int
    cleanliness: int
    overall: int
    length_m: float
    target_length_m: float
    ms: int
    error: str | None  # 失敗時為例外類別名，成功為 None


def _run_one(router: MatchRouter, case: Case, timeout_s: float) -> CaseResult:
    strokes = place(case.shape, case.center, case.size_m, case.rotation_deg)
    started = time.perf_counter()
    try:
        result = match_shape(router, strokes, search_radius_m=None, deadline=Deadline(timeout_s))
    except (AllStrokesFailed, MatchTimeout) as error:
        ms = round((time.perf_counter() - started) * 1000)
        return CaseResult(case.name, 0, 0, 0, 0.0, 0.0, ms, type(error).__name__)
    ms = round((time.perf_counter() - started) * 1000)
    s = result.score
    return CaseResult(
        case.name, s.coverage, s.cleanliness, s.overall,
        round(result.route_length_m, 1), round(result.target_length_m, 1), ms, None,
    )


def run_cases(router: MatchRouter, cases: list[Case], timeout_s: float = 30.0) -> list[CaseResult]:
    return [_run_one(router, case, timeout_s) for case in cases]


def compare(results: list[CaseResult], baseline: dict[str, dict], tolerance: int = 3) -> list[str]:
    """回傳退步說明：overall 比 baseline 低超過 tolerance，或從成功變失敗。新案例不算退步。"""
    problems: list[str] = []
    for r in results:
        base = baseline.get(r.name)
        if base is None:
            continue
        if r.error and not base.get("error"):
            problems.append(f"{r.name}：原本成功，現在失敗（{r.error}）")
        elif r.overall < base["overall"] - tolerance:
            problems.append(f"{r.name}：overall {base['overall']} → {r.overall}")
    return problems
