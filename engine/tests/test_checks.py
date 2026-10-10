from pathlib import Path

import pytest

from engine.data.bike_patch import DeniedWay
from engine.data.build import build_with_patch
from engine.data.checks import ZHOUMEI_EAST, ZHOUMEI_ONEWAY_WAY_ID, ZHOUMEI_WEST, taiwan_checks, validate_build
from engine.routing.router import EdgeInfo, RouteResult, RoutingError
from osm_fixture import EXPRESSWAY_MAP, write_grid_pbf

EXPRESSWAY = DeniedWay(900, "洲美快速道路", "", (121.50, 25.10), (121.52, 25.11))
OTHER_DENIED = DeniedWay(901, "八里新店線", "台64", (121.40, 25.05), (121.45, 25.05))


class FakeRouter:
    def __init__(self, routes: dict, edges: dict, fail: bool = False) -> None:
        self._routes, self._edges, self._fail = routes, edges, fail

    def route(self, start, end) -> RouteResult:
        if self._fail:
            raise RoutingError("boom")
        return self._routes[(start, end)]

    def edges(self, points) -> list[EdgeInfo]:
        return self._edges[tuple(points)]


def make_router(*, backward_edges=None, backward_km=0.5, expressway_edges=None, fail=False) -> FakeRouter:
    forward = RouteResult([ZHOUMEI_WEST, ZHOUMEI_EAST], 0.4)
    backward = RouteResult([ZHOUMEI_EAST, (121.5, 25.102), ZHOUMEI_WEST], backward_km)
    along = RouteResult([EXPRESSWAY.first, (121.51, 25.09), EXPRESSWAY.last], 3.0)
    return FakeRouter(
        routes={(ZHOUMEI_WEST, ZHOUMEI_EAST): forward, (ZHOUMEI_EAST, ZHOUMEI_WEST): backward,
                (EXPRESSWAY.first, EXPRESSWAY.last): along},
        edges={tuple(forward.points): [EdgeInfo(ZHOUMEI_ONEWAY_WAY_ID, 90.0)],
               tuple(backward.points): backward_edges or [EdgeInfo(1, 0.0), EdgeInfo(2, 270.0)],
               tuple(along.points): expressway_edges or [EdgeInfo(5, 10.0)]},
        fail=fail,
    )


DENIED = [EXPRESSWAY, OTHER_DENIED]


def test_all_checks_pass() -> None:
    assert taiwan_checks(make_router(), DENIED) == []


def test_backward_riding_oneway_westward_fails() -> None:
    router = make_router(backward_edges=[EdgeInfo(ZHOUMEI_ONEWAY_WAY_ID, 270.0)])
    assert any("逆向" in f for f in taiwan_checks(router, DENIED))


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
    assert any("洲美街單行段檢查失敗" in f for f in failures)
    assert any("找不到洲美快速道路" in f for f in failures)
