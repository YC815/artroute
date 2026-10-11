"""bench 的安全斷言查逆向：Router.edges 取 Valhalla 的 edge.traversability（相對 OSM way 方向，自行車能騎的方向）
與 edge.forward（這次是不是順著 way 的方向走），兩者不合就是逆向（wrong_way）。

合成地圖 ONEWAY_MAP（planner_build）：4002 是 oneway=yes 畫成往西、4003 是 oneway=-1 畫成往東（兩條都只能往西），
4004 是 oneway=yes＋oneway:bicycle=no、4005 是 oneway=yes＋cycleway=opposite_lane（自行車雙向），4008 是 40 m 的單行道。
逆向的折線用 edge_walk 會接受的頂點（Valhalla 的 edge_walk 本身不檢查通行方向，正好拿來造逆向的路線）。
"""

from pathlib import Path

import pytest

from engine.bench.runner import UNDETERMINED, UNSAFE, safety_problem
from engine.matching.types import Segment
from engine.routing.router import EdgeInfo, Router, _wrong_way
from osm_fixture import ONEWAY_X, meter_point


def along(*xs: float, y: float = 0.0) -> list[tuple[float, float]]:
    return [meter_point(x + ONEWAY_X, y) for x in xs]


def segment(points) -> list[Segment]:
    return [Segment("stroke", 0, points, 0.0)]


@pytest.mark.parametrize("traversability,forward,expected", [
    ("both", True, False), ("both", False, False),
    ("forward", True, False), ("forward", False, True),
    ("backward", False, False), ("backward", True, True),
    ("none", True, True),
    (None, True, None), ("forward", None, None), ("sideways", True, None),
])
def test_wrong_way_from_valhalla_attributes(traversability, forward, expected) -> None:
    edge = {k: v for k, v in (("traversability", traversability), ("forward", forward)) if v is not None}
    assert _wrong_way(edge) is expected


class Edges:
    def __init__(self, edges: list[EdgeInfo]) -> None:
        self.result = edges

    def edges(self, points):
        return self.result


def test_unknown_direction_is_undetermined_and_wrong_way_is_unsafe() -> None:
    points = [(121.5, 25.05), (121.51, 25.05)]
    assert safety_problem(Edges([EdgeInfo(1, 0.0, wrong_way=None)]), segment(points), frozenset())[0] == UNDETERMINED
    problem = safety_problem(Edges([EdgeInfo(1, 0.0), EdgeInfo(7, 0.0, wrong_way=True)]), segment(points), frozenset())
    assert problem is not None and problem[0] == UNSAFE and "7" in problem[1]
    assert safety_problem(Edges([EdgeInfo(1, 0.0)]), segment(points), frozenset()) is None


@pytest.fixture(scope="module")
def router(planner_build: Path) -> Router:
    return Router.from_build(planner_build)


@pytest.mark.valhalla
def test_riding_one_ways_backwards_is_caught(router: Router) -> None:
    eastward = along(100.0, 200.0, 400.0, 600.0, 700.0)  # 中間 4002、4003 逆向
    assert [(e.way_id, e.wrong_way) for e in router.edges(eastward)] == [
        (4001, False), (4002, True), (4003, True), (4004, False)]
    problem = safety_problem(router, segment(eastward), frozenset())
    assert problem is not None and problem[0] == UNSAFE and "4002" in problem[1] and "4003" in problem[1]
    short = along(1070.0, 1080.0, 1120.0, 1130.0)  # 40 m 的單行道逆向
    assert safety_problem(router, segment(short), frozenset())[0] == UNSAFE


@pytest.mark.valhalla
def test_riding_one_ways_the_right_way_is_not_flagged(router: Router) -> None:
    westward = along(700.0, 600.0, 400.0, 200.0, 100.0)
    assert all(e.wrong_way is False for e in router.edges(westward))
    assert safety_problem(router, segment(westward), frozenset()) is None
    assert safety_problem(router, segment(along(1130.0, 1120.0, 1080.0, 1070.0)), frozenset()) is None


@pytest.mark.valhalla
def test_bicycle_contraflow_streets_are_not_flagged(router: Router) -> None:
    """oneway:bicycle=no 與 cycleway=opposite_lane：自行車往東（逆著汽車）是合法的，Valhalla 也這樣走。"""
    east = router.route(meter_point(610.0 + ONEWAY_X, 0.0), meter_point(990.0 + ONEWAY_X, 0.0), shortest=True)
    edges = router.edges(east.points)
    assert {e.way_id for e in edges} == {4004, 4005} and all(e.wrong_way is False for e in edges)
    assert safety_problem(router, [Segment("stroke", 0, east.points, east.length_km * 1000)], frozenset()) is None
