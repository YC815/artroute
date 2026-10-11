"""quality="fine"（P2.5 計畫 B3）：描形規劃器 → Valhalla 照著走 → 失敗的範圍退回 fast，合成地圖＋真的 Valhalla。

planner_build（conftest）用同一份修補後的 pbf 建 tiles 與可騎路網圖。每個場景驗證一件事（P2.5 spec §8）；
每個 fine 結果都過 assert_valid：相鄰段接點 ≤ 1 m、bench 的安全斷言（不經過禁行的 way）、每個接點 Valhalla 都會直接騎。
"""

import json
import time
from pathlib import Path

import numpy as np
import pytest
from valhalla import Actor

from engine.bench.runner import bad_turns, safety_problem
from engine.data.build import load_denied
from engine.matching.deadline import Deadline
from engine.matching.service import MatchResult, StrokeInput, match_shape
from engine.matching.types import JOIN_TOLERANCE_M
from engine.planner.follow import FOLLOW_RADIUS_M
from engine.planner.graph import BACKWARD, FLAGS, FORWARD, RideGraph, _region
from engine.planner.plan import plan_shape, region_bounds
from engine.routing.geo import haversine_m
from engine.routing.router import RouteResult, Router, load_config
from osm_fixture import (
    CORNER_FORK,
    CORNER_NEAR,
    CORNER_TARGET,
    CORNER_X,
    DETOUR_TARGET,
    DETOUR_X,
    DIAGONAL_ROAD,
    DIAGONAL_TARGET,
    GAP_TARGET,
    ONEWAY_TARGET,
    ONEWAY_X,
    PARALLEL_TARGET,
    SHORT_SPUR_TARGET,
    SHORT_SPUR_TIP,
    SPIKE_TARGET,
    SQUARE_TARGET,
    SQUARE_X,
    meter_point,
    to_meters,
)

pytestmark = pytest.mark.valhalla

Meters = list[tuple[float, float]]


@pytest.fixture(scope="module")
def router(planner_build: Path) -> Router:
    return Router.from_build(planner_build)


@pytest.fixture(scope="module")
def graph(planner_build: Path) -> RideGraph:
    return RideGraph.load(planner_build)


@pytest.fixture(scope="module")
def denied(planner_build: Path) -> frozenset[int]:
    return frozenset(d.id for d in load_denied(planner_build))


def strokes_of(*targets: Meters, closed: bool = False) -> list[StrokeInput]:
    return [StrokeInput([meter_point(x, y) for x, y in t], closed) for t in targets]


def run(router, strokes: list[StrokeInput], **kwargs) -> MatchResult:
    return match_shape(router, strokes, search_radius_m=None, deadline=Deadline(30), **kwargs)


def fine(router, graph, strokes: list[StrokeInput], **kwargs) -> MatchResult:
    return run(router, strokes, quality="fine", graph=graph, **kwargs)


def assert_valid(router: Router, result: MatchResult, denied: frozenset[int]) -> None:
    for a, b in zip(result.segments, result.segments[1:]):
        assert haversine_m(a.points[-1], b.points[0]) <= JOIN_TOLERANCE_M
    assert safety_problem(router, result.segments, denied) is None
    assert bad_turns(router, result.segments) == 0


def route_m(result: MatchResult) -> np.ndarray:
    return np.array(to_meters(result.route_points))


def dense(points: np.ndarray, step: float = 1.0) -> np.ndarray:
    out = [points[0]]
    for a, b in zip(points[:-1], points[1:]):
        count = max(1, int(np.ceil(np.hypot(*(b - a)) / step)))
        out.extend(a + (b - a) * k / count for k in range(1, count + 1))
    return np.array(out)


def distance_to(points: np.ndarray, line: Meters) -> np.ndarray:
    poly = np.array(line)
    a, d = poly[:-1], poly[1:] - poly[:-1]
    rel = points[:, None, :] - a[None]
    t = np.clip(np.einsum("nij,ij->ni", rel, d) / np.einsum("ij,ij->i", d, d), 0, 1)
    return np.hypot(*(rel - d[None] * t[..., None]).transpose(2, 0, 1)).min(axis=1)


