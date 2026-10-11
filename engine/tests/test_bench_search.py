"""搜尋 bench：在合成城市的完整 build 上跑得起來；不合格規則（時間、候選數、安全、樓梯）不能空洞。"""

import json
from dataclasses import replace
from pathlib import Path

import pytest

from engine.bench import search as bench
from engine.bench.search import CandidateRow, SearchCaseResult, problems_of, spearman, summary, top_within
from engine.bench.search_cases import SEARCH_CASES, SearchCase, default_text
from engine.data.build import DENIED_FILE
from engine.data.layout import DataLayout
from engine.data.pipeline import run_build
from engine.data.rideable import extract_rideable
from engine.routing.tiles import build_tiles
from engine.matching.service import MatchResult
from engine.matching.score import Score
from engine.search.types import Candidate, Evaluation, NormStroke, Placement
from osm_fixture import write_grid_pbf
from search_city import CITY_CENTER, CITY_MAP

SQUARE = [NormStroke([(-0.5, -0.5), (0.5, -0.5), (0.5, 0.5), (-0.5, 0.5)], closed=True)]
GOOD = CandidateRow(90, 90, 90, 2.7, True, 100, 670, 0.0, None, 0, 0.0, 0)


def result(**overrides) -> SearchCaseResult:
    base = SearchCaseResult("x", 12.0, 3.0, 1000, 10, [GOOD] * 5, 0.5, False)
    return replace(base, **overrides)


def test_problem_rules() -> None:
    assert problems_of(result()) == []
    assert problems_of(result(elapsed_s=30.5))
    assert problems_of(result(candidates=[GOOD] * 4))
    assert problems_of(result(error="MatchTimeout"))
    assert problems_of(result(candidates=[GOOD] * 4 + [replace(GOOD, unsafe="UnsafeRoute：x")]))
    assert problems_of(result(candidates=[GOOD] * 4 + [replace(GOOD, bad_turns=1)]))
    assert problems_of(result(candidates=[GOOD] * 4 + [replace(GOOD, steps_m=3.0)]))
    assert problems_of(result(candidates=[replace(GOOD, length_ok=False)] * 5)) == []  # 公里數不符只標記，不算不合格


def test_spearman() -> None:
    def ev(origin, overall, rescaled=False):
        return Evaluation(origin, rescaled, overall, True, True)

    assert spearman([ev(0, 90), ev(1, 80), ev(2, 70), ev(3, 60)]) == pytest.approx(1.0)
    assert spearman([ev(0, 60), ev(1, 70), ev(2, 80), ev(3, 90)]) == pytest.approx(-1.0)
    assert spearman([ev(0, 90), ev(1, None), ev(2, 70)]) is None  # 少於 3 個有效點
    assert spearman([ev(0, 90), ev(1, 80), ev(2, 70), ev(0, 10, rescaled=True)]) == pytest.approx(1.0)


def scored(overall: int) -> Candidate:
    result = MatchResult([(121.5, 25.0)], 3000.0, [], Score(overall, overall, overall), 3000.0, [])
    return Candidate(Placement((121.5, 25.0), 700.0, 0.0), result, 100.0, True)


def test_top_within_previous_k() -> None:
    # 搜尋排好的順序：粗篩第 12、3、15、0、9、11、4 名
    ranked = [(o, scored(s)) for o, s in [(12, 95), (3, 90), (15, 88), (0, 80), (9, 70), (11, 65), (4, 60)]]
    assert top_within(ranked, 10) == [90, 80, 70, 60]
    assert top_within(ranked, 16) == [95, 90, 88, 80, 70]


def test_summary_counts_late_candidates_and_gain() -> None:
    late = [replace(GOOD, overall=95, origin=12)] + [replace(GOOD, origin=k) for k in range(4)]  # 95 90 90 90 90
    a = result(candidates=late, previous_k=[90, 90, 90, 90, 80])
    b = result(candidates=[GOOD] * 5, previous_k=[90] * 5)
    text = summary([a, b])
    assert "第 11 名以後：1/10" in text
    assert "（中位數）+3.0" in text  # 兩個案例的差 0.0、+3.0：偶數個時取較大的那個（與時間中位數同一個算法）
    assert "第 1 名分數變高 1/2" in text
    assert "少於 5 個候選 0 案例" in text


def test_cases_cover_completion_condition() -> None:
    assert len(SEARCH_CASES) == 12
    assert all(c.radius_m == 5000 for c in SEARCH_CASES)
    strokes = default_text()
    assert len(strokes) == 15 and sum(len(s.points) for s in strokes) == 68 and sum(s.closed for s in strokes) == 1
    for case in SEARCH_CASES:
        points = [p for s in case.strokes for p in s.points]
        xs, ys = [x for x, _ in points], [y for _, y in points]
        assert max(max(xs) - min(xs), max(ys) - min(ys)) == pytest.approx(1.0)
        assert (min(xs) + max(xs)) / 2 == pytest.approx(0, abs=1e-9) and (min(ys) + max(ys)) / 2 == pytest.approx(0, abs=1e-9)


@pytest.mark.valhalla
def test_cli_runs_on_a_real_build(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    pbf = tmp_path / "city.osm.pbf"
    write_grid_pbf(pbf, CITY_MAP)

    def builder(src: Path, build_dir: Path) -> None:
        extract_rideable(src, build_dir)
        build_tiles(src, build_dir)
        (build_dir / DENIED_FILE).write_text("[]")

    run_build(DataLayout(tmp_path), pbf, "md5", "20261011T000000Z", builder, lambda d: [])
    case = SearchCase("square@city", SQUARE, CITY_CENTER, 800.0, 2.0, 4.0, (-15.0, 15.0))
    monkeypatch.setattr(bench, "SEARCH_CASES", [case])
    monkeypatch.setattr(bench, "TOP_N", 1)
    out = tmp_path / "out.json"
    bench.main(["--data-dir", str(tmp_path), "--routers", "2", "--json", str(out)])
    rows = json.loads(out.read_text())
    assert rows[0]["name"] == "square@city" and rows[0]["candidates"]
    assert rows[0]["candidates"][0]["unsafe"] is None and rows[0]["candidates"][0]["bad_turns"] == 0
    assert rows[0]["evaluations"] and {"origin", "rescaled", "overall"} <= rows[0]["evaluations"][0].keys()
    assert rows[0]["candidates"][0]["origin"] in {e["origin"] for e in rows[0]["evaluations"]}
    assert "square@city" in capsys.readouterr().out


def test_cli_refuses_build_without_road_index(tmp_path: Path) -> None:
    run_build(DataLayout(tmp_path), tmp_path / "x.pbf", "md5", "20261011T000000Z", lambda s, d: None, lambda d: [])
    with pytest.raises(SystemExit, match="可騎道路索引"):
        bench.main(["--data-dir", str(tmp_path)])
