"""bench 安全斷言的「合法重現」（bench.replay）：edge_walk 在兩條 way 共用同一段幾何時可能配錯 way，
被判禁行或逆向的 edge 用遵守通行權的 router.route 重現同一段幾何，重現得出來才豁免，其餘一律算不安全。

單元測試用假 router（座標是東西向的公尺，meter_point）；真實 Valhalla 測試用合成地圖：
ONEWAY_MAP（真的逆向騎單行道必須仍然抓得到）與 OVERLAP_MAP（重疊的 footway 與 cycleway，重現真實路網的誤報）。
"""

from collections.abc import Callable
from pathlib import Path

import pytest

from engine.bench.replay import legal_replay, same_geometry
from engine.bench.runner import UNDETERMINED, UNSAFE, safety_problem
from engine.matching.types import Segment
from engine.routing.router import EdgeInfo, RouteResult, Router, RoutingError, _align_shape, _edge_span
from osm_fixture import ONEWAY_X, OVERLAP_ROW_M, OVERLAP_X, meter_point, overlap_shape

DENIED = frozenset({9})


def line(*xy: tuple[float, float]) -> list[tuple[float, float]]:
    return [meter_point(x, y) for x, y in xy]


def straight(x0: float, x1: float, step: float, y: float = 0.0) -> list[tuple[float, float]]:
    count = round((x1 - x0) / step)
    return [meter_point(x0 + (x1 - x0) * i / count, y) for i in range(count + 1)]


POINTS = straight(0.0, 100.0, 20.0)  # 6 個點：0、20、40、60、80、100 m
CLEAN = EdgeInfo(1, 0.0, begin_shape_index=0, end_shape_index=2)


def flagged(kind: str, begin: int | None = 2, end: int | None = 3) -> EdgeInfo:
    """被 edge_walk 判成逆向（wrong）或禁行（denied）的那條 edge，範圍預設是 40–60 m。"""
    if kind == "wrong":
        return EdgeInfo(5, 0.0, wrong_way=True, begin_shape_index=begin, end_shape_index=end)
    return EdgeInfo(9, 0.0, begin_shape_index=begin, end_shape_index=end)


class ReplayRouter:
    """edges()：整條折線回傳 segment_edges，route 的結果回傳 route_edges（預設是乾淨的 edge）；
    route()：呼叫 route_for(起點, 終點)，回傳 list 就是路線的點，回傳例外就丟出去。"""

    def __init__(
        self,
        segment_edges: list[EdgeInfo],
        route_for: Callable[[tuple, tuple], list | Exception],
        route_edges: list[EdgeInfo] | Exception | None = None,
    ) -> None:
        self.segment_edges, self.route_for = segment_edges, route_for
        self.route_edges = [EdgeInfo(77, 0.0)] if route_edges is None else route_edges
        self.route_calls: list[tuple] = []
        self.routes: list[list] = []

    def edges(self, points):
        if list(points) in self.routes:
            if isinstance(self.route_edges, Exception):
                raise self.route_edges
            return self.route_edges
        return self.segment_edges

    def route(self, start, end, *, shortest=False):
        self.route_calls.append((start, end))
        result = self.route_for(start, end)
        if isinstance(result, Exception):
            raise result
        self.routes.append(result)
        return RouteResult(points=result, length_km=0.0)


def run(router: ReplayRouter, points=POINTS, denied=DENIED):
    return safety_problem(router, [Segment("stroke", 0, points, 0.0)], denied)


def fixed(points: list) -> Callable:
    return lambda a, b: points


KINDS = pytest.mark.parametrize("kind", ["wrong", "denied"])


# ---- 重現得出來：不算不安全 ----


@KINDS
def test_a_flagged_edge_the_legal_route_reproduces_is_not_unsafe(kind: str) -> None:
    router = ReplayRouter([CLEAN, flagged(kind)], fixed(straight(40.0, 60.0, 10.0)))  # 頂點間距與折線不同
    assert run(router) is None
    assert router.route_calls == [(POINTS[2], POINTS[3])]


@KINDS
def test_a_route_within_the_tolerance_still_reproduces(kind: str) -> None:
    router = ReplayRouter([flagged(kind)], fixed(line((40.0, 0.8), (60.0, 0.8))))
    assert run(router) is None


# ---- 重現不了：照舊算不安全 ----


def zigzag() -> list[tuple[float, float]]:
    """40–60 m 之間每公尺來回 0.9 m：每一點都離直線 ≤ 0.9 m，但長度多了約 7 m。"""
    return line(*[(40.0 + k, 0.9 * (k % 2)) for k in range(21)])