def is_slice_of(points: list, of: list) -> bool:
    return any(of[k : k + len(points)] == points for k in range(len(of) - len(points) + 1))


class Recorder:
    """包住真的 Router：記下照著走（搜尋半徑 FOLLOW_RADIUS_M 的 trace）收到的點與 Valhalla 給的結果。"""

    def __init__(self, inner: Router) -> None:
        self.inner = inner
        self.follows: list[tuple[list, RouteResult | None]] = []

    def trace(self, points, *, search_radius_m):
        if search_radius_m != FOLLOW_RADIUS_M:
            return self.inner.trace(points, search_radius_m=search_radius_m)
        try:
            result = self.inner.trace(points, search_radius_m=search_radius_m)
        except Exception:
            self.follows.append((list(points), None))
            raise
        self.follows.append((list(points), result))
        return result

    def route(self, start, end, *, shortest=False):
        return self.inner.route(start, end, shortest=shortest)


# ---- 場景 ----


def test_parallel_road_is_used_without_going_around(router, graph, denied) -> None:
    """正下方的路中間斷掉：走 40 m 外的平行路，不從北邊 300 m 繞出去再繞回來；長度接近目標。"""
    result = fine(router, graph, strokes_of(PARALLEL_TARGET))
    assert_valid(router, result, denied)
    assert result.warnings == []
    route = route_m(result)
    assert route[:, 1].max() <= 1.0 and route[:, 1].min() >= -41.0
    assert route[0, 0] <= 15.0 and route[-1, 0] >= 1585.0
    assert result.route_length_m <= 1600 * 1.08
    assert distance_to(dense(route), PARALLEL_TARGET).max() <= 41.0


def test_corner_takes_the_junction_with_the_closer_angle(router, graph, denied) -> None:
    """60° 的折角：走提早 15 m 的 60° 岔路，不走正下方 85° 的路口（位置比較近、角度不像）。"""
    result = fine(router, graph, strokes_of(CORNER_TARGET))
    assert_valid(router, result, denied)
    route = route_m(result)
    after = dense(route[route[:, 0] > CORNER_X + 790])
    assert len(after) and distance_to(after, CORNER_FORK).max() < 1.0
    stub = dense(np.array(CORNER_NEAR[:2]))
    stub = stub[np.hypot(*(stub - CORNER_NEAR[0]).T) > 5.0]
    assert distance_to(stub, [tuple(p) for p in route]).min() > 3.0


def test_diagonal_follows_the_diagonal_road_not_a_zigzag(router, graph, denied) -> None:
    """棋盤裡的 45° 斜線：fine 走斜路（方向分數高），fast 貼著目標走鋸齒。"""
    strokes = strokes_of(DIAGONAL_TARGET)
    result, fast = fine(router, graph, strokes), run(router, strokes)
    assert_valid(router, result, denied)
    assert (distance_to(dense(route_m(result)), DIAGONAL_ROAD) < 2.0).mean() >= 0.85
    assert result.score.direction >= fast.score.direction + 30
    assert result.route_length_m < fast.route_length_m


def contraflow(result: MatchResult) -> list[tuple[float, float]]:
    """往東騎在只能往西的路段上的點（ONEWAY_MAP：200–600 m 的 oneway=yes 畫成往西與 oneway=-1、1080–1120 m 的
    40 m 單行道；600–1000 m 是 oneway:bicycle=no 與 cycleway=opposite_lane，自行車可以往東）。"""
    route = route_m(result) - (ONEWAY_X, 0.0)
    out = []
    for a, b in zip(route[:-1], route[1:]):
        on_street = abs(a[1]) < 1.0 and abs(b[1]) < 1.0
        middle = (a[0] + b[0]) / 2
        if on_street and b[0] - a[0] > 0.1 and (200.0 < middle < 600.0 or 1080.0 < middle < 1120.0):
            out.append((float(middle), float(a[1])))
    return out


