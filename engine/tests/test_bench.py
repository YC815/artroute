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
    CaseResult,
    case_set_changes,
    compare,
    format_row,
    load_baseline,
    run_cases,
    summarize,
)
from engine.data.layout import DataLayout
from engine.matching.deadline import MatchTimeout
from engine.matching.score import Score
from engine.matching.service import AllStrokesFailed, MatchResult
from engine.routing.geo import haversine_m
from engine.routing.router import Router
from osm_fixture import grid_point

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
    (res,) = run_cases(Router(grid_config), [case])
    assert res.error is None
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


def fake_match_result() -> MatchResult:
    return MatchResult([], 900.0, [], Score(80, 70, 75), 1000.0, [])


def test_run_cases_warms_up_each_location_once_then_streams_each_result(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []

    def fake_match(router, strokes, *, search_radius_m, deadline):
        events.append("match")
        return fake_match_result()

    monkeypatch.setattr(runner, "match_shape", fake_match)
    here, there = (121.50, 25.05), (121.60, 25.10)
    cases = [Case("a1", LINE, here, 1000.0, 0.0), Case("a2", LINE, here, 1000.0, 30.0), Case("b1", LINE, there, 1000.0, 0.0)]

    results = run_cases(object(), cases, on_result=lambda r: events.append(f"result:{r.name}"))
    assert [r.name for r in results] == ["a1", "a2", "b1"]
    assert events == ["match", "match", "match", "result:a1", "match", "result:a2", "match", "result:b1"]

    events.clear()
    run_cases(object(), cases, warm_up=False, on_result=lambda r: events.append(f"result:{r.name}"))
    assert events == ["match", "result:a1", "match", "result:a2", "match", "result:b1"]


@pytest.mark.parametrize("error", [AllStrokesFailed([]), MatchTimeout()])
def test_failed_case_keeps_the_real_target_length(monkeypatch: pytest.MonkeyPatch, error: Exception) -> None:
    def boom(*args, **kwargs):
        raise error

    monkeypatch.setattr(runner, "match_shape", boom)
    case = Case("sq", UNIT_SQUARE, (121.5, 25.05), 1000.0, 0.0)
    (res,) = run_cases(object(), [case])  # 預熱階段的失敗也不能讓整個跑分中斷
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

    def _run_cases(self, router, cases, timeout_s=30.0, *, warm_up=True, on_result=None):
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
    data.mkdir()
    DataLayout(data).current_link.symlink_to(grid_config.parent)
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
