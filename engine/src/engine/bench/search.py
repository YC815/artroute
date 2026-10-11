"""python -m engine.bench.search：在真實路網上量自動搜尋（在引擎容器內執行）。

每個案例照正式的 /search 流程跑（每次重建 SEARCH_ROUTERS 個 Router、讀 current build 的可騎道路索引），量：
- 完成條件（spec §7）：從開始搜尋到產生前 5 名的時間 ≤ 30 s，而且有 5 個候選；
- 安全不變式：每個回傳的候選都要通過吸附 bench 的同一套斷言（相鄰路段 ≤ 1 m、不經過禁行 way、接點轉彎能直接騎），
  樓梯 0 m；沒過一律算失敗；
- 粗篩成本排名與精算 overall 的 Spearman 相關（spec §6.3，調整 K 的依據；只是量測）；
- 精算全部粗篩候選的效益（spec §8 第 29 列）：前 5 名來自粗篩第幾名，以及只精算前 PREVIOUS_K 名時的前 5 名
  （從同一次搜尋的結果篩出來；舊做法實際跑到的候選只會更少，所以差距是下限）。
每個地點的第一個案例要載入該處的 tiles（冷），表格標 cold；完成條件對冷熱一視同仁。
候選另記 score() 的方向項與同一條路線的 v2 分數（P2.5 spec §4.5）；`--svg-dir` 為每個案例的前 5 名畫疊圖，
`--turn-penalty-factor`／`--sigma-z` 改吸附參數（bench.options）。
"""

import argparse
import json
import os
import sys
import time
from collections.abc import Callable
from dataclasses import KW_ONLY, asdict, dataclass, field, replace
from pathlib import Path

import numpy as np

from engine.bench.options import (
    add_match_options,
    add_router_options,
    describe_match_options,
    describe_router_options,
    match_options,
    router_options,
)
from engine.bench.runner import BenchRouter, bad_turns, safety_problem, stroke_targets, use_meters
from engine.bench.search_cases import SEARCH_CASES, SearchCase
from engine.bench.svg import case_svg, safe_name
from engine.data.build import DENIED_FILE, load_denied
from engine.data.layout import DataLayout
from engine.matching.deadline import Deadline
from engine.matching.score import SCORE_VERSION, rank, score_v2
from engine.routing.router import Router
from engine.search.geometry import place
from engine.search.roads import RoadDataMissing, RoadIndex
from engine.search.service import run_search
from engine.search.types import Candidate, Evaluation, Progress, SearchParams

TARGET_S = 30.0  # spec §7 的完成條件
HARD_TIMEOUT_S = 60.0  # 與 /search 相同的硬上限
TOP_N = 5
PREVIOUS_K = 10  # P2 部署時精算的候選數（spec §8 第 27 列）


@dataclass(frozen=True)
class CandidateRow:
    overall: int
    coverage: int
    cleanliness: int
    length_km: float
    length_ok: bool
    start_distance_m: float
    size_m: float
    rotation_deg: float
    unsafe: str | None
    bad_turns: int
    steps_m: float
    origin: int  # 粗篩名次（0 = 粗篩成本最低）
    _: KW_ONLY
    direction: int  # score() 的方向項（v3 才有；v2 為 0）
    v2_overall: int  # 同一條路線的 score_v2（和 v2 時代的搜尋 bench 數字比）
    rank: float = 0.0  # 搜尋排名用的分數（score.rank：沒取整數的 overall 扣旋轉）


@dataclass(frozen=True)
class SearchCaseResult:
    name: str
    elapsed_s: float
    coarse_s: float  # 到精算開始（粗篩＋微調）
    coarse_candidates: int
    evaluated: int
    candidates: list[CandidateRow]
    spearman: float | None
    cold: bool
    previous_k: list[int] = field(default_factory=list)  # 只精算前 PREVIOUS_K 名時前 5 名的 overall
    evaluations: list[Evaluation] = field(default_factory=list)  # 每一次精算（粗篩名次與分數），給 --json 離線分析
    error: str | None = None
    notice: str | None = None
    problems: list[str] = field(default_factory=list)


