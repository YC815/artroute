from dataclasses import replace
from pathlib import Path

import pytest

from engine.data.bike_patch import DeniedWay
from engine.data.build import build_with_patch, load_denied
from engine.data.checks import (
    STEPS_PROBE_M,
    STEPS_SAMPLE,
    ZHOUMEI_EAST,
    ZHOUMEI_ONEWAY_WAY_ID,
    ZHOUMEI_WEST,
    ShortestRoutes,
    _expressway_checks,
    steps_checks,
    taiwan_checks,
    validate_build,
)
from engine.routing.geo import haversine_m
from engine.routing.router import EdgeInfo, RouteResult, Router, RoutingError
from engine.routing.tiles import build_tiles
from osm_fixture import (
    EXPRESSWAY_MAP,
    EXPRESSWAY_WAY_ID,
    STEPS_MAP,
    STEPS_WAY_ID,
    X0,
    GridWay,
    grid_point,
    write_grid_pbf,
)

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
    # 預設設定的失敗一定要是沒有前綴的那份：只剩 shortest 的結果代表預設設定的驗證被丟掉了
    assert any("沒有走洲美街單行段" in f and not f.startswith("（shortest）") for f in failures)
    assert any("找不到洲美快速道路" in f and not f.startswith("（shortest）") for f in failures)
    # 吸附用的是 shortest 的 route：兩種設定都要驗
    assert any(f.startswith("（shortest）") and "沒有走洲美街單行段" in f for f in failures)


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


@pytest.mark.valhalla
def test_expressway_check_with_shortest_routes_on_real_tiles(tmp_path: Path) -> None:
    ways = [replace(w, tags={**w.tags, "name": "洲美快速道路"}) if w.id == EXPRESSWAY_WAY_ID else w for w in EXPRESSWAY_MAP]
    src = tmp_path / "src.osm.pbf"
    write_grid_pbf(src, ways)
    build_dir = tmp_path / "build"
    build_dir.mkdir()
    build_with_patch(src, build_dir)
    denied = load_denied(build_dir)
    assert _expressway_checks(ShortestRoutes(Router.from_build(build_dir)), denied) == []

    # 對照組：不修補時最短路徑一定走快速道路，檢查必須抓到
    unpatched = Router(build_tiles(src, tmp_path / "unpatched"))
    failures = _expressway_checks(ShortestRoutes(unpatched), denied)
    assert failures and all(f"[{EXPRESSWAY_WAY_ID}]" in f for f in failures)


# ---- 樓梯檢查：被標記的樓梯至少一條；抽樣的樓梯兩端之間的自行車路線不得經過任何被禁行的 way ----

STEPS_A = DeniedWay(950, "", "", (121.55, 25.03), (121.551, 25.03), "steps")
STEPS_B = DeniedWay(951, "", "", (121.56, 25.04), (121.561, 25.04), "steps")


def probe_ends(way: DeniedWay) -> tuple:
    """樓梯檢查實際查的起訖：兩端各往外延伸 STEPS_PROBE_M。"""
    first, last = way.first, way.last
    dx, dy = last[0] - first[0], last[1] - first[1]
    t = STEPS_PROBE_M / haversine_m(first, last)
    return (first[0] - dx * t, first[1] - dy * t), (last[0] + dx * t, last[1] + dy * t)


def steps_router(*steps: DeniedWay, raises: frozenset = frozenset()) -> FakeRouter:
    router = FakeRouter(routes={}, edges={}, raises=raises)
    for way in steps:
        start, end = probe_ends(way)
        router.add_route(start, end, [EdgeInfo(7, 90.0)])
        router.add_route(end, start, [EdgeInfo(8, 270.0)])
    return router


def test_steps_probe_ends_lie_beyond_both_ends_of_the_steps() -> None:
    start, end = probe_ends(STEPS_A)
    assert haversine_m(start, STEPS_A.first) == pytest.approx(STEPS_PROBE_M, rel=1e-3)
    assert haversine_m(end, STEPS_A.last) == pytest.approx(STEPS_PROBE_M, rel=1e-3)
    assert haversine_m(start, end) == pytest.approx(haversine_m(STEPS_A.first, STEPS_A.last) + 2 * STEPS_PROBE_M, rel=1e-3)


def test_steps_check_passes_when_routes_avoid_marked_steps() -> None:
    assert steps_checks(steps_router(STEPS_A, STEPS_B), [EXPRESSWAY, STEPS_A, STEPS_B]) == []


def test_steps_check_fails_when_no_steps_are_marked() -> None:
    failures = steps_checks(steps_router(), [EXPRESSWAY, OTHER_DENIED])
    assert len(failures) == 1 and "沒有任何樓梯被標記" in failures[0]


def test_steps_check_fails_when_route_uses_a_marked_steps_way() -> None:
    router = steps_router(STEPS_A, STEPS_B)
    start, end = probe_ends(STEPS_B)
    router.add_route(end, start, [EdgeInfo(8, 270.0), EdgeInfo(STEPS_A.id, 270.0)])
    failures = steps_checks(router, [STEPS_A, STEPS_B])
    assert len(failures) == 1
    assert "樓梯 way 951 逆向" in failures[0] and "使用了被禁行的路段 way [950]" in failures[0]


def test_steps_check_also_catches_marked_expressway() -> None:
    router = steps_router(STEPS_A)
    router.add_route(*probe_ends(STEPS_A), [EdgeInfo(EXPRESSWAY.id, 90.0)])
    assert any("[900]" in f for f in steps_checks(router, [EXPRESSWAY, STEPS_A]))


