"""P0 核心：bicycle 必須遵守單行道、允許 oneway:bicycle=no 的逆向、避開快速道路。"""

from pathlib import Path

import pytest

from engine.routing.router import Router, RoutingError
from osm_fixture import BOTTOM_ONEWAY_WAY_ID, CONTRAFLOW_WAY_ID, MOTORWAY_WAY_ID, grid_point

pytestmark = pytest.mark.valhalla


@pytest.fixture(scope="module")
def router(grid_config: Path) -> Router:
    return Router(grid_config)


def densify(start: tuple[float, float], end: tuple[float, float], n: int = 16) -> list[tuple[float, float]]:
    (x1, y1), (x2, y2) = start, end
    return [(x1 + (x2 - x1) * i / n, y1 + (y2 - y1) * i / n) for i in range(n + 1)]


def rides_westward_on_row(points: list[tuple[float, float]], row: int, col_min: int, col_max: int) -> bool:
    """路線是否在 row 這條街、col_min..col_max 之間往西騎。"""
    lat = grid_point(0, row)[1]
    lo, hi = grid_point(col_min, row)[0], grid_point(col_max, row)[0]
    for (x1, y1), (x2, y2) in zip(points, points[1:]):
        on_row = abs(y1 - lat) < 1e-5 and abs(y2 - lat) < 1e-5
        within = lo - 1e-6 <= min(x1, x2) and max(x1, x2) <= hi + 1e-6
        if on_row and within and x2 < x1 - 1e-7:
            return True
    return False


def touches_motorway_interior(points: list[tuple[float, float]]) -> bool:
    lat = grid_point(0, 3)[1]
    lo, hi = grid_point(0.5, 3)[0], grid_point(5.5, 3)[0]
    return any(abs(y - lat) < 1e-5 and lo < x < hi for x, y in points)


def test_trace_along_oneway_in_legal_direction(router: Router) -> None:
    result = router.trace(densify(grid_point(0, 0), grid_point(2, 0)))
    assert result.length_km == pytest.approx(0.40, abs=0.05)
    assert all(abs(lat - grid_point(0, 0)[1]) < 1e-4 for _, lat in result.points)


def test_route_against_oneway_detours(router: Router) -> None:
    result = router.route(grid_point(2, 0), grid_point(0, 0))
    assert not rides_westward_on_row(result.points, row=0, col_min=0, col_max=2)
    assert result.length_km > 0.8  # 必須繞上面那條街


def test_trace_against_oneway_raises(router: Router) -> None:
    # map_snap 對逆向段會悄悄退化成 0 km；Router 必須明確報錯，不能把退化結果當成功
    with pytest.raises(RoutingError):
        router.trace(densify(grid_point(2, 0), grid_point(0, 0)))


def test_trace_with_reverse_middle_section_raises(router: Router) -> None:
    # 0→2→0→2：中間逆向，map_snap 會丟掉逆向段之後的所有東西（含最後的合法段）
    legal, reverse = densify(grid_point(0, 0), grid_point(2, 0)), densify(grid_point(2, 0), grid_point(0, 0))
    with pytest.raises(RoutingError):
        router.trace(legal + reverse[1:] + legal[1:])


def test_contraflow_lets_bicycle_ride_against_oneway(router: Router) -> None:
    result = router.route(grid_point(6, 0), grid_point(4, 0))
    assert result.length_km == pytest.approx(0.40, abs=0.05)


def test_bicycle_route_avoids_motorway(router: Router) -> None:
    result = router.route(grid_point(0, 3), grid_point(6, 3))
    assert not touches_motorway_interior(result.points)
    assert result.length_km > 1.6


def test_edges_report_way_ids_and_heading(router: Router) -> None:
    forward = router.route(grid_point(0, 0), grid_point(2, 0))
    edges = router.edges(forward.points)
    oneway_edges = [e for e in edges if e.way_id == BOTTOM_ONEWAY_WAY_ID]
    assert oneway_edges
    assert all(45 <= e.begin_heading <= 135 for e in oneway_edges)  # 往東
    assert all(e.way_id not in (CONTRAFLOW_WAY_ID, MOTORWAY_WAY_ID) for e in edges)


def test_trace_far_from_roads_raises(router: Router) -> None:
    with pytest.raises(RoutingError):
        router.trace(densify((121.0, 24.0), (121.001, 24.0)))


def test_trace_accepts_search_radius_up_to_150m(router: Router) -> None:
    result = router.trace(densify(grid_point(0, 0), grid_point(2, 0)), search_radius_m=150)
    assert result.length_km == pytest.approx(0.40, abs=0.05)
