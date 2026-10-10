from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from engine.api.models import ErrorResponse, MatchRequest, RouteOut, ScoreOut, SegmentOut, ValidationErrorResponse
from engine.app import create_app
from engine.data.layout import DataLayout
from engine.data.pipeline import run_build
from engine.matching.deadline import MatchTimeout
from engine.matching.service import AllStrokesFailed, OutsideTaiwan, TargetTooSmall
from engine.matching.types import MatchWarning
from engine.routing.router import RoutingError

LINE = {"points": [[121.5, 25.05], [121.6, 25.05]]}


@pytest.mark.parametrize("radius", [None, 30, 100.5, 150])
def test_radius_bounds_are_inclusive(radius: float | None) -> None:
    request = MatchRequest.model_validate({"strokes": [LINE], "search_radius_m": radius})
    assert request.search_radius_m == radius


def test_stroke_limits_are_inclusive() -> None:
    MatchRequest.model_validate({"strokes": [LINE] * 40})
    MatchRequest.model_validate({"strokes": [{"points": [[121.5, 25.05]] * 2000}]})


@pytest.mark.parametrize("scores", [(-1, 0, 0), (0, 101, 0), (0, 0, 101), (0, 0, -1)])
def test_score_is_a_percentage(scores: tuple[int, int, int]) -> None:
    with pytest.raises(ValidationError):
        ScoreOut(coverage=scores[0], cleanliness=scores[1], overall=scores[2])
    ScoreOut(coverage=0, cleanliness=100, overall=50)


def test_lengths_are_not_negative() -> None:
    with pytest.raises(ValidationError):
        RouteOut(points=[], length_m=-0.1)
    with pytest.raises(ValidationError):
        SegmentOut(kind="stroke", stroke_index=0, points=[], length_m=-0.1)
    assert RouteOut(points=[], length_m=0).length_m == 0


def post_with_match_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: Exception):
    layout = DataLayout(tmp_path)
    run_build(layout, tmp_path / "x.pbf", "abc", "20261010T000000Z", lambda pbf, d: None, lambda d: [])

    def failing(router, strokes, *, search_radius_m, deadline):
        raise error

    monkeypatch.setattr("engine.app.match_shape", failing)
    api = TestClient(create_app(tmp_path, router_factory=lambda build: object()))
    return api.post("/match", json={"strokes": [LINE]})


@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (OutsideTaiwan("圖形不在台灣範圍內"), 400, "outside_taiwan"),
        (TargetTooSmall("圖形太小"), 422, "too_small"),
        (AllStrokesFailed([MatchWarning(0, "no_route", "附近找不到道路")]), 422, "no_route"),
        (MatchTimeout("逾時"), 504, "timeout"),
    ],
)
def test_domain_errors_conform_to_the_declared_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: Exception, status: int, code: str
) -> None:
    response = post_with_match_error(tmp_path, monkeypatch, error)
    assert response.status_code == status
    parsed = ErrorResponse.model_validate(response.json())
    assert parsed.detail.code == code
    assert (parsed.detail.warnings is not None) == (code == "no_route")


def test_no_data_conforms_to_the_declared_model(tmp_path: Path) -> None:
    def broken(build: Path):
        raise RoutingError("x")

    layout = DataLayout(tmp_path)
    run_build(layout, tmp_path / "x.pbf", "abc", "20261010T000000Z", lambda pbf, d: None, lambda d: [])
    for api in (TestClient(create_app(tmp_path / "empty")), TestClient(create_app(tmp_path, router_factory=broken))):
        response = api.post("/match", json={"strokes": [LINE]})
        assert response.status_code == 503
        assert ErrorResponse.model_validate(response.json()).detail.code == "no_data"


def test_validation_errors_conform_to_the_declared_model(tmp_path: Path) -> None:
    response = TestClient(create_app(tmp_path)).post("/match", json={"strokes": []})
    assert response.status_code == 422
    assert ValidationErrorResponse.model_validate(response.json()).detail
