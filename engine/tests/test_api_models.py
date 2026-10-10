import pytest

from engine.api.models import MatchRequest

LINE = {"points": [[121.5, 25.05], [121.6, 25.05]]}


@pytest.mark.parametrize("radius", [None, 30, 100.5, 150])
def test_radius_bounds_are_inclusive(radius: float | None) -> None:
    request = MatchRequest.model_validate({"strokes": [LINE], "search_radius_m": radius})
    assert request.search_radius_m == radius


def test_stroke_limits_are_inclusive() -> None:
    MatchRequest.model_validate({"strokes": [LINE] * 40})
    MatchRequest.model_validate({"strokes": [{"points": [[121.5, 25.05]] * 2000}]})