@KINDS
@pytest.mark.parametrize("route", [
    line((40.0, 0.0), (40.0, 30.0), (60.0, 30.0), (60.0, 0.0)),  # 繞路
    line((40.0, 1.5), (60.0, 1.5)),                              # 平行偏開 1.5 m、長度相同
    line((40.0, 0.0), (58.5, 0.0)),                              # 提早 1.5 m 停下
    zigzag(),                                                    # 點都在 1 m 內，但長度多 > 1 m
    line((40.0, 0.0)) * 2,                                       # 塌縮成一個點
], ids=["detour", "offset", "stops-short", "longer", "collapsed"])
def test_a_route_that_does_not_match_leaves_it_unsafe(kind: str, route: list) -> None:
    problem = run(ReplayRouter([CLEAN, flagged(kind)], fixed(route)))
    assert problem is not None and problem[0] == UNSAFE


@KINDS
def test_a_failed_route_leaves_it_unsafe(kind: str) -> None:
    problem = run(ReplayRouter([flagged(kind)], fixed(RoutingError("找不到路"))))
    assert problem is not None and problem[0] == UNSAFE


@KINDS
@pytest.mark.parametrize("route_edges", [
    [EdgeInfo(5, 0.0, wrong_way=True)],   # 重現出來的路線自己逆向（例如沒修補的路網沿被禁行的 way 走）
    [EdgeInfo(9, 0.0)],                   # 經過被禁行的 way
    [EdgeInfo(77, 0.0, wrong_way=None)],  # 方向判斷不了
    [],                                   # 配不到任何路段
    RoutingError("edge_walk 配不上"),
], ids=["wrong-way", "denied", "unknown", "empty", "error"])
def test_a_route_that_is_itself_not_provably_legal_leaves_it_unsafe(kind: str, route_edges) -> None:
    router = ReplayRouter([flagged(kind)], fixed(straight(40.0, 60.0, 10.0)), route_edges)
    problem = run(router)
    assert problem is not None and problem[0] == UNSAFE


@KINDS
@pytest.mark.parametrize("begin,end", [
    (None, None), (2, None), (None, 3), (3, 2), (-1, 3), (2, 6), (2, 99), (True, 3), (2.0, 3.0),
])
def test_a_missing_or_invalid_range_leaves_it_unsafe(kind: str, begin, end) -> None:
    router = ReplayRouter([flagged(kind, begin, end)], fixed(straight(40.0, 60.0, 10.0)))
    problem = run(router)
    assert problem is not None and problem[0] == UNSAFE and not router.route_calls


def test_one_unreproducible_edge_is_enough_and_the_report_names_only_it() -> None:
    reproducible, not_reproducible = flagged("wrong", 1, 2), EdgeInfo(6, 0.0, wrong_way=True, begin_shape_index=3, end_shape_index=4)

    def route_for(a, b):
        return straight(20.0, 40.0, 10.0) if a == POINTS[1] else line((60.0, 0.0), (60.0, 30.0), (80.0, 30.0), (80.0, 0.0))

    problem = run(ReplayRouter([reproducible, not_reproducible], route_for))
    assert problem is not None and problem[0] == UNSAFE and "[6]" in problem[1] and "5" not in problem[1]


def test_unflagged_edges_never_ask_for_a_route() -> None:
    router = ReplayRouter([CLEAN, EdgeInfo(2, 0.0, begin_shape_index=3, end_shape_index=5)], fixed(RoutingError("不該被呼叫")))
    assert run(router) is None and not router.route_calls


def test_unknown_direction_stays_undetermined_without_a_replay() -> None:
    router = ReplayRouter([EdgeInfo(5, 0.0, wrong_way=None, begin_shape_index=2, end_shape_index=3)], fixed(POINTS))
    problem = run(router)
    assert problem is not None and problem[0] == UNDETERMINED and not router.route_calls


# ---- 範圍太短與邊界 ----


@pytest.mark.parametrize("begin,end,expected", [
    (3, 3, (2, 4)),  # 起訖同一點：往外各擴一個點
    (0, 0, (0, 1)),  # 在折線開頭：只能往後擴
    (5, 5, (4, 5)),  # 在折線結尾：只能往前擴
    (0, 1, (0, 1)),  # 夠長不擴
])
def test_a_range_that_is_too_short_is_widened_by_one_point_each_side(begin, end, expected) -> None:
    router = ReplayRouter([flagged("wrong", begin, end)], lambda a, b: [a, b])
    assert run(router) is None
    assert router.route_calls == [(POINTS[expected[0]], POINTS[expected[1]])]


