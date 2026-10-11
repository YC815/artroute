import json
import math
import xml.etree.ElementTree as ET
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from engine.bench import __main__ as bench_cli
from engine.bench import runner
from engine.bench.svg import KIND_COLORS
from engine.bench.cases import CASES, LOCATIONS, SHAPES, Case, Shape, place
from engine.bench.runner import (
    PRODUCTION_TIMEOUT_MS,
    REPORTED_USES,
    SAFETY_ERRORS,
    UNDETERMINED,
    UNSAFE,
    CaseResult,
    bad_turns,
    case_set_changes,
    compare,
    format_header,
    format_row,
    load_baseline,
    run_cases,
    safety_problem,
    summarize,
    use_meters,
)
from engine.data.build import DENIED_FILE, build_with_patch, load_denied
from engine.data.layout import DataLayout
from engine.matching.deadline import MatchTimeout
from engine.matching.score import SCORE_VERSION, Score, score, score_v1, score_v2
from engine.matching.deadline import Deadline
from engine.matching.service import AllStrokesFailed, MatchResult, StrokeInput, match_shape
from engine.matching.types import JOIN_TOLERANCE_M, Segment
from engine.routing.geo import haversine_m, polyline_length_m
from engine.routing.router import FAST_TURN_PENALTY_FACTOR, EdgeInfo, Router, RoutingError
from engine.routing.tiles import build_tiles
from grid_helpers import densify
from osm_fixture import EXPRESSWAY_MAP, EXPRESSWAY_WAY_ID, X0, X3, grid_point, write_grid_pbf
from turn_router import TurnBlockRouter, east, north

SVG_NS = "{http://www.w3.org/2000/svg}"
UNIT_SQUARE = Shape("square", [([(0, 0), (1, 0), (1, 1), (0, 1)], True)])
LINE = Shape("line", [([(0, 0.5), (1, 0.5)], False)])


def test_place_scales_and_centres() -> None:
    (stroke,) = place(UNIT_SQUARE, (121.5, 25.05), 1000.0, 0.0)
    assert stroke.closed
    assert haversine_m(stroke.points[0], stroke.points[1]) == pytest.approx(1000, rel=0.01)
    lon = sum(p[0] for p in stroke.points) / 4
    lat = sum(p[1] for p in stroke.points) / 4
    assert (lon, lat) == pytest.approx((121.5, 25.05), abs=1e-6)


def test_place_rotation_keeps_size() -> None:
    (stroke,) = place(UNIT_SQUARE, (121.5, 25.05), 1000.0, 45.0)
    assert haversine_m(stroke.points[0], stroke.points[1]) == pytest.approx(1000, rel=0.01)


@pytest.mark.parametrize(
    ("rotation_deg", "east_m", "north_m"),
    [(0.0, 0.0, 500.0), (90.0, 500.0, 0.0), (180.0, 0.0, -500.0), (270.0, -500.0, 0.0)],
)
def test_place_rotates_clockwise_from_north(rotation_deg: float, east_m: float, north_m: float) -> None:
    """與 web/src/lib/shape/transform.ts 一致：順時針，0 度時圖形上方朝北。"""
    center = (121.5, 25.05)
    top_centre = Shape("top", [([(0.5, 0.5), (0.5, 1.0)], False)])  # 第 2 點 = 圖形上緣中點，離中心 size/2
    (stroke,) = place(top_centre, center, 1000.0, rotation_deg)
    lon, lat = stroke.points[1]
    east = math.copysign(haversine_m(center, (lon, center[1])), lon - center[0])
    north = math.copysign(haversine_m(center, (center[0], lat)), lat - center[1])
    assert (east, north) == pytest.approx((east_m, north_m), abs=3)


def test_cases_are_unique_and_inside_taiwan() -> None:
    names = [c.name for c in CASES]
    assert len(names) == len(set(names)) >= 12
    for case in CASES:
        lon, lat = case.center
        assert 118.1 <= lon <= 122.1 and 21.8 <= lat <= 26.4


def test_hi_is_a_multi_stroke_shape() -> None:
    hi = next(s for s in SHAPES if s.name == "hi")
    assert [closed for _, closed in hi.strokes] == [False] * 4  # H 三筆 + I 一筆


def test_cases_are_every_shape_at_every_location_plus_rotated_variants_at_daan() -> None:
    plain = {c.name for c in CASES if c.rotation_deg == 0}
    assert plain == {f"{s.name}@{loc}" for s in SHAPES for loc in LOCATIONS}
    rotated = [c for c in CASES if c.rotation_deg != 0]
    assert sorted(c.name for c in rotated) == sorted(f"{s.name}@taipei_daan@r30" for s in SHAPES)
    assert all(c.center == LOCATIONS["taipei_daan"] and c.rotation_deg == 30 for c in rotated)


def scored(
    name: str,
    coverage: int,
    cleanliness: int,
    overall: int,
    *,
    legacy: Score | None = None,
    v2: Score | None = None,
    direction: int = 0,
    error: str | None = None,
) -> CaseResult:
    """主分數（score()）＋v2、v1（legacy）分數；沒給 v2、legacy 就和主分數相同。"""
    legacy = legacy or Score(coverage, cleanliness, overall)
    v2 = v2 or Score(coverage, cleanliness, overall)
    return CaseResult(
        name, coverage, cleanliness, overall, 1000.0, 1000.0, 10, error,
        direction=direction,
        v2_coverage=v2.coverage, v2_cleanliness=v2.cleanliness, v2_overall=v2.overall,
        legacy_coverage=legacy.coverage, legacy_cleanliness=legacy.cleanliness, legacy_overall=legacy.overall,
    )


def result(name: str, overall: int, error: str | None = None) -> CaseResult:
    return scored(name, overall, overall, overall, error=error)


def test_compare_flags_regression_and_new_failure_only() -> None:
    baseline = {"a": {"overall": 80, "error": None}, "b": {"overall": 70, "error": None}, "c": {"overall": 60, "error": None}}
    results = [result("a", 78), result("b", 66), result("c", 0, error="no_route"), result("new", 10)]
    problems = compare(results, baseline, tolerance=3)
    assert len(problems) == 2
    assert any("b" in p for p in problems) and any("c" in p for p in problems)


@pytest.mark.valhalla
def test_run_cases_on_grid(grid_config: Path) -> None:
    lon, lat = grid_point(3, 1)
    case = Case("grid-line", Shape("line", [([(0, 0.5), (1, 0.5)], False)]), (lon, lat), 1000.0, 0.0)
    (res,) = run_cases(Router(grid_config), [case], denied_ids=frozenset())
    assert res.error is None and res.safety is None
    assert res.overall >= 80
    assert res.length_m == pytest.approx(1000, rel=0.15)


def test_compare_reports_malformed_baseline_entry_readably() -> None:
    for entry in ({"error": None}, {"overall": "80"}, None, "oops"):
        problems = compare([result("a", 80)], {"a": entry})
        assert len(problems) == 1
        assert "a" in problems[0] and "overall" in problems[0]