def test_fine_never_rides_one_way_streets_backwards(router, graph, denied) -> None:
    """往東畫：單行道那兩段改走平行路；自行車可以雙向的兩段照目標走。往西畫：單行道順向，直接走。"""
    east = fine(router, graph, strokes_of(ONEWAY_TARGET))
    assert_valid(router, east, denied)
    assert contraflow(east) == []
    route = route_m(east) - (ONEWAY_X, 0.0)
    on_target = np.abs(route[:, 1]) < 1.0
    assert on_target[(route[:, 0] > 620) & (route[:, 0] < 980)].all()
    west = fine(router, graph, strokes_of(ONEWAY_TARGET[::-1]))
    assert_valid(router, west, denied)
    assert np.abs(route_m(west)[:, 1]).max() < 1.0


def test_reversed_stroke_uses_the_backward_plan_without_riding_one_ways_backwards(router, graph, denied) -> None:
    """第二筆往西畫、起點在東邊；第一筆沿平行路往西畫到西端：串接器從第一筆的終點接過來時選反向，用規劃器的
    backward（往東）照著走，單行道照樣繞平行路。backward 是照著走的結果，不是整段倒過來 trace。"""
    recorder = Recorder(router)
    strokes = strokes_of([(200.0 + ONEWAY_X, -40.0), (0.0 + ONEWAY_X, -40.0)], ONEWAY_TARGET[::-1])
    result = fine(recorder, graph, strokes)
    assert_valid(router, result, denied)
    assert contraflow(result) == []
    second = [s for s in result.segments if s.stroke_index == 1]
    start, end = to_meters([second[0].points[0], second[-1].points[-1]])
    assert start[0] - ONEWAY_X < 15.0 and end[0] - ONEWAY_X > 1185.0  # 由西往東騎（反向）
    eastward = [r for p, r in recorder.follows if r is not None and to_meters(p)[0][0] < to_meters(p)[-1][0] - 1000]
    assert eastward and any(is_slice_of(s.points, r.points) for s in second for r in eastward)
    assert result.warnings == []


def test_planner_ignoring_one_ways_is_still_stopped_by_valhalla(router, graph, denied) -> None:
    """Valhalla 把關：讓規劃器以為所有路都是雙向（region 的旗標全改成雙向），它規劃出逆向的路徑；
    Valhalla 照著走時不會逆向（map_snap 走不了或繞開，Hausdorff 擋下），那段退回 fast，輸出仍然不逆向。"""

    class IgnoresOneWays:
        def region(self, *bbox):
            region = graph.region(*bbox)
            edges = np.asarray(graph.edges[region.edge_ids]).copy()
            edges[:, FLAGS] = FORWARD | BACKWARD
            return _region(graph.nodes, region.edge_ids, edges, region.lengths, region.coords, region.coord_offsets)

    strokes = strokes_of(ONEWAY_TARGET)
    points = [s.points for s in strokes]
    plan = plan_shape(IgnoresOneWays().region(*region_bounds(points)), points, [False], deadline=Deadline(30), budget_s=30)
    planned = np.array(to_meters(plan[0].forward[0].points)) - (ONEWAY_X, 0.0)
    assert (np.abs(planned[(planned[:, 0] > 220) & (planned[:, 0] < 580), 1]) < 1.0).all()  # 前提：規劃真的逆向了
    result = fine(router, IgnoresOneWays(), strokes)
    assert_valid(router, result, denied)
    assert contraflow(result) == []


def test_edge_walk_does_not_check_one_ways_so_follow_does_not_use_it(planner_build: Path) -> None:
    """釘住 Valhalla 3.9.1 的行為（follow 的模組說明）：trace_route 的 edge_walk 只對起訖兩端的邊做通行過濾，
    中間沿 oneway=yes（畫成往西）與 oneway=-1 往東走的形狀照樣成功。所以照著走一律用 map_snap。
    升級 Valhalla 後這裡變紅，代表 edge_walk 開始檢查通行方向，可以重新評估。"""
    actor = Actor(load_config(planner_build))
    xs = (100.0, 200.0, 400.0, 600.0, 700.0)  # 中間兩條邊是只能往西的單行道，頭尾的邊是雙向的
    shape = [dict(zip(("lon", "lat"), meter_point(x + ONEWAY_X, 0.0))) for x in xs]
    response = actor.trace_route(json.dumps({"shape": shape, "costing": "bicycle", "shape_match": "edge_walk"}))
    assert json.loads(response)["trip"]["summary"]["length"] == pytest.approx(0.6, abs=0.01)