def spearman(evaluations: list[Evaluation]) -> float | None:
    """粗篩排名（origin 越小越好）與精算 overall 的 Spearman 相關；只看第一次（未修正大小）而且吸附成功的。
    正值代表粗篩排名越前、精算分數越高。少於 3 個點時回傳 None。"""
    pairs = [(e.origin, e.overall) for e in evaluations if not e.rescaled and e.overall is not None]
    if len(pairs) < 3:
        return None
    ranks = np.array([-p[0] for p in pairs], dtype=float)  # 取負號：粗篩越前 = 越大
    scores = np.array([p[1] for p in pairs], dtype=float)
    def rank(values: np.ndarray) -> np.ndarray:
        order = values.argsort(kind="stable")
        out = np.empty(len(values))
        out[order] = np.arange(len(values))
        for v in np.unique(values):  # 同分取平均名次
            out[values == v] = out[values == v].mean()
        return out
    a, b = rank(ranks), rank(scores)
    if a.std() == 0 or b.std() == 0:
        return None
    return float(np.corrcoef(a, b)[0, 1])


def top_within(ranked: list[tuple[int, Candidate]], k: int) -> list[int]:
    """只看粗篩前 k 名（含它們的公里數修正）時的前 5 名 overall；ranked 已依搜尋的排序排好。"""
    return [c.result.score.overall for origin, c in ranked if origin < k][:TOP_N]


def problems_of(result: SearchCaseResult) -> list[str]:
    out = []
    if result.error:
        out.append(f"{result.name}：搜尋失敗（{result.error}）")
    if result.elapsed_s > TARGET_S:
        out.append(f"{result.name}：{result.elapsed_s:.1f} s，超過 {TARGET_S:.0f} s")
    if len(result.candidates) < TOP_N:
        out.append(f"{result.name}：只有 {len(result.candidates)} 個候選")
    for k, c in enumerate(result.candidates, 1):
        if c.unsafe:
            out.append(f"{result.name} 第 {k} 名：安全斷言沒過（{c.unsafe}）")
        if c.bad_turns:
            out.append(f"{result.name} 第 {k} 名：{c.bad_turns} 個接點的轉彎 Valhalla 不會直接騎")
        if c.steps_m > 0:
            out.append(f"{result.name} 第 {k} 名：走了樓梯 {c.steps_m:.0f} m")
    return out


def run_case(
    case: SearchCase,
    make_router: Callable[[], BenchRouter],
    roads: RoadIndex,
    denied_ids: frozenset[int],
    routers: int,
    cold: bool,
    svg_dir: Path | None = None,
    matching: dict | None = None,
) -> SearchCaseResult:
    """svg_dir 有值時前 TOP_N 名各寫一張 `<safe_name(案例)>_<名次>.svg`（疊圖＋縮圖），目錄不存在就建立。"""
    if svg_dir is not None:
        svg_dir.mkdir(parents=True, exist_ok=True)
    params = SearchParams(
        case.strokes, case.center, case.radius_m, case.min_km, case.max_km, case.rotation[0], case.rotation[1]
    )
    started = time.monotonic()
    first_match: list[float] = []

    def emit(event: object) -> None:
        if isinstance(event, Progress) and event.stage == "match" and not first_match:
            first_match.append(time.monotonic())

    try:
        outcome = run_search(
            params, [make_router() for _ in range(routers)], roads, Deadline(HARD_TIMEOUT_S), emit, match_options=matching
        )
    except Exception as error:  # noqa: BLE001  一個案例失敗不擋住其他案例
        elapsed = round(time.monotonic() - started, 2)
        result = SearchCaseResult(case.name, elapsed, elapsed, 0, 0, [], None, cold, error=type(error).__name__)
        return replace(result, problems=problems_of(result))
    elapsed = time.monotonic() - started
    # 安全斷言用另一個 Router，不計時；make_router 與搜尋同一組吸附參數（兩個參數只影響 trace 的 map_snap，
    # 安全斷言用的 route、edges（edge_walk）本來就不受影響）
    checker = make_router()
    rows = []
    for k, (origin, c) in enumerate(outcome.ranked[: len(outcome.candidates)], 1):
        unsafe = safety_problem(checker, c.result.segments, denied_ids)
        targets = stroke_targets(place(case.strokes, c.placement))
        row = CandidateRow(
            c.result.score.overall, c.result.score.coverage, c.result.score.cleanliness,
            round(c.result.route_length_m / 1000, 2), c.length_ok, round(c.start_distance_m),
            round(c.placement.size_m), round(c.placement.rotation_deg % 360, 1),
            None if unsafe is None else f"{unsafe[0]}：{unsafe[1]}",
            bad_turns(checker, c.result.segments),
            use_meters(checker, c.result.segments).get("steps", 0.0),
            origin,
            direction=c.result.score.direction,
            v2_overall=score_v2(targets, c.result.route_points).overall,
            rank=round(rank(c.result.score, c.placement.rotation_deg), 2),
        )
        rows.append(row)
        if svg_dir is not None and k <= TOP_N:
            svg = case_svg(f"{case.name} 第 {k} 名", targets, c.result.segments, extra_lines=[_score_line(row)])
            (svg_dir / f"{safe_name(case.name)}_{k}.svg").write_text(svg)
    coarse_s = (first_match[0] if first_match else time.monotonic()) - started
    result = SearchCaseResult(
        case.name, round(elapsed, 2), round(coarse_s, 2), outcome.coarse_candidates, outcome.evaluated, rows,
        spearman(outcome.evaluations), cold, top_within(outcome.ranked, PREVIOUS_K), list(outcome.evaluations),
        notice=outcome.notice,
    )
    return replace(result, problems=problems_of(result))


