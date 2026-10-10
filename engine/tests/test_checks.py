from dataclasses import replace
from pathlib import Path

import pytest

from engine.data.bike_patch import DeniedWay
from engine.data.build import build_with_patch, load_denied
from engine.data.checks import (
    ZHOUMEI_EAST,
    ZHOUMEI_ONEWAY_WAY_ID,
    ZHOUMEI_WEST,
    _expressway_checks,
    taiwan_checks,
    validate_build,
)
from engine.routing.router import EdgeInfo, RouteResult, Router, RoutingError
from engine.routing.tiles import build_tiles
from osm_fixture import EXPRESSWAY_MAP, EXPRESSWAY_WAY_ID, X0, GridWay, grid_point, write_grid_pbf

EXPRESSWAY = DeniedWay(900, "洲美快速道路", "", (121.50, 25.10), (121.52, 25.11))
OTHER_DENIED = DeniedWay(901, "八里新店線", "台64", (121.40, 25.05), (121.45, 25.05))


class FakeRouter:
    def __init__(self, routes: dict, edges: dict, fail: bool = False, raises: frozenset = frozenset()) -> None:
        self._routes, self._edges, self._fail, self._raises = routes, edges, fail, raises

    def route(self, start, end) -> RouteResult:
        if self._fail or (start, end) in self._raises:
            raise RoutingError("boom")
        return self._routes[(start, end)]

    def edges(self, points) -> list[EdgeInfo]:
        edges = self._edges[tuple(points)]
        if isinstance(edges, RoutingError):
            raise edges
        return edges

    def add_route(self, start, end, edges: list[EdgeInfo] | RoutingError) -> None:
        result = RouteResult([start, ((start[0] + end[0]) / 2, (start[1] + end[1]) / 2), end], 3.0)
        self._routes[(start, end)] = result
        self._edges[tuple(result.points)] = edges


def make_router(*, backward_edges=None, backward_km=0.5, expressway_edges=None, reverse_edges=None,
                fail=False, raises=frozenset()) -> FakeRouter:
    forward = RouteResult([ZHOUMEI_WEST, ZHOUMEI_EAST], 0.4)
    backward = RouteResult([ZHOUMEI_EAST, (121.5, 25.102), ZHOUMEI_WEST], backward_km)
    router = FakeRouter(
        routes={(ZHOUMEI_WEST, ZHOUMEI_EAST): forward, (ZHOUMEI_EAST, ZHOUMEI_WEST): backward},
        edges={tuple(forward.points): [EdgeInfo(ZHOUMEI_ONEWAY_WAY_ID, 90.0)],
               tuple(backward.points): backward_edges or [EdgeInfo(1, 0.0), EdgeInfo(2, 270.0)]},
        fail=fail,
        raises=raises,
    )
    router.add_route(EXPRESSWAY.first, EXPRESSWAY.last, expressway_edges or [EdgeInfo(5, 10.0)])
    router.add_route(EXPRESSWAY.last, EXPRESSWAY.first, reverse_edges or [EdgeInfo(5, 190.0)])
    return router


DENIED = [EXPRESSWAY, OTHER_DENIED]


def test_all_checks_pass() -> None:
    assert taiwan_checks(make_router(), DENIED) == []


def test_backward_riding_oneway_westward_fails() -> None:
    router = make_router(backward_edges=[EdgeInfo(ZHOUMEI_ONEWAY_WAY_ID, 270.0)])
    assert any("逆向騎上" in f for f in taiwan_checks(router, DENIED))


def test_backward_not_longer_than_forward_fails() -> None:
    assert any("沒有比順向長" in f for f in taiwan_checks(make_router(backward_km=0.4), DENIED))


def test_no_denied_ways_fails() -> None:
    assert any("沒有任何快速道路" in f for f in taiwan_checks(make_router(), []))


def test_missing_zhoumei_expressway_fails() -> None:
    assert any("找不到洲美快速道路" in f for f in taiwan_checks(make_router(), [OTHER_DENIED]))


def test_route_using_denied_way_fails() -> None:
    router = make_router(expressway_edges=[EdgeInfo(901, 0.0)])
    assert any("使用了被禁行的快速道路" in f for f in taiwan_checks(router, DENIED))


def test_routing_error_becomes_named_failure() -> None:
    failures = taiwan_checks(make_router(fail=True), DENIED)
    assert any("洲美街單行段檢查" in f for f in failures)
    assert any("洲美快速道路檢查" in f for f in failures)


# ---- 洲美快速道路檢查：名稱變體、兩個方向、多條 way ----


def test_suffixed_zhoumei_name_is_still_found() -> None:
    suffixed = replace(EXPRESSWAY, name="洲美快速道路（北上）")
    assert taiwan_checks(make_router(), [suffixed, OTHER_DENIED]) == []