def test_case_set_changes_lists_orphans_and_new_cases() -> None:
    notes = case_set_changes([result("a", 1), result("fresh", 1)], {"a": {}, "gone": {}})
    assert len(notes) == 2
    assert any("gone" in n and "沒跑" in n for n in notes)
    assert any("fresh" in n and "新增" in n for n in notes)
    assert case_set_changes([result("a", 1)], {"a": {}}) == []


def test_load_baseline_rejects_garbage_with_readable_errors(tmp_path: Path) -> None:
    path = tmp_path / "bl.json"
    path.write_text("not json")
    with pytest.raises(ValueError, match="bl.json"):
        load_baseline(path)
    path.write_text("[1]")
    with pytest.raises(ValueError, match="JSON 物件"):
        load_baseline(path)
    path.write_text('{"a": {"overall": 1}}')
    assert load_baseline(path) == {"a": {"overall": 1}}


A, B, C = (121.50, 25.05), (121.51, 25.05), (121.52, 25.05)


def seg(points, kind="stroke") -> Segment:
    return Segment(kind, 0, points, polyline_length_m(points))


class EdgesRouter:
    """edges() 回傳固定的 way；error 不是 None 時丟 RoutingError（edge_walk 配不出路段）。"""

    def __init__(self, ways: tuple[int, ...] = (1, 2), error: str | None = None) -> None:
        self.ways, self.error = ways, error
        self.calls = 0

    def edges(self, points):
        self.calls += 1
        if self.error is not None:
            raise RoutingError(self.error)
        return [EdgeInfo(way, 0.0) for way in self.ways]


def fake_match_result(segments: list[Segment] | None = None) -> MatchResult:
    segments = [seg([A, B])] if segments is None else segments
    return MatchResult([], 900.0, segments, Score(80, 70, 75), 1000.0, [])