def _score_line(c: CandidateRow) -> str:
    """SVG 上的分數行。"""
    flags = "" if c.length_ok else "｜公里數不符"
    return (
        f"overall {c.overall}（cov {c.coverage}、clean {c.cleanliness}、dir {c.direction}）｜v2 {c.v2_overall}｜"
        f"{c.length_km:g} km｜{c.size_m:g} m、{c.rotation_deg:g}°｜粗篩第 {c.origin + 1} 名{flags}"
    )


def format_row(r: SearchCaseResult) -> str:
    top = " ".join(f"{c.overall}{'' if c.length_ok else '*'}" for c in r.candidates) or "-"
    km = " ".join(f"{c.length_km:g}" for c in r.candidates) or "-"
    rho = "-" if r.spearman is None else f"{r.spearman:+.2f}"
    late = sum(c.origin >= PREVIOUS_K for c in r.candidates)
    gain = "-"
    if r.candidates and r.previous_k:
        gain = f"{_mean([c.overall for c in r.candidates]) - _mean(r.previous_k):+.1f}"
    flags = " ".join(filter(None, ["cold" if r.cold else "", r.error, "FAIL" if r.problems else "ok"]))
    return (
        f"{r.name:34} {r.elapsed_s:6.1f} {r.coarse_s:6.1f} {r.coarse_candidates:9} {r.evaluated:4} "
        f"{len(r.candidates):3}  {top:24} {km:30} {rho:>6} {late:4} {gain:>6}  {flags}"
    )


def _mean(values: list[int]) -> float:
    return sum(values) / len(values)


HEADER = (
    f"{'name':34} {'total_s':>6} {'coarse':>6} {'coarse_n':>9} {'eval':>4} {'n':>3}  {'overall(*=公里數不符)':24} "
    f"{'km':30} {'rho':>6} {'late':>4} {'gain':>6}  note"
)