def test_steps_check_needs_at_least_one_route() -> None:
    router = FakeRouter(routes={}, edges={}, fail=True)
    failures = steps_checks(router, [STEPS_A])
    assert len(failures) == 1 and "樓梯檢查失敗" in failures[0]


def test_steps_check_fails_when_fewer_than_half_of_the_routes_succeed() -> None:
    """真實資料上很多樓梯夾在人行道網裡，外延的起訖吸附到別處或無路：只靠一兩條成功的路線不算證據。"""
    a_start, a_end = probe_ends(STEPS_A)
    b_start, b_end = probe_ends(STEPS_B)
    router = steps_router(STEPS_A, STEPS_B, raises=frozenset({(a_end, a_start), (b_start, b_end), (b_end, b_start)}))
    failures = steps_checks(router, [STEPS_A, STEPS_B])
    assert len(failures) == 1
    assert "樓梯檢查失敗" in failures[0] and "1/4" in failures[0] and "boom" in failures[0]


def test_steps_check_passes_when_half_of_the_routes_succeed() -> None:
    a_start, a_end = probe_ends(STEPS_A)
    b_start, b_end = probe_ends(STEPS_B)
    router = steps_router(STEPS_A, STEPS_B, raises=frozenset({(a_end, a_start), (b_end, b_start)}))
    assert steps_checks(router, [STEPS_A, STEPS_B]) == []


def test_steps_check_reports_both_violation_and_too_few_routes() -> None:
    a_start, a_end = probe_ends(STEPS_A)
    b_start, b_end = probe_ends(STEPS_B)
    router = steps_router(STEPS_A, STEPS_B, raises=frozenset({(a_end, a_start), (b_start, b_end), (b_end, b_start)}))
    router.add_route(a_start, a_end, [EdgeInfo(STEPS_B.id, 90.0)])
    failures = steps_checks(router, [STEPS_A, STEPS_B])
    assert any("使用了被禁行的路段 way [951]" in f for f in failures)
    assert any("1/4" in f for f in failures)


def test_steps_without_coordinates_fail_the_check() -> None:
    failures = steps_checks(steps_router(), [replace(STEPS_A, first=None), replace(STEPS_B, last=None)])
    assert len(failures) == 1 and "座標" in failures[0]


def test_steps_check_samples_evenly_by_way_id() -> None:
    """全台上萬條樓梯每條都查太慢：依 way id 排序後等距抽 STEPS_SAMPLE 條（兩個方向）。"""
    steps = [DeniedWay(10_000 + i, "", "", (121.0 + i * 1e-3, 25.0), (121.0 + i * 1e-3, 25.001), "steps")
             for i in range(5 * STEPS_SAMPLE)]
    router = steps_router(*steps)
    calls: list = []
    route = router.route
    router.route = lambda start, end: calls.append((start, end)) or route(start, end)
    assert steps_checks(router, list(reversed(steps))) == []
    checked = sorted({d.id for d in steps if probe_ends(d) in calls})
    assert len(calls) == 2 * STEPS_SAMPLE
    assert checked == [10_000 + 5 * i for i in range(STEPS_SAMPLE)]


def test_expressway_check_does_not_count_steps_as_expressways() -> None:
    failures = taiwan_checks(make_router(), [STEPS_A])
    assert any("沒有任何快速道路" in f for f in failures)


@pytest.mark.valhalla
def test_steps_check_on_real_tiles_passes_when_patched_and_fails_when_not(tmp_path: Path) -> None:
    src = tmp_path / "src.osm.pbf"
    write_grid_pbf(src, STEPS_MAP)
    build_dir = tmp_path / "build"
    build_dir.mkdir()
    build_with_patch(src, build_dir)
    denied = load_denied(build_dir)
    assert [(d.id, d.highway) for d in denied] == [(STEPS_WAY_ID, "steps")]

    patched = Router.from_build(build_dir)
    assert steps_checks(patched, denied) == []
    assert steps_checks(ShortestRoutes(patched), denied) == []

    # 對照組：不修補時最短路徑走樓梯，檢查必須抓到（預設成本本來就繞開樓梯，抓不到不代表檢查壞了）
    unpatched = Router(build_tiles(src, tmp_path / "unpatched"))
    failures = steps_checks(ShortestRoutes(unpatched), denied)
    assert failures and all(f"[{STEPS_WAY_ID}]" in f for f in failures)


@pytest.mark.valhalla
def test_validate_build_runs_steps_check_with_both_route_settings(tmp_path: Path) -> None:
    src = tmp_path / "src.osm.pbf"
    write_grid_pbf(src, EXPRESSWAY_MAP)  # 沒有樓梯
    build_dir = tmp_path / "build"
    build_dir.mkdir()
    build_with_patch(src, build_dir)
    failures = validate_build(build_dir)
    assert any("沒有任何樓梯被標記" in f and not f.startswith("（shortest）") for f in failures)
    assert any("沒有任何樓梯被標記" in f and f.startswith("（shortest）") for f in failures)

    steps_src = tmp_path / "steps.osm.pbf"
    write_grid_pbf(steps_src, STEPS_MAP)
    steps_dir = tmp_path / "steps"
    steps_dir.mkdir()
    build_with_patch(steps_src, steps_dir)
    assert not any("樓梯" in f for f in validate_build(steps_dir))