def test_reverse_direction_using_denied_way_fails() -> None:
    """oneway=-1 這類只在逆向才會走上快速道路的情形，只檢查 first->last 會誤判通過。"""
    router = make_router(reverse_edges=[EdgeInfo(901, 0.0)])
    failures = taiwan_checks(router, DENIED)
    assert any("逆向" in f and "使用了被禁行的快速道路" in f and "901" in f for f in failures)


def test_forward_failure_message_names_direction_and_way() -> None:
    failures = taiwan_checks(make_router(expressway_edges=[EdgeInfo(901, 0.0)]), DENIED)
    assert any("順向" in f and "way 900" in f for f in failures)


def test_every_zhoumei_way_is_checked() -> None:
    second = DeniedWay(902, "洲美快速道路", "", (121.60, 25.20), (121.62, 25.21))
    router = make_router()
    router.add_route(second.first, second.last, [EdgeInfo(5, 10.0)])
    router.add_route(second.last, second.first, [EdgeInfo(902, 0.0)])  # 第二條的逆向走回被禁行的路
    failures = taiwan_checks(router, [EXPRESSWAY, second, OTHER_DENIED])
    assert any("way 902" in f and "逆向" in f for f in failures)
    router.add_route(second.last, second.first, [EdgeInfo(5, 190.0)])
    assert taiwan_checks(router, [EXPRESSWAY, second, OTHER_DENIED]) == []


def test_one_direction_routing_error_is_fine_when_the_other_succeeds() -> None:
    router = make_router(raises=frozenset({(EXPRESSWAY.last, EXPRESSWAY.first)}))
    assert taiwan_checks(router, DENIED) == []


def test_one_direction_routing_error_does_not_hide_denied_way_in_the_other() -> None:
    router = make_router(expressway_edges=[EdgeInfo(901, 0.0)], raises=frozenset({(EXPRESSWAY.last, EXPRESSWAY.first)}))
    assert any("使用了被禁行的快速道路" in f for f in taiwan_checks(router, DENIED))


def test_zero_length_route_in_one_direction_is_fine_when_the_other_proves_avoidance() -> None:
    router = make_router()
    router.add_route(EXPRESSWAY.last, EXPRESSWAY.first, [])  # 起訖吸附到同一點：沒有經過任何路段
    assert taiwan_checks(router, DENIED) == []


def test_zero_length_route_does_not_hide_denied_way_in_the_other_direction() -> None:
    router = make_router(expressway_edges=[EdgeInfo(901, 0.0)])
    router.add_route(EXPRESSWAY.last, EXPRESSWAY.first, [])
    assert any("使用了被禁行的快速道路" in f for f in taiwan_checks(router, DENIED))


def test_only_zero_length_routes_prove_nothing() -> None:
    """零長度路線沒有經過任何路段，不能算成「路線避開了快速道路」的證據。"""
    router = make_router()
    router.add_route(EXPRESSWAY.first, EXPRESSWAY.last, [])
    router.add_route(EXPRESSWAY.last, EXPRESSWAY.first, [])
    failures = taiwan_checks(router, DENIED)
    assert len(failures) == 1 and "洲美快速道路檢查失敗" in failures[0] and "長度為 0" in failures[0]


def test_unknown_edges_fail_the_check_without_hiding_other_directions() -> None:
    """Valhalla 的 edge_walk 對某些路線配不出路段：沒辦法證明這條路線避開了快速道路就要失敗，但其他路線照樣要查完。"""
    router = make_router(expressway_edges=[EdgeInfo(901, 0.0)])
    router.add_route(EXPRESSWAY.last, EXPRESSWAY.first, RoutingError("edge_walk boom"))
    failures = taiwan_checks(router, DENIED)
    assert any("順向" in f and "使用了被禁行的快速道路" in f and "901" in f for f in failures)
    assert any("way 900 逆向" in f and "無法判斷" in f and "edge_walk boom" in f for f in failures)


def test_unknown_edges_alone_fail_the_check() -> None:
    router = make_router()
    router.add_route(EXPRESSWAY.last, EXPRESSWAY.first, RoutingError("edge_walk boom"))
    failures = taiwan_checks(router, DENIED)
    assert len(failures) == 1 and "way 900 逆向" in failures[0] and "無法判斷" in failures[0]


def test_unknown_edges_are_reported_even_when_no_route_proves_avoidance() -> None:
    router = make_router(raises=frozenset({(EXPRESSWAY.first, EXPRESSWAY.last)}))
    router.add_route(EXPRESSWAY.last, EXPRESSWAY.first, RoutingError("edge_walk boom"))
    failures = taiwan_checks(router, DENIED)
    assert len(failures) == 1 and "way 900 逆向" in failures[0] and "無法判斷" in failures[0]