def test_fine_output_is_pruned_too(router, graph, denied) -> None:
    """照著走的結果騎進 9 m 的死巷再出來（Hausdorff 仍 ≤ 10 m，被收下）：剪小刺照樣作用在 fine 的輸出上。"""

    class SpurRouter(Recorder):
        """照著走時改回傳「先騎到死巷底、再回來騎完」的 Valhalla 路線（兩段 route 在死巷底相接）。"""

        def trace(self, points, *, search_radius_m):
            if search_radius_m != FOLLOW_RADIUS_M:
                return self.inner.trace(points, search_radius_m=search_radius_m)
            tip = meter_point(*SHORT_SPUR_TIP)
            into, out = self.inner.route(points[0], tip, shortest=True), self.inner.route(tip, points[-1], shortest=True)
            assert into.points[-1] == out.points[0]
            result = RouteResult(into.points + out.points[1:], into.length_km + out.length_km)
            self.follows.append((list(points), result))
            return result

    spur_router = SpurRouter(router)
    strokes = strokes_of(SHORT_SPUR_TARGET)
    tip = meter_point(*SHORT_SPUR_TIP)
    unpruned = fine(spur_router, graph, strokes, prune=False)
    assert any(haversine_m(p, tip) < 1.0 for p in unpruned.route_points)  # 前提：照著走的結果有那根刺
    assert all(r is not None for _, r in spur_router.follows)
    pruned = fine(spur_router, graph, strokes)
    assert_valid(router, pruned, denied)
    assert min(haversine_m(p, tip) for p in pruned.route_points) > 5.0
    assert pruned.route_length_m == pytest.approx(unpruned.route_length_m - 18.0, abs=1.0)


def test_closed_stroke_is_still_a_loop(router, graph, denied) -> None:
    result = fine(router, graph, strokes_of(SQUARE_TARGET, closed=True))
    assert_valid(router, result, denied)
    assert result.warnings == []
    assert haversine_m(result.route_points[0], result.route_points[-1]) <= JOIN_TOLERANCE_M
    route = route_m(result) - (SQUARE_X, 0.0)
    assert result.route_length_m == pytest.approx(3200, rel=0.03)
    assert distance_to(dense(route), [(10.0, 10.0), (810.0, 10.0), (810.0, 810.0), (10.0, 810.0), (10.0, 10.0)]).max() <= 15


def test_the_dead_end_spike_is_not_ridden(router, graph, denied) -> None:
    """目標沿死巷折返：fine 不騎進去又退出來（fast 會）。"""
    strokes = strokes_of(SPIKE_TARGET)
    result, fast = fine(router, graph, strokes), run(router, strokes)
    assert_valid(router, result, denied)
    assert route_m(result)[:, 1].max() < 1.0
    assert route_m(fast)[:, 1].max() > 50.0  # 對照：fast 騎進死巷


# ---- 退回 fast ----


def test_part_of_the_stroke_falls_back_to_fast_and_joins_within_tolerance(router, graph, denied) -> None:
    """中間那段 70 m 內沒有路：規劃器斷開，兩側照著走，中間交給 fast（繞路），三段用 connect 接起來。"""
    recorder = Recorder(router)
    result = fine(recorder, graph, strokes_of(DETOUR_TARGET))
    assert_valid(router, result, denied)
    assert result.warnings == []
    followed = [r for _, r in recorder.follows if r is not None]
    assert len(followed) == 2  # 兩側各一段照著走
    strokes = [s for s in result.segments if s.stroke_index == 0]
    assert sum(any(is_slice_of(s.points, r.points) for r in followed) for s in strokes) == 2
    assert "detour" in {s.kind for s in strokes}  # 中間那段是 fast 的合法繞路
    route = route_m(result) - (DETOUR_X, 0.0)
    assert route[0, 0] <= 15.0 and route[-1, 0] >= 1385.0