def summary(results: list[SearchCaseResult]) -> str:
    """精算全部候選的效益：late = 前 5 名裡粗篩名次 ≥ PREVIOUS_K 的個數；gain = 前 5 名平均 overall 比只精算前 PREVIOUS_K 名高多少。"""
    rows = [r for r in results if r.candidates and r.previous_k]
    late = sum(c.origin >= PREVIOUS_K for r in results for c in r.candidates)
    total = sum(len(r.candidates) for r in results)
    gains = sorted(_mean([c.overall for c in r.candidates]) - _mean(r.previous_k) for r in rows)
    first = sum(r.candidates[0].overall > r.previous_k[0] for r in rows)
    short = sum(len(r.previous_k) < len(r.candidates) for r in results)
    median = f"{gains[len(gains) // 2]:+.1f}" if gains else "-"
    return (
        f"前 5 名來自粗篩第 {PREVIOUS_K + 1} 名以後：{late}/{total}；前 5 名平均 overall 比只精算前 {PREVIOUS_K} 名"
        f"高（中位數）{median}；第 1 名分數變高 {first}/{len(rows)} 案例；只精算前 {PREVIOUS_K} 名會少於 5 個候選 {short} 案例"
    )


def score_summary(results: list[SearchCaseResult]) -> str:
    """所有案例前 5 名的平均 overall：主分數（score()，score_version 版）與同一批路線的 v2。"""
    rows = [c for r in results for c in r.candidates]
    mean = f"{_mean([c.overall for c in rows]):.1f}" if rows else "-"
    mean_v2 = f"{_mean([c.v2_overall for c in rows]):.1f}" if rows else "-"
    return f"前 {TOP_N} 名平均 overall {mean}（score_version {SCORE_VERSION}）、v2 {mean_v2}（{len(rows)} 個候選）"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="自動搜尋基準測試（完成條件：R = 5 km 時 30 s 內前 5 名）")
    parser.add_argument("--data-dir", type=Path, default=Path(os.environ.get("DATA_DIR", "/data")))
    parser.add_argument("--routers", type=int, default=int(os.environ.get("SEARCH_ROUTERS", "2")))
    parser.add_argument("--case", action="append", default=[], help="只跑名稱含這個字串的案例（可重複）")
    parser.add_argument("--json", type=Path, help="把逐案例結果寫成 JSON（給 findings 文件用）")
    parser.add_argument("--svg-dir", type=Path, help=f"每個案例前 {TOP_N} 名各寫一張 SVG（疊圖＋縮圖）到這個目錄，不存在就建立")
    add_router_options(parser)
    add_match_options(parser)
    args = parser.parse_args(argv)
    options = router_options(args)
    build = DataLayout(args.data_dir).current()
    if build is None:
        sys.exit("沒有可用的路網資料")
    try:
        roads = RoadIndex.load(build)
    except RoadDataMissing as error:
        sys.exit(f"目前的 build 沒有可騎道路索引（要重建路網）：{error}")
    try:
        denied_ids = frozenset(d.id for d in load_denied(build))
    except (OSError, ValueError, KeyError, TypeError) as error:
        sys.exit(f"讀不到 {build / DENIED_FILE}，無法檢查安全：{error}")
    cases = [c for c in SEARCH_CASES if not args.case or any(s in c.name for s in args.case)]
    matching = match_options(args)
    print(describe_router_options(options), flush=True)
    print(describe_match_options(matching), flush=True)
    print(f"build {build.name}、{len(roads)} 條可騎路段、Router × {args.routers}；完成條件 ≤ {TARGET_S:.0f} s 且 {TOP_N} 個候選", flush=True)
    print(HEADER, flush=True)
    results, seen = [], set()
    for case in cases:
        cold = case.center not in seen
        seen.add(case.center)
        result = run_case(
            case, lambda: Router.from_build(build, **options), roads, denied_ids, args.routers, cold, args.svg_dir,
            matching,
        )
        results.append(result)
        print(format_row(result), flush=True)
    times = sorted(r.elapsed_s for r in results)
    print(
        f"共 {len(results)} 案例；時間中位數 {times[len(times) // 2]:.1f} s、最慢 {times[-1]:.1f} s；"
        f"不合格 {sum(bool(r.problems) for r in results)} 個",
        flush=True,
    )
    print(summary(results), flush=True)
    print(score_summary(results), flush=True)
    if args.json:
        args.json.write_text(json.dumps([asdict(r) for r in results], ensure_ascii=False, indent=2) + "\n")
    problems = [p for r in results for p in r.problems]
    for p in problems:
        print(f"不合格：{p}")
    if problems:
        sys.exit(1)


if __name__ == "__main__":
    main()