def test_a_range_that_cannot_be_widened_enough_is_unsafe() -> None:
    tiny = straight(0.0, 0.9, 0.3)  # 整條折線不到 1 m
    router = ReplayRouter([flagged("wrong", 1, 1)], lambda a, b: [a, b])
    problem = run(router, tiny)
    assert problem is not None and problem[0] == UNSAFE and not router.route_calls


def test_ranges_of_a_split_segment_are_relative_to_the_whole_segment() -> None:
    """edge_walk 整段配不上時 _edges 切成兩半各配各的：後半的範圍要加上切點的位移，route 才會量到正確的位置。"""

    class SplitRouter(ReplayRouter):
        def edges(self, points):
            if len(points) == len(POINTS):
                raise RoutingError("edge_walk 整段配不上")
            if list(points) in self.routes:
                return self.route_edges
            if points[0] == POINTS[0]:
                return [CLEAN]
            return [flagged("wrong", 1, 2)]  # 後半 [60, 80, 100 m] 的第 1～2 個點 = 整條的第 4～5 個點

    router = SplitRouter([], lambda a, b: [a, b])
    assert run(router) is None
    assert router.route_calls == [(POINTS[4], POINTS[5])]


# ---- same_geometry：雙向、長度、取樣 ----


def test_same_geometry_measures_point_to_polyline_not_point_to_point() -> None:
    sparse, dense = line((0.0, 0.0), (100.0, 0.0)), straight(0.0, 100.0, 10.0)  # 頂點最遠差 50 m 的同一條直線
    assert same_geometry(sparse, dense) and same_geometry(dense, sparse)


def test_same_geometry_rejects_what_only_one_direction_would_miss() -> None:
    """a 的每一處都在 b 的 1 m 內，但 b 有一處（29.5 m 處的 1.4 m 岔出）離 a 1.4 m；長度只差 0.4 m。
    只量 a→b 或只量 b→a 都會放行其中一個參數順序。"""
    spur = line((0.0, 0.0), (30.0, 0.0), (0.0, 0.0))
    bulge = line((0.0, 0.0), (29.5, 0.0), (29.5, 1.4), (0.0, 0.0))
    assert not same_geometry(spur, bulge)
    assert not same_geometry(bulge, spur)


def test_same_geometry_samples_between_vertices() -> None:
    """兩條折線的頂點都在對方 1 m 內，只有頂點之間才量得到偏離（只量頂點會放行）。"""
    a = line((1.0, -1.5), (0.5, 1.0), (3.0, 0.0))
    b = line((1.0, 1.5), (3.0, 0.5), (0.5, -1.5))
    assert not same_geometry(a, b) and not same_geometry(b, a)


def test_same_geometry_needs_equal_lengths() -> None:
    assert not same_geometry(straight(0.0, 20.0, 10.0), zigzag_between(0.0, 20.0))
    assert not same_geometry([], straight(0.0, 20.0, 10.0))


def zigzag_between(x0: float, x1: float) -> list[tuple[float, float]]:
    return line(*[(x0 + k, 0.9 * (k % 2)) for k in range(round(x1 - x0) + 1)])


# ---- Router.edges 的範圍換算 ----


def test_align_shape_ignores_the_zero_length_trailing_edge_but_not_a_different_shape() -> None:
    shape = straight(0.0, 20.0, 10.0)
    assert _align_shape(shape, shape) == [0, 1, 2]
    assert _align_shape(shape + [shape[-1]], shape) == [0, 1, 2, 2]  # 終點是路口時回應尾端多一個重複的終點
    assert _align_shape(shape[:2], shape) is None
    assert _align_shape([shape[0], line((10.0, 3.0))[0], shape[2]], shape) is None  # 少一個點、換一個點都不行


def test_edge_span_maps_ranges_back_to_points_with_repeated_vertices() -> None:
    kept = [0, 1, 3]  # 輸入折線 [p0, p1, p1, p2]：第 2 個點是重複的
    aligned = [0, 1, 2]
    assert _edge_span({"begin_shape_index": 0, "end_shape_index": 1}, aligned, kept, 4) == {
        "begin_shape_index": 0, "end_shape_index": 2}
    assert _edge_span({"begin_shape_index": 1, "end_shape_index": 2}, aligned, kept, 4) == {
        "begin_shape_index": 1, "end_shape_index": 3}
    none = {"begin_shape_index": None, "end_shape_index": None}
    assert _edge_span({"begin_shape_index": 0, "end_shape_index": 1}, None, kept, 4) == none
    for edge in ({}, {"begin_shape_index": 1}, {"begin_shape_index": 2, "end_shape_index": 1},
                 {"begin_shape_index": 0, "end_shape_index": 3}, {"begin_shape_index": True, "end_shape_index": 1}):
        assert _edge_span(edge, aligned, kept, 4) == none


