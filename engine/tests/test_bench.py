from pathlib import Path

import pytest

from engine.bench.cases import CASES, Case, Shape, place
from engine.bench.runner import CaseResult, compare, run_cases
from engine.routing.geo import haversine_m
from engine.routing.router import Router
from osm_fixture import grid_point

UNIT_SQUARE = Shape("square", [([(0, 0), (1, 0), (1, 1), (0, 1)], True)])


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


def test_cases_are_unique_and_inside_taiwan() -> None:
    names = [c.name for c in CASES]
    assert len(names) == len(set(names)) >= 12
    for case in CASES:
        lon, lat = case.center
        assert 118.1 <= lon <= 122.1 and 21.8 <= lat <= 26.4


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