def test_unreachable_parts_break_instead_of_being_bridged(router, graph, denied) -> None:
    """路斷掉、fast 也接不上的地方斷開（不畫直線），交給串接器；接不上的部件丟掉並給 partial。"""
    result = fine(router, graph, strokes_of(GAP_TARGET))
    assert_valid(router, result, denied)
    assert [w.code for w in result.warnings] == ["partial"]


def test_planning_without_roads_in_the_region_is_exactly_fast(router, graph) -> None:
    """region 裡沒有路（規劃器什麼都規劃不出來）：整個筆畫交給 fast，結果與 fast 逐點相等。"""

    class FarAway:
        def region(self, min_lon, min_lat, max_lon, max_lat):
            return graph.region(min_lon + 1.0, min_lat, max_lon + 1.0, max_lat)

    for target, closed in ((DIAGONAL_TARGET, False), (SQUARE_TARGET, True), (GAP_TARGET, False)):
        strokes = strokes_of(target, closed=closed)
        assert fine(router, FarAway(), strokes) == run(router, strokes)


def test_follow_drifting_beyond_the_limit_falls_back_to_fast(router, graph, denied) -> None:
    """照著走的結果偏離規劃 20 m（假 router 把結果往北移）：不收，那段交給 fast；結果與 fast 逐點相等，
    偏離的點一個都沒有出現在輸出裡。拿掉 Hausdorff 檢查時這裡會紅。"""

    class Drifting(Recorder):
        def trace(self, points, *, search_radius_m):
            result = self.inner.trace(points, search_radius_m=search_radius_m)
            if search_radius_m != FOLLOW_RADIUS_M:
                return result
            shifted = [meter_point(x, y + 20.0) for x, y in to_meters(result.points)]
            self.follows.append((list(points), RouteResult(shifted, result.length_km)))
            return RouteResult(shifted, result.length_km)

    drifting = Drifting(router)
    for target in (PARALLEL_TARGET, DIAGONAL_TARGET):
        strokes = strokes_of(target)
        result = fine(drifting, graph, strokes)
        assert_valid(router, result, denied)
        shifted = {p for _, r in drifting.follows for p in r.points}
        assert not shifted & set(result.route_points)
        assert result == run(router, strokes)


# ---- quality 不帶、fast、沒有路網圖：與 fast 完全相同 ----


@pytest.mark.parametrize("target,closed", [(DIAGONAL_TARGET, False), (SQUARE_TARGET, True), (SPIKE_TARGET, False)])
def test_without_fine_and_a_graph_the_result_is_exactly_fast(router, graph, target, closed) -> None:
    strokes = strokes_of(target, closed=closed)
    baseline = match_shape(router, strokes, search_radius_m=None, deadline=Deadline(30))
    assert run(router, strokes, quality="fast", graph=graph) == baseline
    assert run(router, strokes, quality=None, graph=graph) == baseline
    assert run(router, strokes, quality="fine", graph=None) == baseline


# ---- 耗時（印出來，寫進 findings）----


def test_fine_timing_on_the_synthetic_scenes(router, graph) -> None:
    rows = []
    scenes = [("平行路", PARALLEL_TARGET, False), ("轉角", CORNER_TARGET, False), ("斜路", DIAGONAL_TARGET, False),
              ("單行道", ONEWAY_TARGET, False), ("死巷", SPIKE_TARGET, False), ("正方形", SQUARE_TARGET, True),
              ("斷開", GAP_TARGET, False), ("部分退回", DETOUR_TARGET, False)]
    for name, target, closed in scenes:
        strokes = strokes_of(target, closed=closed)
        times = {}
        for quality in ("fast", "fine"):
            started = time.perf_counter()
            run(router, strokes, quality=quality, graph=graph)
            times[quality] = (time.perf_counter() - started) * 1000
        rows.append(f"{name} fast {times['fast']:.0f} ms／fine {times['fine']:.0f} ms")
        assert times["fine"] < 5000
    print("\nfine 耗時（合成地圖）：" + "；".join(rows))

