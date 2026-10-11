"""P0 核心：bicycle 必須遵守單行道、允許 oneway:bicycle=no 的逆向、避開快速道路。"""

from pathlib import Path

import pytest

from engine.routing.router import Router, RoutingError
from engine.routing.tiles import build_tiles
from grid_helpers import densify, rides_westward_on_row
from osm_fixture import (
    BOTTOM_ONEWAY_WAY_ID,
    CONTRAFLOW_WAY_ID,
    MOTORWAY_WAY_ID,
    SHORTEST_MAP,
    X2,
    grid_point,
    write_grid_pbf,
)

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


def test_trace_with_turn_penalty_factor_matches_and_still_respects_oneway(grid_config: Path) -> None:
    # Valhalla 3.9.1 接受 trace_options.turn_penalty_factor（meili 的 customizable 清單裡有），帶了照樣吸附得到；
    # 單行道仍由 Valhalla 擋下：轉彎懲罰只改變偏好，不改變通行規則
    router = Router(grid_config, turn_penalty_factor=500.0)
    l_shape = densify(grid_point(0, 1), grid_point(2, 1)) + densify(grid_point(2, 1), grid_point(2, 0))[1:]
    result = router.trace(l_shape)
    assert result.length_km == pytest.approx(0.62, abs=0.05)
    assert result.points[-1] == pytest.approx(grid_point(2, 0), abs=1e-4)
    with pytest.raises(RoutingError):
        router.trace(densify(grid_point(2, 0), grid_point(0, 0)))


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


def test_shortest_route_still_detours_around_a_oneway(router: Router) -> None:
    result = router.route(grid_point(2, 0), grid_point(0, 0), shortest=True)
    assert not rides_westward_on_row(result.points, row=0, col_min=0, col_max=2)
    assert result.length_km > 0.8


def test_shortest_route_still_avoids_motorway(router: Router) -> None:
    # shortest 只換成本函數：bicycle 不能上的快速道路照樣不能走
    result = router.route(grid_point(0, 3), grid_point(6, 3), shortest=True)
    assert not touches_motorway_interior(result.points)
    assert result.length_km > 1.6


def test_shortest_route_still_rides_contraflow(router: Router) -> None:
    result = router.route(grid_point(6, 0), grid_point(4, 0), shortest=True)
    assert result.length_km == pytest.approx(0.40, abs=0.05)


def test_shortest_route_never_rides_a_denied_way(router: Router) -> None:
    for start, end in ((grid_point(0, 3), grid_point(6, 3)), (grid_point(6, 3), grid_point(0, 3))):
        edges = router.edges(router.route(start, end, shortest=True).points)
        assert all(e.way_id != MOTORWAY_WAY_ID for e in edges)


def test_shortest_really_changes_the_valhalla_route(tmp_path: Path) -> None:
    # Valhalla 會悄悄忽略不認得的 costing 選項：鍵名、巢狀層級錯了，或升級 pyvalhalla 之後選項改名，
    # 其他 shortest 測試照樣全綠。這裡用「短的 primary vs 長的 cycleway」證明 shortest 真的改變了結果
    pbf = tmp_path / "shortest.osm.pbf"
    write_grid_pbf(pbf, SHORTEST_MAP)
    router = Router(build_tiles(pbf, tmp_path / "build"))
    start, end = grid_point(X2, 1), grid_point(X2 + 6, 1)

    default = router.route(start, end)
    shortest = router.route(start, end, shortest=True)
    assert default.length_km > 1.8  # 前提：預設成本確實偏好自行車道繞路
    assert shortest.length_km == pytest.approx(1.2, abs=0.1)
    assert shortest.length_km < default.length_km