def test_run_cases_warms_up_each_location_once_then_streams_each_result(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []

    def fake_match(router, strokes, *, search_radius_m, deadline):
        events.append("match")
        return fake_match_result()

    monkeypatch.setattr(runner, "match_shape", fake_match)
    here, there = (121.50, 25.05), (121.60, 25.10)
    cases = [Case("a1", LINE, here, 1000.0, 0.0), Case("a2", LINE, here, 1000.0, 30.0), Case("b1", LINE, there, 1000.0, 0.0)]

    results = run_cases(EdgesRouter(), cases, denied_ids=frozenset(), on_result=lambda r: events.append(f"result:{r.name}"))
    assert [r.name for r in results] == ["a1", "a2", "b1"]
    assert events == ["match", "match", "match", "result:a1", "match", "result:a2", "match", "result:b1"]

    events.clear()
    run_cases(EdgesRouter(), cases, denied_ids=frozenset(), warm_up=False, on_result=lambda r: events.append(f"result:{r.name}"))
    assert events == ["match", "result:a1", "match", "result:a2", "match", "result:b1"]


@pytest.mark.parametrize("error", [AllStrokesFailed([]), MatchTimeout()])
def test_failed_case_keeps_the_real_target_length(monkeypatch: pytest.MonkeyPatch, error: Exception) -> None:
    def boom(*args, **kwargs):
        raise error

    monkeypatch.setattr(runner, "match_shape", boom)
    case = Case("sq", UNIT_SQUARE, (121.5, 25.05), 1000.0, 0.0)
    (res,) = run_cases(EdgesRouter(), [case], denied_ids=frozenset())  # 預熱階段的失敗也不能讓整個跑分中斷
    assert res.error == type(error).__name__
    assert (res.coverage, res.cleanliness, res.overall, res.length_m) == (0, 0, 0, 0.0)
    assert (res.legacy_coverage, res.legacy_cleanliness, res.legacy_overall) == (0, 0, 0)
    assert (res.direction, res.v2_coverage, res.v2_cleanliness, res.v2_overall) == (0, 0, 0, 0)
    assert res.target_length_m == pytest.approx(4000, rel=0.01)  # 封閉方框：四邊都算


def test_format_row_marks_only_cases_slower_than_the_production_timeout() -> None:
    at_limit = replace(result("a", 80), ms=PRODUCTION_TIMEOUT_MS)
    over = replace(result("b", 80), ms=PRODUCTION_TIMEOUT_MS + 1)
    assert "SLOW" not in format_row(at_limit)
    assert "SLOW" in format_row(over)


def test_summarize_counts_failed_and_slow_cases() -> None:
    results = [result("a", 80), replace(result("b", 60), ms=6000), result("c", 0, error="AllStrokesFailed")]
    text = summarize(results)
    assert "共 3 案例" in text and "成功 2" in text and "失敗 1" in text and "SLOW 1" in text
    assert "平均 overall 70.0" in text


class FakeRun:
    """CLI 的假環境：路網與跑分換成假的，只測 CLI 自己的流程（參數、輸出、baseline 讀寫、結束碼）。"""

    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.data = tmp_path / "data"
        layout = DataLayout(self.data)
        layout.activate(layout.new_build_dir("b1"))
        self.baseline = tmp_path / "baseline.json"
        self.results: list[CaseResult] = []
        self.crash = False
        self.router_kwargs: list[dict] = []
        self.svg_dirs: list[Path | None] = []
        self.match_options: list[dict | None] = []
        monkeypatch.setattr(bench_cli, "Router", SimpleNamespace(from_build=self._from_build))
        monkeypatch.setattr(bench_cli, "run_cases", self._run_cases)
        (layout.current() / DENIED_FILE).write_text("[]")

    def _from_build(self, build, **kwargs):
        self.router_kwargs.append(kwargs)
        return object()

    def _run_cases(
        self, router, cases, timeout_s=30.0, *, denied_ids, warm_up=True, on_result=None, svg_dir=None, match_options=None
    ):
        self.svg_dirs.append(svg_dir)
        self.match_options.append(match_options)
        for r in self.results:
            on_result(r)
        if self.crash:
            raise RuntimeError("boom")
        return self.results

    def __call__(self, results: list[CaseResult], *flags: str) -> None:
        self.results = results
        bench_cli.main(["--data-dir", str(self.data), "--baseline", str(self.baseline), *flags])


@pytest.fixture
def fake_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeRun:
    return FakeRun(tmp_path, monkeypatch)


def test_cli_update_refuses_when_a_case_failed(fake_run: FakeRun, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as refused:
        fake_run([result("a", 80), result("b", 0, error="AllStrokesFailed")], "--update-baseline")
    assert refused.value.code == 1
    assert not fake_run.baseline.exists()
    err = capsys.readouterr().err
    assert "b" in err and "AllStrokesFailed" in err and "--force" in err


def test_cli_update_with_force_writes_failed_cases_too(fake_run: FakeRun) -> None:
    fake_run([result("a", 80), result("b", 0, error="AllStrokesFailed")], "--update-baseline", "--force")
    saved = json.loads(fake_run.baseline.read_text())
    assert set(saved) == {"a", "b"}
    assert saved["a"]["error"] is None and saved["b"]["error"] == "AllStrokesFailed"


def test_cli_update_needs_no_force_when_everything_succeeded(fake_run: FakeRun) -> None:
    fake_run([result("a", 80)], "--update-baseline")
    assert json.loads(fake_run.baseline.read_text())["a"]["overall"] == 80


def test_cli_force_without_update_is_a_usage_error(fake_run: FakeRun) -> None:
    with pytest.raises(SystemExit) as usage:
        fake_run([result("a", 80)], "--force")
    assert usage.value.code == 2


def test_cli_prints_header_then_rows_then_summary(fake_run: FakeRun, capsys: pytest.CaptureFixture[str]) -> None:
    fake_run([result("a", 80), replace(result("b", 70), ms=6000)], "--update-baseline")
    lines = capsys.readouterr().out.splitlines()
    header = next(i for i, line in enumerate(lines) if line.startswith("name"))
    row_a = next(i for i, line in enumerate(lines) if line.startswith("a "))
    row_b = next(i for i, line in enumerate(lines) if line.startswith("b "))
    summary = next(i for i, line in enumerate(lines) if "共 2 案例" in line)
    assert header < row_a < row_b < summary
    assert "SLOW" in lines[row_b] and "SLOW" not in lines[row_a]


def test_cli_rows_are_already_printed_when_the_run_crashes(fake_run: FakeRun, capsys: pytest.CaptureFixture[str]) -> None:
    fake_run.crash = True
    with pytest.raises(RuntimeError):
        fake_run([result("a", 80)])
    assert "a " in capsys.readouterr().out


def test_cli_prints_regressions_and_case_set_notes(fake_run: FakeRun, capsys: pytest.CaptureFixture[str]) -> None:
    fake_run.baseline.write_text(json.dumps({"a": {"overall": 90, "error": None}, "gone": {"overall": 50, "error": None}}))
    with pytest.raises(SystemExit) as regressed:
        fake_run([result("a", 80), result("fresh", 50)])
    assert regressed.value.code == 1
    out = capsys.readouterr().out
    assert "退步：a：overall（v1 評分）90 → 80" in out  # 沒有 score_version 的 baseline 是 v1：比本次的 v1 分數
    assert "gone" in out and "fresh" in out


def test_cli_case_set_notes_alone_do_not_fail_the_run(fake_run: FakeRun, capsys: pytest.CaptureFixture[str]) -> None:
    fake_run.baseline.write_text(json.dumps({"a": {"overall": 80, "error": None}, "gone": {"overall": 50, "error": None}}))
    fake_run([result("a", 80), result("fresh", 50)])  # 不 raise = exit 0
    out = capsys.readouterr().out
    assert "gone" in out and "fresh" in out and "退步" not in out


def test_cli_exits_with_readable_message_on_bad_or_malformed_baseline(fake_run: FakeRun, capsys: pytest.CaptureFixture[str]) -> None:
    fake_run.baseline.write_text("not json")
    with pytest.raises(SystemExit) as bad:
        fake_run([result("a", 80)])
    assert "baseline" in str(bad.value.code)

    fake_run.baseline.write_text(json.dumps({"a": {"error": None}}))
    with pytest.raises(SystemExit) as malformed:
        fake_run([result("a", 80)])
    assert malformed.value.code == 1
    assert "a" in capsys.readouterr().out


def test_cli_without_any_build_exits_with_message(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as nothing:
        bench_cli.main(["--data-dir", str(tmp_path / "empty")])
    assert "路網" in str(nothing.value.code)


@pytest.mark.valhalla
def test_cli_on_grid_layout(grid_config: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    data = tmp_path / "data"
    layout = DataLayout(data)
    build = layout.new_build_dir("grid")
    for name in (grid_config.name, "tiles"):  # 共用整個測試的 tiles，另外放一份空的禁行清單
        (build / name).symlink_to(grid_config.parent / name)
    (build / DENIED_FILE).write_text("[]")
    layout.activate(build)
    lon, lat = grid_point(3, 1)
    ok = Case("grid-line", LINE, (lon, lat), 1000.0, 0.0)
    far = Case("far", LINE, LOCATIONS["taipei_daan"], 1000.0, 0.0)  # 不在網格上，必定失敗
    monkeypatch.setattr(bench_cli, "CASES", [ok, far])
    baseline = tmp_path / "baseline.json"
    argv = ["--data-dir", str(data), "--baseline", str(baseline)]

    with pytest.raises(SystemExit) as refused:
        bench_cli.main([*argv, "--update-baseline"])
    assert refused.value.code == 1 and not baseline.exists()

    bench_cli.main([*argv, "--update-baseline", "--force"])
    saved = json.loads(baseline.read_text())
    assert saved["grid-line"]["error"] is None and saved["far"]["error"] == "AllStrokesFailed"

    bench_cli.main(argv)  # 與剛存的 baseline 比：沒有退步

    capsys.readouterr()
    svg_dir = tmp_path / "svg" / "new"  # 不存在的目錄要自動建立
    bench_cli.main([*argv, "--svg-dir", str(svg_dir), "--turn-penalty-factor", "none", "--sigma-z", "5"])
    assert sorted(p.name for p in svg_dir.iterdir()) == ["far.svg", "grid-line.svg"]
    drawn = ET.fromstring((svg_dir / "grid-line.svg").read_text())
    assert any(line.get("stroke") == KIND_COLORS["stroke"] for line in drawn.iter(f"{SVG_NS}polyline"))
    assert capsys.readouterr().out.splitlines()[0].startswith("吸附參數：turn_penalty_factor none")

    saved["far"].update(overall=80, error=None)  # 手改成「far 原本成功」
    baseline.write_text(json.dumps(saved))
    with pytest.raises(SystemExit) as regressed:
        bench_cli.main(argv)
    assert regressed.value.code == 1
    assert "退步：far：原本成功，現在失敗（AllStrokesFailed）" in capsys.readouterr().out


# ---- 最終審查 M2：每個案例都檢查安全不變式（相鄰路段 ≤ 1 m、不經過被禁行的 way） ----


def test_safety_passes_a_continuous_route_off_denied_ways() -> None:
    assert safety_problem(EdgesRouter(ways=(1, 2)), [seg([A, B]), seg([B, C], "detour")], frozenset({9})) is None


def test_safety_flags_a_gap_between_segments() -> None:
    off = (B[0] + 0.0001, B[1])  # 約 10 m
    problem = safety_problem(EdgesRouter(), [seg([A, B]), seg([off, C])], frozenset())
    assert problem is not None and problem[0] == UNSAFE and "缺口" in problem[1]


def test_safety_flags_a_denied_way() -> None:
    problem = safety_problem(EdgesRouter(ways=(1, 9)), [seg([A, B])], frozenset({9}))
    assert problem is not None and problem[0] == UNSAFE and "9" in problem[1]


def test_safety_cannot_pass_when_edges_cannot_be_matched() -> None:
    problem = safety_problem(EdgesRouter(error="edge_walk failed"), [seg([A, B])], frozenset({9}))
    assert problem is not None and problem[0] == UNDETERMINED


def test_safety_cannot_pass_a_segment_that_matches_no_edges() -> None:
    problem = safety_problem(EdgesRouter(ways=()), [seg([A, B])], frozenset({9}))
    assert problem is not None and problem[0] == UNDETERMINED


def test_safety_cannot_pass_an_empty_route() -> None:
    problem = safety_problem(EdgesRouter(), [], frozenset())
    assert problem is not None and problem[0] == UNDETERMINED


def test_safety_skips_zero_length_fragments() -> None:
    # 封閉圈旋轉時切出的 [p, p] 片段沒有經過任何路段，不是無法判斷
    router = EdgesRouter(ways=(1,))
    assert safety_problem(router, [seg([A, B]), seg([B, B]), seg([B, C])], frozenset({9})) is None
    assert router.calls == 2


def test_unsafe_case_is_a_failed_case_and_marked(monkeypatch: pytest.MonkeyPatch) -> None:
    gap = [seg([A, B]), seg([(B[0] + 0.0001, B[1]), C])]
    monkeypatch.setattr(runner, "match_shape", lambda *args, **kwargs: fake_match_result(gap))
    (res,) = run_cases(EdgesRouter(), [Case("sq", UNIT_SQUARE, (121.5, 25.05), 1000.0, 0.0)], denied_ids=frozenset())
    assert res.error == UNSAFE and "缺口" in res.safety
    assert res.overall == 75  # 分數照記，但案例算失敗
    assert UNSAFE in format_row(res) and "缺口" in format_row(res)
    assert "失敗 1" in summarize([res]) and "不安全 1" in summarize([res])
    assert compare([res], {"sq": {"overall": 75, "error": None}})[0].startswith("sq：安全斷言沒過（UnsafeRoute：")


def test_undetermined_case_is_counted_separately(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runner, "match_shape", lambda *args, **kwargs: fake_match_result())
    (res,) = run_cases(EdgesRouter(error="boom"), [Case("sq", UNIT_SQUARE, (121.5, 25.05), 1000.0, 0.0)], denied_ids=frozenset())
    assert res.error == UNDETERMINED
    assert "無法判斷 1" in summarize([res])


def test_cli_refuses_to_run_without_the_denied_list(fake_run: FakeRun) -> None:
    (DataLayout(fake_run.data).current() / DENIED_FILE).unlink()
    with pytest.raises(SystemExit) as refused:
        fake_run([result("a", 80)])
    assert DENIED_FILE in str(refused.value.code)


@pytest.mark.valhalla
def test_grid_safety_detects_a_way_the_route_really_uses(grid_config: Path) -> None:
    """非空洞：把路線真的經過的 way 當成被禁行，真實 Valhalla 的 edges() 必須抓得到。"""
    router = Router(grid_config)
    lon, lat = grid_point(3, 1)
    case = Case("grid-line", LINE, (lon, lat), 1000.0, 0.0)
    strokes = place(case.shape, case.center, case.size_m, case.rotation_deg)
    result = match_shape(router, strokes, search_radius_m=None, deadline=Deadline(30))
    used = {e.way_id for s in result.segments for e in router.edges(s.points)}
    assert used
    (res,) = run_cases(router, [case], denied_ids=frozenset(used))
    assert res.error == UNSAFE and str(min(used)) in res.safety


@pytest.mark.valhalla
def test_stroke_along_a_marked_expressway_becomes_a_legal_detour(tmp_path: Path) -> None:
    """服務層真實 Valhalla 回歸：沿被標記的快速道路畫一筆 → 合法 detour、接點連續、不經過被禁行的 way。

    對照組（未修補的 tiles）同一筆會沿快速道路吸附，同一個安全檢查必須抓到：證明檢查不是空洞通過。
    """
    src = tmp_path / "src.osm.pbf"
    write_grid_pbf(src, EXPRESSWAY_MAP)
    build = tmp_path / "build"
    build.mkdir()
    router = Router(build_with_patch(src, build))
    denied = frozenset(d.id for d in load_denied(build))
    assert EXPRESSWAY_WAY_ID in denied
    along = [StrokeInput(densify(grid_point(X0, 1), grid_point(X0 + 4, 1), n=40), False)]

    for radius in (None, 150):
        result = match_shape(router, along, search_radius_m=radius, deadline=Deadline(30))
        assert "detour" in {s.kind for s in result.segments}
        for a, b in zip(result.segments, result.segments[1:]):
            assert haversine_m(a.points[-1], b.points[0]) <= JOIN_TOLERANCE_M
        assert safety_problem(router, result.segments, denied) is None

    control = Router(build_tiles(src, tmp_path / "control"))
    unsafe = match_shape(control, along, search_radius_m=None, deadline=Deadline(30))
    problem = safety_problem(control, unsafe.segments, denied)
    assert problem is not None and problem[0] == UNSAFE and str(EXPRESSWAY_WAY_ID) in problem[1]


@pytest.mark.parametrize(("version", "label"), [({}, "（v1 評分）"), ({"score_version": SCORE_VERSION}, " ")])
def test_compare_flags_coverage_drop_even_when_overall_holds(version: dict, label: str) -> None:
    # ledger L120：overall 可能因 cleanliness 上升而持平，覆蓋率卻掉到 0（例如部件被整段丟掉）
    base = {"a": {"overall": 50, "coverage": 66, "error": None, **version}, "old": {"overall": 50, "error": None, **version}}
    results = [scored("a", 0, 100, 50), scored("old", 0, 100, 50)]
    assert compare(results, base) == [f"a：coverage{label}66 → 0"]  # 沒有 coverage 的舊條目只比 overall


class UturnRouter:
    """edge_walk 配不上原地折返（A→B→A）：整段含折返就失敗，切開後各自配得上。
    way id 依位置給：經度 ≥ split 的點落在 way 9，其餘在 way 1。"""

    def __init__(self, split: float = 999.0) -> None:
        self.split = split

    def edges(self, points):
        if any(points[i - 1] == points[i + 1] for i in range(1, len(points) - 1)):
            raise RoutingError("edge_walk algorithm failed to find exact route match")
        return [EdgeInfo(9 if lon >= self.split else 1, 0.0) for lon, _ in points[1:]]


def test_safety_splits_a_segment_edge_walk_cannot_match_whole() -> None:
    # 真實資料：吸附結果在死巷原地折返時 edge_walk 整段配不上；從中間切開各自配，經過的路段一條都不漏
    spur = seg([A, B, (B[0], B[1] + 0.0003), B, C])
    assert safety_problem(UturnRouter(), [spur], frozenset({9})) is None
    flagged = safety_problem(UturnRouter(split=C[0]), [spur], frozenset({9}))
    assert flagged is not None and flagged[0] == UNSAFE and "9" in flagged[1]


# ---- P1.5 Task 1：安全斷言沒過一律算退步，--force 也不能寫進 baseline ----


def unsafe(name: str, error: str = UNSAFE) -> CaseResult:
    return replace(result(name, 80), error=error, safety="第 0、1 段之間缺口 12.0 m")


def test_safety_errors_are_exactly_the_two_assertion_outcomes() -> None:
    assert SAFETY_ERRORS == frozenset({UNSAFE, UNDETERMINED})


def test_compare_flags_a_safety_failure_even_when_the_baseline_entry_failed_the_same_way() -> None:
    baseline = {"a": {"overall": 80, "coverage": 80, "error": UNSAFE}}
    assert compare([unsafe("a")], baseline) == ["a：安全斷言沒過（UnsafeRoute：第 0、1 段之間缺口 12.0 m）"]


def test_compare_flags_a_safety_failure_of_a_case_missing_from_the_baseline() -> None:
    assert compare([unsafe("fresh", UNDETERMINED)], {}) == [
        "fresh：安全斷言沒過（SafetyUndetermined：第 0、1 段之間缺口 12.0 m）"
    ]


def test_compare_still_ignores_an_ordinary_failure_the_baseline_already_had() -> None:
    baseline = {"b": {"overall": 0, "coverage": 0, "error": "AllStrokesFailed"}}
    assert compare([result("b", 0, error="AllStrokesFailed")], baseline) == []


def test_cli_check_fails_on_a_safety_failure_whatever_the_baseline_says(
    fake_run: FakeRun, capsys: pytest.CaptureFixture[str]
) -> None:
    fake_run.baseline.write_text(json.dumps({"a": {"overall": 80, "coverage": 80, "error": UNSAFE}}))
    with pytest.raises(SystemExit) as failed:
        fake_run([unsafe("a")])
    assert failed.value.code == 1
    assert "退步：a：安全斷言沒過" in capsys.readouterr().out


@pytest.mark.parametrize("error", [UNSAFE, UNDETERMINED])
def test_cli_update_with_force_refuses_safety_failures(
    fake_run: FakeRun, capsys: pytest.CaptureFixture[str], error: str
) -> None:
    with pytest.raises(SystemExit) as refused:
        fake_run([result("a", 80), unsafe("b", error)], "--update-baseline", "--force")
    assert refused.value.code == 1
    assert not fake_run.baseline.exists()
    err = capsys.readouterr().err
    assert "b" in err and error in err and "--force" in err


# ---- P1.5 Task 2：量 footway／steps／parking_aisle（只量測） ----

ZERO_USES = {"footway": 0.0, "steps": 0.0, "parking_aisle": 0.0}


class UseRouter:
    """edges()：每段固定配到 footway 12 m、一般道路 500 m、steps 4 m。"""

    def edges(self, points):
        return [EdgeInfo(1, 0.0, "footway", 0.012), EdgeInfo(2, 0.0, "road", 0.5), EdgeInfo(3, 0.0, "steps", 0.004)]


def test_reported_uses() -> None:
    assert REPORTED_USES == ("footway", "steps", "parking_aisle")


def test_use_meters_sums_only_the_reported_uses_over_every_segment() -> None:
    assert use_meters(UseRouter(), [seg([A, B]), seg([B, C], "detour")]) == {
        "footway": 24.0, "steps": 8.0, "parking_aisle": 0.0,
    }


def test_use_meters_skips_zero_length_fragments_and_unmatchable_segments() -> None:
    assert use_meters(UseRouter(), [seg([A, A])]) == ZERO_USES
    assert all(isinstance(m, float) for m in use_meters(UseRouter(), [seg([A, A])]).values())  # 沒有路段也是 0.0，不是 int 0
    assert use_meters(EdgesRouter(error="boom"), [seg([A, B])]) == ZERO_USES


def test_case_result_row_and_summary_carry_use_meters(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runner, "match_shape", lambda *args, **kwargs: fake_match_result())
    case = Case("sq", UNIT_SQUARE, (121.5, 25.05), 1000.0, 0.0)
    (res,) = run_cases(UseRouter(), [case], denied_ids=frozenset(), warm_up=False)
    assert res.error is None
    assert res.use_m == {"footway": 12.0, "steps": 4.0, "parking_aisle": 0.0}
    assert format_header().split()[10:13] == ["foot_m", "steps_m", "park_m"]
    assert format_row(res).split()[10:13] == ["12", "4", "0"]
    assert "footway／steps／parking_aisle 合計 12／4／0 m" in summarize([res])


def test_summarize_use_totals_exclude_failed_and_unsafe_cases() -> None:
    uses = {"footway": 10.0, "steps": 2.0, "parking_aisle": 1.0}
    ok = replace(result("ok", 80), use_m=uses)
    failed = replace(result("failed", 0, error="AllStrokesFailed"), use_m={"footway": 500.0, "steps": 500.0, "parking_aisle": 500.0})
    unsafe = replace(result("unsafe", 80, error=UNSAFE), use_m={"footway": 700.0, "steps": 700.0, "parking_aisle": 700.0})
    assert "footway／steps／parking_aisle 合計 10／2／1 m" in summarize([ok, failed, unsafe])


def test_failed_case_has_no_use_meters_and_still_formats() -> None:
    failed = result("x", 0, error="AllStrokesFailed")
    assert failed.use_m == {}
    assert format_row(failed).split()[10:13] == ["0", "0", "0"]


@pytest.mark.valhalla
def test_edges_report_use_and_length_on_real_tiles(grid_config: Path) -> None:
    router = Router(grid_config)
    route = router.route(grid_point(0, 1), grid_point(2, 1))
    edges = router.edges(route.points)
    assert edges and {e.use for e in edges} == {"road"}  # 合成地圖全是 residential
    assert sum(e.length_km for e in edges) == pytest.approx(route.length_km, rel=0.05)


# ---- P1.5 Task 11：接點轉彎安全計數 ----

CORNER = (121.505, 25.05)
INTO = seg([east(CORNER, -100), CORNER])
UP = seg([CORNER, north(CORNER, 100)])
ON = seg([CORNER, east(CORNER, 100)])


class TurnEdgesRouter(TurnBlockRouter):
    """TurnBlockRouter 加上 edges()（安全斷言要用）：一律配到 way 1。"""

    def edges(self, points):
        return [EdgeInfo(1, 0.0)]


def test_bad_turns_counts_joins_valhalla_would_not_ride() -> None:
    router = TurnEdgesRouter(blocked=CORNER)
    assert bad_turns(router, [INTO, UP]) == 1
    assert bad_turns(router, [INTO, ON]) == 0
    assert bad_turns(router, [INTO]) == 0


def test_bad_turns_sees_through_a_zero_length_fragment() -> None:
    # 封閉圈旋轉可能切出 [p, p]：它兩側的接點都量到同一個轉彎
    assert bad_turns(TurnEdgesRouter(blocked=CORNER), [INTO, seg([CORNER, CORNER]), UP]) == 2


def test_case_with_a_bad_turn_is_unsafe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runner, "match_shape", lambda *args, **kwargs: fake_match_result([INTO, UP]))
    case = Case("sq", UNIT_SQUARE, (121.5, 25.05), 1000.0, 0.0)
    (res,) = run_cases(TurnEdgesRouter(blocked=CORNER), [case], denied_ids=frozenset(), warm_up=False)
    assert res.error == UNSAFE and "轉彎" in res.safety
    assert res.bad_turns == 1
    assert "不安全 1" in summarize([res]) and "轉彎不能直接騎的接點 1 個" in summarize([res])
    assert compare([res], {})[0].startswith("sq：安全斷言沒過（UnsafeRoute：1 個接點的轉彎")


def test_case_without_bad_turns_is_unaffected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runner, "match_shape", lambda *args, **kwargs: fake_match_result([INTO, ON]))
    case = Case("sq", UNIT_SQUARE, (121.5, 25.05), 1000.0, 0.0)
    (res,) = run_cases(TurnEdgesRouter(blocked=CORNER), [case], denied_ids=frozenset(), warm_up=False)
    assert res.error is None and res.bad_turns == 0


@pytest.mark.valhalla
def test_bad_turns_on_real_tiles(crossing_config: Path) -> None:
    router = Router(crossing_config)
    cross = grid_point(X3 + 1, 1)
    bridge, under = seg([grid_point(X3, 1), cross]), seg([cross, grid_point(X3 + 1, 2)])
    assert bad_turns(router, [bridge, under]) == 1
    node = grid_point(X3, 1)
    assert bad_turns(router, [seg([grid_point(X3, 0), node]), seg([node, cross])]) == 0


# ---- 評分 v2：CaseResult 同時記 v1（legacy）分數；v1 baseline 用 v1 分數比，v2 baseline 用 v2 分數比 ----

V2 = {"score_version": SCORE_VERSION}


def test_score_version_is_three() -> None:
    assert SCORE_VERSION == 3  # P2.5：score() 換成 v3
    assert result("a", 1).score_version == SCORE_VERSION


def test_compare_with_a_legacy_baseline_uses_the_legacy_fields() -> None:
    baseline = {name: {"overall": 80, "coverage": 80, "error": None} for name in ("routing", "metric", "cov")}
    results = [
        scored("routing", 95, 95, 95, legacy=Score(70, 70, 70)),  # 路線退步：v2 變高也要抓到
        scored("metric", 50, 50, 50, legacy=Score(80, 80, 80)),  # 只有評分換了，路線沒變：不算退步
        scored("cov", 90, 90, 90, legacy=Score(60, 100, 80)),
    ]
    assert compare(results, baseline) == ["routing：overall（v1 評分）80 → 70", "cov：coverage（v1 評分）80 → 60"]


def test_compare_with_a_v2_baseline_uses_the_v2_fields() -> None:
    baseline = {name: {"overall": 80, "coverage": 80, "error": None, **V2} for name in ("v2drop", "legacydrop")}
    results = [
        scored("v2drop", 70, 70, 70, legacy=Score(80, 80, 80)),
        scored("legacydrop", 80, 80, 80, legacy=Score(10, 10, 10)),
    ]
    assert compare(results, baseline) == ["v2drop：overall 80 → 70"]


def test_compare_still_flags_safety_failures_against_a_legacy_baseline() -> None:
    unsafe_case = replace(scored("a", 99, 99, 99), error=UNSAFE, safety="缺口")
    assert compare([unsafe_case], {"a": {"overall": 0, "coverage": 0, "error": UNSAFE}}) == ["a：安全斷言沒過（UnsafeRoute：缺口）"]


def test_run_cases_records_the_v1_score_of_the_same_route(monkeypatch: pytest.MonkeyPatch) -> None:
    case = Case("sq", UNIT_SQUARE, (121.5, 25.05), 600.0, 0.0)  # 對角線 849 m：v1 的 0 分距離 42 m，v2 是 60 m，兩版分數不同
    (stroke,) = place(case.shape, case.center, case.size_m, case.rotation_deg)
    closed = stroke.points + [stroke.points[0]]  # 封閉筆畫的目標含收尾那一段（與 matching.service 相同）
    route = [(lon + 0.0002, lat + 0.0001) for lon, lat in closed]  # 偏 20 m 左右：分數在中間值
    expected = score_v1([closed], route)
    assert 0 < expected.coverage < 100 and expected not in (Score(80, 70, 75), score([closed], route))
    fake = replace(fake_match_result([seg(route)]), route_points=route)  # match_shape 回傳的 v2 分數是 Score(80, 70, 75)
    monkeypatch.setattr(runner, "match_shape", lambda *args, **kwargs: fake)
    (res,) = run_cases(EdgesRouter(), [case], denied_ids=frozenset(), warm_up=False)
    assert (res.coverage, res.cleanliness, res.overall) == (80, 70, 75)
    assert (res.legacy_coverage, res.legacy_cleanliness, res.legacy_overall) == (
        expected.coverage, expected.cleanliness, expected.overall,
    )
    assert res.score_version == SCORE_VERSION


def test_closed_target_includes_the_closing_edge_for_the_v1_score(monkeypatch: pytest.MonkeyPatch) -> None:
    # 路線完全照封閉目標騎一圈：v1 也該是滿分；漏了收尾那段，路線的收尾邊就會被當成多出來的線
    case = Case("sq", UNIT_SQUARE, (121.5, 25.05), 1000.0, 0.0)
    (stroke,) = place(case.shape, case.center, case.size_m, case.rotation_deg)
    route = stroke.points + [stroke.points[0]]
    fake = replace(fake_match_result([seg(route)]), route_points=route)
    monkeypatch.setattr(runner, "match_shape", lambda *args, **kwargs: fake)
    (res,) = run_cases(EdgesRouter(), [case], denied_ids=frozenset(), warm_up=False)
    assert (res.legacy_coverage, res.legacy_cleanliness, res.legacy_overall) == (100, 100, 100)


def test_row_and_summary_show_the_v2_and_v1_overall_and_direction() -> None:
    a = scored("a", 80, 81, 82, v2=Score(70, 70, 71), legacy=Score(60, 60, 61), direction=55)
    b = scored("b", 60, 60, 60, v2=Score(50, 50, 51), legacy=Score(40, 40, 41), direction=30)
    assert format_header().split()[:7] == ["name", "overall", "v2", "v1", "cov", "clean", "dir"]
    assert format_row(a).split()[:7] == ["a", "82", "71", "61", "80", "81", "55"]
    failed = scored("c", 0, 0, 0, v2=Score(0, 0, 0), legacy=Score(0, 0, 0), error="AllStrokesFailed")
    assert "平均 overall 71.0（v2 評分 61.0、v1 評分 51.0）" in summarize([a, b, failed])
    assert "平均 overall -（v2 評分 -、v1 評分 -）" in summarize([failed])


def test_cli_update_baseline_writes_v2_and_legacy_fields(fake_run: FakeRun) -> None:
    fake_run([scored("a", 80, 70, 75, legacy=Score(60, 50, 55))], "--update-baseline")
    saved = json.loads(fake_run.baseline.read_text())["a"]
    assert (saved["coverage"], saved["cleanliness"], saved["overall"]) == (80, 70, 75)
    assert (saved["legacy_coverage"], saved["legacy_cleanliness"], saved["legacy_overall"]) == (60, 50, 55)
    assert saved["score_version"] == SCORE_VERSION
    # 剛寫的 baseline 是 v2：下一次比 v2 分數，v1 分數掉了不算退步
    fake_run([scored("a", 80, 70, 75, legacy=Score(0, 0, 0))])


def test_cli_says_when_it_compares_against_a_legacy_baseline(fake_run: FakeRun, capsys: pytest.CaptureFixture[str]) -> None:
    fake_run.baseline.write_text(json.dumps({"a": {"overall": 50, "coverage": 50, "error": None}}))
    fake_run([scored("a", 90, 90, 90, legacy=Score(50, 50, 50))])  # v1 分數沒退步：exit 0
    assert "提示：baseline 有 1 個案例是 v1 評分" in capsys.readouterr().out
    fake_run.baseline.write_text(json.dumps({"a": {"overall": 50, "coverage": 50, "error": None, **V2}}))
    fake_run([scored("a", 90, 90, 90, legacy=Score(50, 50, 50))])
    assert "提示：baseline" not in capsys.readouterr().out


def test_unknown_score_version_is_not_compared_with_another_scale() -> None:
    # 審查：以前任何不是目前版本的條目都被當成 v1；未來的 v3 條目會被拿 v1 分數比
    from engine.bench.runner import _regression

    r = scored("c", 80, 80, 80)
    assert "score_version" in (_regression(r, {"overall": 70, "coverage": 70, "score_version": SCORE_VERSION + 1}, 3) or "")
    assert _regression(r, {"overall": 70, "coverage": 70, "score_version": 1}, 3) is None


# ---- P2.5：主分數是 score()（v3 帶 direction），另記 v2、v1；退步比較依 baseline 的 score_version 選尺 ----


def entry(version: object = None, overall: int = 80, coverage: int = 80) -> dict:
    out = {"overall": overall, "coverage": coverage, "error": None}
    if version is not None:
        out["score_version"] = version
    return out


# 三把尺刻意給不同的分數：主分數 60、v2 70、v1 90；baseline 都是 80
THREE = scored("x", 60, 60, 60, v2=Score(70, 70, 70), legacy=Score(90, 90, 90))


def test_compare_picks_the_scale_by_the_baseline_score_version(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runner, "SCORE_VERSION", 3)  # v3 上線後的情況（現在 SCORE_VERSION 還是 2 時，v2 就是目前版本）
    assert compare([THREE], {"x": entry()}) == []  # 沒有 score_version = v1：90 沒有退步
    assert compare([THREE], {"x": entry(1)}) == []
    assert compare([THREE], {"x": entry(2)}) == ["x：overall（v2 評分）80 → 70"]
    assert compare([THREE], {"x": entry(3)}) == ["x：overall 80 → 60"]
    assert compare([THREE], {"x": entry(2, overall=60, coverage=90)}) == ["x：coverage（v2 評分）90 → 70"]


def test_a_v2_baseline_uses_the_v2_fields_whatever_the_current_version() -> None:
    (problem,) = compare([THREE], {"x": entry(2)})
    assert problem.endswith("overall 80 → 70") or problem.endswith("overall（v2 評分）80 → 70")
    assert compare([scored("x", 10, 10, 10, v2=Score(80, 80, 80))], {"x": entry(2)}) == []


@pytest.mark.parametrize("version", [0, SCORE_VERSION + 1, "2", None])
def test_compare_fails_loudly_on_an_unknown_score_version(version: object) -> None:
    base = entry(version)
    if version is None:
        base["score_version"] = None  # 明寫 null 也不是 v1
    (problem,) = compare([THREE], {"x": base})
    assert problem.startswith("x：baseline 的 score_version") and "無法比較" in problem


def test_older_entries_counts_baseline_entries_on_an_older_scale(monkeypatch: pytest.MonkeyPatch) -> None:
    baseline = {"a": entry(), "b": entry(2), "c": entry(2), "gone": entry(1)}
    results = [scored(n, 80, 80, 80) for n in ("a", "b", "c", "new")]
    monkeypatch.setattr(runner, "SCORE_VERSION", 3)
    assert runner.older_entries(results, baseline) == {1: 1, 2: 2}
    monkeypatch.setattr(runner, "SCORE_VERSION", 2)
    assert runner.older_entries(results, baseline) == {1: 1}


def test_cli_says_when_it_compares_against_a_v2_baseline_after_the_upgrade(
    fake_run: FakeRun, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(runner, "SCORE_VERSION", 3)
    fake_run.baseline.write_text(json.dumps({"a": entry(2, overall=70, coverage=70)}))
    fake_run([scored("a", 10, 10, 10, v2=Score(70, 70, 70))])  # v2 分數沒退步：exit 0，主分數低不算
    assert "提示：baseline 有 1 個案例是 v2 評分" in capsys.readouterr().out


def test_run_cases_records_direction_and_the_v2_score_of_the_same_route(monkeypatch: pytest.MonkeyPatch) -> None:
    case = Case("sq", UNIT_SQUARE, (121.5, 25.05), 600.0, 0.0)
    (stroke,) = place(case.shape, case.center, case.size_m, case.rotation_deg)
    closed = stroke.points + [stroke.points[0]]
    route = [(lon + 0.0002, lat + 0.0001) for lon, lat in closed]
    expected = score_v2([closed], route)
    assert 0 < expected.coverage < 100 and expected.overall != score_v1([closed], route).overall
    main = Score(80, 70, 75, direction=42)
    fake = replace(fake_match_result([seg(route)]), route_points=route, score=main)
    monkeypatch.setattr(runner, "match_shape", lambda *args, **kwargs: fake)
    (res,) = run_cases(EdgesRouter(), [case], denied_ids=frozenset(), warm_up=False)
    assert (res.coverage, res.cleanliness, res.overall, res.direction) == (80, 70, 75, 42)
    assert (res.v2_coverage, res.v2_cleanliness, res.v2_overall) == (expected.coverage, expected.cleanliness, expected.overall)
    assert res.score_version == SCORE_VERSION


def test_run_cases_writes_one_svg_per_case_into_a_new_directory(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def fake_match(router, strokes, *, search_radius_m, deadline):
        if strokes[0].points[0][0] > 121.55:
            raise AllStrokesFailed([])
        return fake_match_result([seg([A, B]), seg([B, C], "detour")])

    monkeypatch.setattr(runner, "match_shape", fake_match)
    out = tmp_path / "svg" / "nested"
    cases = [Case("a@x 1", LINE, (121.50, 25.05), 1000.0, 0.0), Case("far", LINE, (121.60, 25.05), 1000.0, 0.0)]
    run_cases(EdgesRouter(), cases, denied_ids=frozenset(), svg_dir=out)
    assert sorted(p.name for p in out.iterdir()) == ["a_x_1.svg", "far.svg"]  # 失敗的案例也畫（只有目標）
    root = ET.fromstring((out / "a_x_1.svg").read_text())
    texts = [t.text or "" for t in root.iter(f"{SVG_NS}text")]
    assert texts[0] == "a@x 1" and any("75" in t and "dir" in t for t in texts)
    colours = {line.get("stroke") for line in root.iter(f"{SVG_NS}polyline")}
    assert {KIND_COLORS["stroke"], KIND_COLORS["detour"]} <= colours
    assert any("AllStrokesFailed" in (t.text or "") for t in ET.fromstring((out / "far.svg").read_text()).iter(f"{SVG_NS}text"))


def test_run_cases_without_svg_dir_writes_nothing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(runner, "match_shape", lambda *args, **kwargs: fake_match_result())
    monkeypatch.chdir(tmp_path)
    run_cases(EdgesRouter(), [Case("a", LINE, (121.50, 25.05), 1000.0, 0.0)], denied_ids=frozenset())
    assert not list(tmp_path.iterdir())


def test_cli_update_baseline_writes_the_current_version_with_direction_and_v2(fake_run: FakeRun) -> None:
    fake_run([scored("a", 80, 70, 75, v2=Score(60, 50, 55), direction=33)], "--update-baseline")
    saved = json.loads(fake_run.baseline.read_text())["a"]
    assert saved["score_version"] == SCORE_VERSION and saved["direction"] == 33
    assert (saved["v2_coverage"], saved["v2_cleanliness"], saved["v2_overall"]) == (60, 50, 55)


def test_cli_passes_router_options_to_the_router_and_prints_them_first(
    fake_run: FakeRun, capsys: pytest.CaptureFixture[str]
) -> None:
    fake_run.baseline.write_text("{}")  # 非正式參數不能 --update-baseline（只比對、不寫入）
    fake_run([result("a", 80)], "--turn-penalty-factor", "none", "--sigma-z", "6")
    assert fake_run.router_kwargs == [{"turn_penalty_factor": None, "sigma_z": 6.0}]
    assert capsys.readouterr().out.splitlines()[0] == "吸附參數：turn_penalty_factor none（不帶，由 meili 設定決定）、sigma_z 6"
    fake_run([result("a", 80)], "--turn-penalty-factor", "140")
    assert fake_run.router_kwargs[-1] == {"turn_penalty_factor": 140.0, "sigma_z": None}


def test_cli_router_options_default_to_the_router_defaults(fake_run: FakeRun, capsys: pytest.CaptureFixture[str]) -> None:
    fake_run([result("a", 80)], "--update-baseline")
    assert fake_run.router_kwargs == [{"turn_penalty_factor": FAST_TURN_PENALTY_FACTOR, "sigma_z": None}]
    assert capsys.readouterr().out.splitlines()[0] == "吸附參數：turn_penalty_factor 500、sigma_z 設定檔預設"
    assert fake_run.svg_dirs == [None]


@pytest.mark.parametrize("flags", [["--turn-penalty-factor", "fast"], ["--sigma-z", "-1"]])
def test_cli_bad_router_option_is_a_usage_error(fake_run: FakeRun, flags: list[str]) -> None:
    with pytest.raises(SystemExit) as usage:
        fake_run([result("a", 80)], *flags)
    assert usage.value.code == 2 and fake_run.router_kwargs == []


def test_cli_passes_the_svg_dir(fake_run: FakeRun, tmp_path: Path) -> None:
    fake_run([result("a", 80)], "--update-baseline", "--svg-dir", str(tmp_path / "s"))
    assert fake_run.svg_dirs == [tmp_path / "s"]


# ---- --no-prune：改前的路線（不剪小刺與小圈），給本機比較改前／改後 ----


def test_run_cases_passes_match_options_to_every_match_including_warm_up(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[dict] = []

    def fake_match(router, strokes, *, search_radius_m, deadline, **kwargs):
        seen.append(kwargs)
        return fake_match_result()

    monkeypatch.setattr(runner, "match_shape", fake_match)
    cases = [Case("a1", LINE, (121.50, 25.05), 1000.0, 0.0)]
    run_cases(EdgesRouter(), cases, denied_ids=frozenset(), match_options={"prune": False})
    assert seen == [{"prune": False}, {"prune": False}]  # 預熱一次＋正式一次
    seen.clear()
    run_cases(EdgesRouter(), cases, denied_ids=frozenset(), warm_up=False)
    assert seen == [{}]  # 預設與正式環境相同：不帶額外參數


def test_cli_no_prune_reaches_run_cases(fake_run: FakeRun, capsys: pytest.CaptureFixture[str]) -> None:
    fake_run.baseline.write_text("{}")
    fake_run([], "--no-prune", "--turn-penalty-factor", "none")
    assert fake_run.match_options == [{"prune": False}]
    assert fake_run.router_kwargs == [{"turn_penalty_factor": None, "sigma_z": None}]
    assert "剪小刺與小圈：關" in capsys.readouterr().out
    fake_run([])
    assert fake_run.match_options[-1] == {}


@pytest.mark.parametrize("flags", [["--no-prune"], ["--turn-penalty-factor", "none"], ["--sigma-z", "15"]])
def test_cli_update_baseline_only_with_production_settings(fake_run: FakeRun, flags: list[str]) -> None:
    with pytest.raises(SystemExit) as refused:
        fake_run([result("a", 80)], "--update-baseline", *flags)
    assert refused.value.code == 2
    assert not fake_run.baseline.exists()


# ---- --quality fine（PR B）：描形規劃器，需要 current build 的路網圖 ----


def test_cli_quality_fine_loads_the_graph_and_passes_it(fake_run: FakeRun, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    graph = object()
    monkeypatch.setattr(bench_cli, "RideGraph", SimpleNamespace(load=lambda build: graph))
    fake_run.baseline.write_text("{}")
    fake_run([], "--quality", "fine")
    assert fake_run.match_options == [{"quality": "fine", "graph": graph}]
    assert "quality fine" in capsys.readouterr().out


def test_cli_quality_fine_without_a_graph_exits(fake_run: FakeRun) -> None:
    fake_run.baseline.write_text("{}")
    with pytest.raises(SystemExit) as stopped:
        fake_run([], "--quality", "fine")  # 假的 build 沒有路網圖
    assert "路網圖" in str(stopped.value.code)
    assert fake_run.match_options == []


def test_cli_update_baseline_refuses_quality_fine(fake_run: FakeRun) -> None:
    with pytest.raises(SystemExit) as refused:
        fake_run([result("a", 80)], "--update-baseline", "--quality", "fine")
    assert refused.value.code == 2
