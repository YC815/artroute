"""P0 核心：bicycle 必須遵守單行道、允許 oneway:bicycle=no 的逆向、避開快速道路。"""

from pathlib import Path

import pytest

from engine.routing.router import Router, RoutingError
from grid_helpers import densify, rides_westward_on_row
from osm_fixture import BOTTOM_ONEWAY_WAY_ID, CONTRAFLOW_WAY_ID, MOTORWAY_WAY_ID, grid_point

pytestmark = pytest.mark.valhalla


@pytest.fixture(scope="module")
def router(grid_config: Path) -> Router:
    return Router(grid_config)


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


def test_edges_tolerate_repeated_vertex(router: Router) -> None:
    # edge_walk 逐點比對路段形狀：重複一個點（route 端點落在路口時會出現）就整條配不上
    along = router.route(grid_point(0, 1), grid_point(6, 1)).points
    assert router.edges([along[0], *along]) == router.edges(along)


def test_edges_of_zero_length_route_is_empty(router: Router) -> None:
    # 兩端都在不能騎的快速道路上，吸附到同一個路口：零長度路線，沒有經過任何路段
    route = router.route(grid_point(1, 3), grid_point(2, 3))
    assert len(set(route.points)) == 1
    assert router.edges(route.points) == []


def test_trace_far_from_roads_raises(router: Router) -> None:
    with pytest.raises(RoutingError):
        router.trace(densify((121.0, 24.0), (121.001, 24.0)))


def parallel_trace_offset_north(offset_m: float) -> list[tuple[float, float]]:
    """沿 row 1（雙向住宅街）從 col 1.5 到 col 4.5，整條往北平移 offset_m。

    row 1 以北 120 m 內沒有其他路（往北最近的是 row 3 的快速道路，約 444 m），所以能吸附的只有 row 1。
    """
    (x1, y1), (x2, y2) = grid_point(1.5, 1), grid_point(4.5, 1)
    dlat = offset_m / 111_195  # 緯度 1 度約 111.195 km
    return [(x1 + (x2 - x1) * i / 24, y1 + dlat) for i in range(25)]


def test_trace_accepts_search_radius_150m_for_trace_120m_off_road(router: Router) -> None:
    # 軌跡離路 120 m：只有 meili.default.max_search_radius 也調到 150，search_radius=150 才真的生效
    result = router.trace(parallel_trace_offset_north(120), search_radius_m=150)
    assert result.length_km == pytest.approx(0.60, abs=0.05)


def test_trace_with_search_radius_100m_fails_for_trace_120m_off_road(router: Router) -> None:
    with pytest.raises(RoutingError):
        router.trace(parallel_trace_offset_north(120), search_radius_m=100)