# ---- 真實 Valhalla（合成地圖） ----


@pytest.fixture(scope="module")
def router(planner_build: Path) -> Router:
    return Router.from_build(planner_build)


def along(*xs: float) -> list[tuple[float, float]]:
    return [meter_point(x + ONEWAY_X, 0.0) for x in xs]


def segment(points) -> list[Segment]:
    return [Segment("stroke", 0, points, 0.0)]


def overlap_points(row: int) -> list[tuple[float, float]]:
    return [meter_point(x, y) for x, y in overlap_shape(row)]


@pytest.mark.valhalla
def test_edges_report_where_each_edge_is_on_the_polyline(router: Router) -> None:
    eastward = along(100.0, 200.0, 400.0, 600.0, 700.0)
    assert [(e.begin_shape_index, e.end_shape_index) for e in router.edges(eastward)] == [(0, 1), (1, 2), (2, 3), (3, 4)]
    repeated = [eastward[0], eastward[0], *eastward[1:3], eastward[2], *eastward[3:]]  # 重複的點不影響編號的對應
    ranges = [(e.begin_shape_index, e.end_shape_index) for e in router.edges(repeated)]
    assert ranges == [(0, 2), (2, 4), (3, 5), (5, 6)]  # 起點取重複點的第一個、終點取最後一個


@pytest.mark.valhalla
@pytest.mark.parametrize("row", [0, 1])
def test_overlapping_footway_and_cycleway_is_not_a_false_alarm(router: Router, row: int) -> None:
    """真實路網 hi@taipei_daan 的誤報：footway 與 cycleway 完全重疊，edge_walk 一半配到 traversability: none 的 footway。"""
    points = overlap_points(row)
    edges = router.edges(points)
    misattributed = [e for e in edges if e.wrong_way]
    assert [e.use for e in misattributed] == ["footway"]  # 前提：edge_walk 真的配錯了
    assert (misattributed[0].begin_shape_index, misattributed[0].end_shape_index) == (1, 2)
    assert safety_problem(router, segment(points), frozenset()) is None
    # 禁行同理：footway 被標記禁行，route 走 cycleway 重現得出來
    footway = misattributed[0].way_id
    assert safety_problem(router, segment(points), frozenset({footway})) is None


@pytest.mark.valhalla
def test_a_denied_cycleway_cannot_be_excused_by_a_route_over_it(router: Router) -> None:
    """重現出來的路線自己經過被禁行的 way（這裡 cycleway 被禁行，route 在這個 router 上仍走它）→ 不豁免。"""
    points = overlap_points(0)
    cycleway = next(e.way_id for e in router.edges(points) if e.use == "cycleway")
    problem = safety_problem(router, segment(points), frozenset({cycleway}))
    assert problem is not None and problem[0] == UNSAFE and str(cycleway) in problem[1]


@pytest.mark.valhalla
def test_riding_a_one_way_backwards_is_still_caught_after_the_replay(router: Router) -> None:
    eastward = along(100.0, 200.0, 400.0, 600.0, 700.0)  # 中間 4002（oneway=yes 畫成往西）、4003（oneway=-1）逆向
    problem = safety_problem(router, segment(eastward), frozenset())
    assert problem is not None and problem[0] == UNSAFE and "4002" in problem[1] and "4003" in problem[1]
    assert not legal_replay(router, eastward, 1, 2, frozenset())
    assert not legal_replay(router, eastward, 2, 3, frozenset())
    short = along(1070.0, 1080.0, 1120.0, 1130.0)  # 40 m 的單行道逆向：route 必須繞到旁邊的平行路
    assert safety_problem(router, segment(short), frozenset())[0] == UNSAFE
    assert not legal_replay(router, short, 1, 2, frozenset())


@pytest.mark.valhalla
def test_the_replay_accepts_a_legal_ride_over_the_same_roads(router: Router) -> None:
    westward = along(700.0, 600.0, 400.0, 200.0, 100.0)  # 順著單行道騎：route 重現得出來
    assert legal_replay(router, westward, 2, 3, frozenset())
    assert legal_replay(router, westward, 0, 4, frozenset())