def test_every_route_failing_is_a_named_failure() -> None:
    router = make_router(raises=frozenset({(EXPRESSWAY.first, EXPRESSWAY.last), (EXPRESSWAY.last, EXPRESSWAY.first)}))
    failures = taiwan_checks(router, DENIED)
    assert len(failures) == 1 and "洲美快速道路檢查失敗" in failures[0]


# ---- 端點座標是 None（建圖時端點節點不在 pbf 裡）----


@pytest.mark.parametrize(
    "first, last",
    [(None, (121.52, 25.11)), ((121.50, 25.10), None), (None, None)],
    ids=["first-none", "last-none", "both-none"],
)
def test_zhoumei_expressway_without_coordinates_is_named_failure(first, last) -> None:
    no_coordinates = DeniedWay(900, "洲美快速道路", "", first, last)
    failures = taiwan_checks(make_router(), [no_coordinates, OTHER_DENIED])  # make_router 沒有這組座標的路線：呼叫 route 就會 KeyError
    assert len(failures) == 1
    assert "洲美快速道路" in failures[0] and "座標" in failures[0]


def test_zhoumei_check_uses_first_way_that_has_both_coordinates() -> None:
    broken = DeniedWay(899, "洲美快速道路", "", None, None)
    assert taiwan_checks(make_router(), [broken, EXPRESSWAY, OTHER_DENIED]) == []


@pytest.mark.valhalla
def test_validate_build_loads_router_and_denied_from_build_dir(tmp_path: Path) -> None:
    """玩具路網沒有洲美街也沒有洲美快速道路：validate_build 要把這些都回報成 failure，而不是丟例外。"""
    src = tmp_path / "src.osm.pbf"
    write_grid_pbf(src, EXPRESSWAY_MAP)
    build_dir = tmp_path / "build"
    build_dir.mkdir()
    build_with_patch(src, build_dir)
    failures = validate_build(build_dir)
    # 洲美街的兩個端點都吸附到玩具路網的同一點，是零長度路線，不會走到單行段
    assert any("沒有走洲美街單行段" in f for f in failures)
    assert any("找不到洲美快速道路" in f for f in failures)


@pytest.mark.valhalla
def test_expressway_check_on_real_tiles_passes_when_patched_and_fails_when_not(tmp_path: Path) -> None:
    ways = [replace(w, tags={**w.tags, "name": "洲美快速道路"}) if w.id == EXPRESSWAY_WAY_ID else w for w in EXPRESSWAY_MAP]
    src = tmp_path / "src.osm.pbf"
    write_grid_pbf(src, ways)
    build_dir = tmp_path / "build"
    build_dir.mkdir()
    build_with_patch(src, build_dir)
    denied = load_denied(build_dir)
    assert [d.id for d in denied] == [EXPRESSWAY_WAY_ID]
    assert (denied[0].first, denied[0].last) == (pytest.approx(grid_point(X0, 1)), pytest.approx(grid_point(X0 + 4, 1)))

    assert _expressway_checks(Router.from_build(build_dir), denied) == []

    # 對照組：同一張圖不修補，路線會直接走快速道路，檢查必須抓到
    unpatched = Router(build_tiles(src, tmp_path / "unpatched"))
    failures = _expressway_checks(unpatched, denied)
    assert failures and all(f"[{EXPRESSWAY_WAY_ID}]" in f for f in failures)


@pytest.mark.valhalla
def test_expressway_check_on_real_tiles_survives_link_whose_ends_snap_to_one_junction(tmp_path: Path) -> None:
    """台灣路網的 way 334841526：短連絡道兩端吸附到同一個路口，route 是零長度，edge_walk 會失敗。"""
    named = [replace(w, tags={**w.tags, "name": "洲美快速道路"}) if w.id == EXPRESSWAY_WAY_ID else w for w in EXPRESSWAY_MAP]
    link = GridWay(220, [(X0 - 1, 2), (X0, 1)], {"highway": "trunk", "name": "洲美快速道路"})
    src = tmp_path / "src.osm.pbf"
    write_grid_pbf(src, [*named, link])
    build_dir = tmp_path / "build"
    build_dir.mkdir()
    build_with_patch(src, build_dir)
    denied = load_denied(build_dir)
    assert [d.id for d in denied] == [EXPRESSWAY_WAY_ID, link.id]

    patched = Router.from_build(build_dir)
    zero_length = patched.route(grid_point(X0 - 1, 2), grid_point(X0, 1))
    assert len(set(zero_length.points)) == 1  # 前提：真的重現了零長度路線
    assert _expressway_checks(patched, denied) == []

    unpatched = Router(build_tiles(src, tmp_path / "unpatched"))
    failures = _expressway_checks(unpatched, denied)
    assert any(f"[{EXPRESSWAY_WAY_ID}]" in f for f in failures)
    assert any(f"[{link.id}]" in f for f in failures)
