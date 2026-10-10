import json
import math
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from engine.bench import __main__ as bench_cli
from engine.bench import runner
from engine.bench.cases import CASES, LOCATIONS, SHAPES, Case, Shape, place
from engine.bench.runner import (
    PRODUCTION_TIMEOUT_MS,
    REPORTED_USES,
    SAFETY_ERRORS,
    UNDETERMINED,
    UNSAFE,
    CaseResult,
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
from engine.matching.score import Score
from engine.matching.deadline import Deadline
from engine.matching.service import AllStrokesFailed, MatchResult, StrokeInput, match_shape
from engine.matching.types import JOIN_TOLERANCE_M, Segment
from engine.routing.geo import haversine_m, polyline_length_m
from engine.routing.router import EdgeInfo, Router, RoutingError
from engine.routing.tiles import build_tiles
from grid_helpers import densify
from osm_fixture import EXPRESSWAY_MAP, EXPRESSWAY_WAY_ID, X0, grid_point, write_grid_pbf

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


def result(name: str, overall: int, error: str | None = None) -> CaseResult:
    return CaseResult(name, overall, overall, overall, 1000.0, 1000.0, 10, error)


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
        monkeypatch.setattr(bench_cli, "Router", SimpleNamespace(from_build=lambda build: object()))
        monkeypatch.setattr(bench_cli, "run_cases", self._run_cases)
        (layout.current() / DENIED_FILE).write_text("[]")

    def _run_cases(self, router, cases, timeout_s=30.0, *, denied_ids, warm_up=True, on_result=None):
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
    assert "退步：a：overall 90 → 80" in out
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


def test_compare_flags_coverage_drop_even_when_overall_holds() -> None:
    # ledger L120：overall 可能因 cleanliness 上升而持平，覆蓋率卻掉到 0（例如部件被整段丟掉）
    base = {"a": {"overall": 50, "coverage": 66, "error": None}, "old": {"overall": 50, "error": None}}
    results = [CaseResult("a", 0, 100, 50, 1.0, 1.0, 1, None), CaseResult("old", 0, 100, 50, 1.0, 1.0, 1, None)]
    assert compare(results, base) == ["a：coverage 66 → 0"]  # 沒有 coverage 的舊條目只比 overall


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
    assert use_meters(EdgesRouter(error="boom"), [seg([A, B])]) == ZERO_USES


def test_case_result_row_and_summary_carry_use_meters(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runner, "match_shape", lambda *args, **kwargs: fake_match_result())
    case = Case("sq", UNIT_SQUARE, (121.5, 25.05), 1000.0, 0.0)
    (res,) = run_cases(UseRouter(), [case], denied_ids=frozenset(), warm_up=False)
    assert res.error is None
    assert res.use_m == {"footway": 12.0, "steps": 4.0, "parking_aisle": 0.0}
    assert format_header().split()[7:10] == ["foot_m", "steps_m", "park_m"]
    assert format_row(res).split()[7:10] == ["12", "4", "0"]
    assert "footway／steps／parking_aisle 合計 12／4／0 m" in summarize([res])


def test_failed_case_has_no_use_meters_and_still_formats() -> None:
    failed = result("x", 0, error="AllStrokesFailed")
    assert failed.use_m == {}
    assert format_row(failed).split()[7:10] == ["0", "0", "0"]


@pytest.mark.valhalla
def test_edges_report_use_and_length_on_real_tiles(grid_config: Path) -> None:
    router = Router(grid_config)
    route = router.route(grid_point(0, 1), grid_point(2, 1))
    edges = router.edges(route.points)
    assert edges and {e.use for e in edges} == {"road"}  # 合成地圖全是 residential
    assert sum(e.length_km for e in edges) == pytest.approx(route.length_km, rel=0.05)
