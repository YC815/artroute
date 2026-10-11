"""剪掉、重畫、拖曳（route editing spec §2.2、§3.3、§6.2）。

合成地圖＋真的 Valhalla（@pytest.mark.valhalla）：剪掉用剪小刺的地圖（prune_config）加上 osm_fixture 的 EDIT_MAPS
（edit_config）。輸入路線由 router.route 的真實結果首尾相接組成（同吸附流程的輸出）。
Valhalla 很難造出來的情況（吸附偏掉、拖曳的路沒經過放開的位置、只有嚴格版才擋得住的接點）用 tests/edit_router.py 的假 router。
"""

import math
from collections.abc import Callable
from pathlib import Path

import pytest

from engine.bench.runner import bad_turns, safety_problem
from engine.edit.ops import DRAG_SPAN_M, cut_at, drag, reroute
from engine.edit.position import RoutePos, pos_at_arc, resolve
from engine.edit.splice import DEFAULT_MESSAGES, EditRejected
from engine.matching.compose import flat, route_points
from engine.matching.deadline import Deadline
from engine.matching.join import junction_ok
from engine.matching.prune import loop_candidates, prune_loops
from engine.matching.score import target_extent_m
from engine.matching.service import StrokeInput, check_continuous
from engine.matching.stroke import densify
from engine.matching.turns import distance_to_polyline_m
from engine.matching.types import JOIN_TOLERANCE_M, Segment, segment_from
from engine.routing.geo import haversine_m, polyline_length_m
from engine.routing.router import DRAG_SNAP_M, Router, RoutingError
from edit_router import FakeRouter
from osm_fixture import (
    EDIT_DRAG_X,
    EDIT_NEST_X,
    EDIT_ONEWAY_REVERSE_WAY_ID,
    EDIT_ONEWAY_REVERSE_X,
    EDIT_ONEWAY_YES_WAY_ID,
    EDIT_ONEWAY_YES_X,
    EDIT_SHAPE_TARGET,
    EDIT_SHAPE_X,
    PRUNE_BLOCK_ROW,
    PRUNE_BRIDGE_ROW,
    PRUNE_RING_ROW,
    X5,
    grid_point,
    meter_point,
)
from turn_router import east, north


@pytest.fixture(scope="module")
def prune_router(prune_config: Path) -> Router:
    return Router(prune_config)


@pytest.fixture(scope="module")
def edit_router(edit_config: Path) -> Router:
    return Router(edit_config)


def deadline() -> Deadline:
    return Deadline(60)


def p(col: float, row: float) -> tuple[float, float]:
    return grid_point(X5 + col, row)


def chain_of(router, *stops, stroke_index: int | None = 0) -> list[Segment]:
    """依序 route 相鄰兩站（shortest，同吸附流程），組成首尾相接的路線。"""
    return [segment_from("stroke", stroke_index, router.route(a, b, shortest=True)) for a, b in zip(stops, stops[1:])]


def offset_of(segment: Segment, point, tol_m: float = 0.5) -> float:
    """point（落在 segment 的折線上）從段起點沿折線量的公尺數。"""
    walked = 0.0
    for a, b in zip(segment.points, segment.points[1:]):
        if distance_to_polyline_m(point, [a, b]) <= tol_m:
            return walked + haversine_m(a, point)
        walked += haversine_m(a, b)
    raise AssertionError(f"{point} 不在這一段上")


def at(segments: list[Segment], index: int, point) -> RoutePos:
    return RoutePos(index, offset_of(segments[index], point))


def rejected(call: Callable[[], object]) -> EditRejected:
    with pytest.raises(EditRejected) as info:
        call()
    return info.value


def near(points, target, tol_m: float) -> bool:
    return any(haversine_m(q, target) <= tol_m for q in points)


def toward(a, b, meters: float):
    """從 a 往 b 走 meters 公尺（同 turns 的探針：經緯度線性內插）。"""
    t = meters / haversine_m(a, b)
    return a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t


# ---- 所有成功的輸出共通的安全檢查 ----


def _is_slice_of_original(segment: Segment, original: list[Segment]) -> bool:
    """保留的段落：中間的點是原本某一段連續的點，頭尾落在那一段的折線上（切點是在原折線上內插的）。"""
    inner = segment.points[1:-1]
    for o in original:
        if (o.kind, o.stroke_index) != (segment.kind, segment.stroke_index):
            continue
        if inner and not any(o.points[k : k + len(inner)] == inner for k in range(len(o.points) - len(inner) + 1)):
            continue
        if all(distance_to_polyline_m(q, o.points) < 1e-3 for q in (segment.points[0], segment.points[-1])):
            return True
    return False


def assert_safe_edit(router, original: list[Segment], result: list[Segment]) -> None:
    check_continuous(result)
    for a, b in zip(result, result[1:]):
        assert haversine_m(a.points[-1], b.points[0]) <= JOIN_TOLERANCE_M
    for segment in result:  # 不是原本幾何的切片就一定是新換上的（kind == "edit"）
        assert segment.kind == "edit" or _is_slice_of_original(segment, original)
    old = {(a.points[-1], b.points[0]) for a, b in zip(original, original[1:])}
    for i, (a, b) in enumerate(zip(result, result[1:])):
        if "edit" in (a.kind, b.kind) or (a.points[-1], b.points[0]) not in old:
            assert junction_ok(router, deadline(), result, i, strict=True), f"新接點 {i} 沒通過嚴格版"
    assert safety_problem(router, result, frozenset()) is None
    assert bad_turns(router, result) == 0


# ---- 場景（剪小刺的地圖） ----

BLOCK_A, BLOCK_C = p(0, PRUNE_BLOCK_ROW), p(6, PRUNE_BLOCK_ROW)
BX, BY = p(3, PRUNE_BLOCK_ROW), p(3.25, PRUNE_BLOCK_ROW)
BN1, BN2 = p(3, PRUNE_BLOCK_ROW + 0.12), p(3.25, PRUNE_BLOCK_ROW + 0.12)
RING_A, RING_X = p(0, PRUNE_RING_ROW), p(5, PRUNE_RING_ROW)
RING_R2, RING_R3 = p(5.25, PRUNE_RING_ROW), p(5.1, PRUNE_RING_ROW - 0.12)
BRIDGE_W, BRIDGE_E, UNDER_N = p(0, PRUNE_BRIDGE_ROW), p(3.1, PRUNE_BRIDGE_ROW), p(3, PRUNE_BRIDGE_ROW + 3)


def block_route(router) -> list[Segment]:
    return chain_of(router, BLOCK_A, BN1, BN2, BY, BX, BLOCK_C)


def short_block_route(router) -> list[Segment]:
    """同一個街廓圈，主街只有前後各約 100 m：圈長遠超過外框 15%。"""
    return chain_of(router, p(2.5, PRUNE_BLOCK_ROW), BN1, BN2, BY, BX, p(3.75, PRUNE_BLOCK_ROW))


def ring_route(router) -> list[Segment]:
    """主街走到底 X，繞小環回頭，原路回到 A。"""
    return chain_of(router, RING_A, RING_R2, RING_R3, RING_A)


# ---- 場景（EDIT_MAPS） ----


def nm(x: float, y: float):
    return meter_point(EDIT_NEST_X + x, y)


NEST_STOPS = [nm(0, 0), nm(200, 120), nm(250, 180), nm(300, 120), nm(300, 0), nm(200, 0), nm(500, 0)]
NEST_T, NEST_N1, NEST_N2 = nm(250, 180), nm(200, 120), nm(300, 120)


def nest_route(router) -> list[Segment]:
    """A → N1 → T → N2 → Y → X → C：死巷（M..M）是內圈，整個街廓（X..X）是外圈。"""
    return chain_of(router, *NEST_STOPS)


def oneway_route(router, x0: float) -> list[Segment]:
    """往東，經過南邊 120 m 的繞路；中間 200–400 m 的北邊是只能往西的單行道。"""
    return chain_of(router, meter_point(x0 + 100, 0), meter_point(x0 + 300, -120), meter_point(x0 + 500, 0))


def oneway_ends(segments: list[Segment]) -> tuple[RoutePos, RoutePos]:
    """單行道兩側 70 m（x = 130、470）。"""
    return RoutePos(0, 30.0), RoutePos(1, polyline_length_m(segments[1].points) - 30.0)


def shape_route(router) -> list[Segment]:
    """沿直路往東 1000 m（沒走目標的凸起）。"""
    return chain_of(router, meter_point(EDIT_SHAPE_X, 0), meter_point(EDIT_SHAPE_X + 1000, 0))


SHAPE_TARGET = [meter_point(*q) for q in EDIT_SHAPE_TARGET]
SHAPE_STROKES = [StrokeInput(SHAPE_TARGET, closed=False)]
SHAPE_A, SHAPE_B = RoutePos(0, 200.0), RoutePos(0, 800.0)


def drag_route(router) -> list[Segment]:
    return chain_of(router, meter_point(EDIT_DRAG_X, 0), meter_point(EDIT_DRAG_X + 1200, 0))


DRAG_GRAB, DRAG_TO = RoutePos(0, 600.0), meter_point(EDIT_DRAG_X + 600, 57)


# ---- 成功的案例：(router fixture, 原路線, 操作) ----

type Case = tuple[str, Callable[[Router], list[Segment]], Callable[[Router, list[Segment]], list[Segment]]]

SUCCESS: dict[str, Case] = {
    "cut_block": ("prune_router", block_route, lambda r, s: cut_at(r, deadline(), s, at(s, 1, BN1))),
    "cut_over_cap": ("prune_router", short_block_route, lambda r, s: cut_at(r, deadline(), s, at(s, 1, BN1))),
    "cut_nested_inner": ("edit_router", nest_route, lambda r, s: cut_at(r, deadline(), s, at(s, 1, nm(250, 150)))),
    "cut_nested_outer": ("edit_router", nest_route, lambda r, s: cut_at(r, deadline(), s, at(s, 0, nm(200, 60)))),
    "shortest_oneway_yes": (
        "edit_router", lambda r: oneway_route(r, EDIT_ONEWAY_YES_X),
        lambda r, s: reroute(r, deadline(), s, *oneway_ends(s), "shortest", []),
    ),
    "shortest_oneway_reverse": (
        "edit_router", lambda r: oneway_route(r, EDIT_ONEWAY_REVERSE_X),
        lambda r, s: reroute(r, deadline(), s, *oneway_ends(s), "shortest", []),
    ),
    "shortest_on_shape_map": (
        "edit_router", shape_route, lambda r, s: reroute(r, deadline(), s, SHAPE_A, SHAPE_B, "shortest", SHAPE_STROKES),
    ),
    "shape": ("edit_router", shape_route, lambda r, s: reroute(r, deadline(), s, SHAPE_A, SHAPE_B, "shape", SHAPE_STROKES)),
    "drag": ("edit_router", drag_route, lambda r, s: drag(r, deadline(), s, DRAG_GRAB, DRAG_TO)),
}


def run(request: pytest.FixtureRequest, name: str) -> tuple[Router, list[Segment], list[Segment]]:
    fixture, build, operate = SUCCESS[name]
    router = request.getfixturevalue(fixture)
    original = build(router)
    return router, original, operate(router, original)


@pytest.mark.valhalla
@pytest.mark.parametrize("name", list(SUCCESS))
def test_every_successful_edit_is_safe(request: pytest.FixtureRequest, name: str) -> None:
    """相鄰點 ≤ 1 m 或來自同一段；保留的只切原本的幾何、新的都是 kind="edit"；連續；新接點過嚴格版；
    不逆向、不走禁行；每個接點 Valhalla 都會直接騎。"""
    router, original, result = run(request, name)
    assert_safe_edit(router, original, result)
    assert result != original


# ---- 剪掉 ----


@pytest.mark.valhalla
def test_cut_removes_a_block_loop_beside_a_straight_line(request: pytest.FixtureRequest) -> None:
    _, original, result = run(request, "cut_block")
    points = route_points(result)
    assert not near(points, BN1, 10) and not near(points, BN2, 10)
    assert haversine_m(points[0], BLOCK_A) <= 1 and haversine_m(points[-1], BLOCK_C) <= 1
    assert sum(s.length_m for s in result) == pytest.approx(haversine_m(BLOCK_A, BLOCK_C), rel=0.01)
    assert all(s.kind == "stroke" for s in result)  # 純剪掉：沒有新的段落


@pytest.mark.valhalla
def test_cut_ignores_the_loop_length_cap(request: pytest.FixtureRequest, prune_router: Router) -> None:
    """使用者指定的剪法不套用剪小刺的圈長上限（外框 15%）。"""
    _, original, result = run(request, "cut_over_cap")
    target = [[p(2.5, PRUNE_BLOCK_ROW), p(3.75, PRUNE_BLOCK_ROW)]]
    assert 0.15 * target_extent_m(target) < 100 < polyline_length_m([BX, BN1, BN2, BY, BX])  # 前提：圈超過上限
    assert prune_loops(prune_router, original, target, deadline(), extent_m=target_extent_m(target)) == original
    assert not near(route_points(result), BN1, 10)


@pytest.mark.valhalla
def test_cut_refuses_the_only_loop_to_turn_around(prune_router: Router) -> None:
    """主街走到底只能繞小環回頭：剪掉任何一個含點擊處的圈都是原地迴轉（剪掉要用嚴格版，非嚴格版放行迴轉）。"""
    segments = ring_route(prune_router)
    click = RoutePos(1, 10.0)
    assert rejected(lambda: cut_at(prune_router, deadline(), segments, click)).code == "turn_not_rideable"


@pytest.mark.valhalla
def test_cut_onto_the_road_under_a_bridge_is_refused(prune_router: Router) -> None:
    """橋上 K' 與下面那條路的 K 平面上相距 0.3 m（是圈），剪了就是從橋上直接轉進下面那條路，Valhalla 不會這樣騎。
    這個轉彎不是迴轉，便宜的篩選擋不住，只有 route 量得出來：拿掉 cut_at 的 turns_ok 會紅。"""
    segments = chain_of(prune_router, BRIDGE_W, BRIDGE_E, UNDER_N)
    click = RoutePos(1, 5.0)
    assert loop_candidates([q.point for q in flat(segments)], math.inf)  # 前提：K'..K 是圈
    assert rejected(lambda: cut_at(prune_router, deadline(), segments, click)).code == "turn_not_rideable"


@pytest.mark.valhalla
def test_cut_inside_nested_loops_takes_the_inner_one(request: pytest.FixtureRequest) -> None:
    _, _, inner = run(request, "cut_nested_inner")
    points = route_points(inner)
    assert not near(points, NEST_T, 30)  # 死巷剪掉了
    assert near(points, NEST_N1, 1) and near(points, NEST_N2, 1)  # 街廓還在
    _, _, outer = run(request, "cut_nested_outer")
    assert not near(route_points(outer), NEST_N1, 30)  # 點在外圈（不在死巷裡）：整個街廓剪掉


@pytest.mark.valhalla
def test_cut_where_there_is_no_loop(edit_router: Router) -> None:
    segments = nest_route(edit_router)
    click = RoutePos(len(segments) - 1, polyline_length_m(segments[-1].points) - 50.0)
    error = rejected(lambda: cut_at(edit_router, deadline(), segments, click))
    assert (error.code, error.message) == ("nothing_to_cut", DEFAULT_MESSAGES["nothing_to_cut"])


def test_positions_off_the_route_are_not_on_route() -> None:
    segments = [Segment("stroke", 0, [(121.5, 25.05), east((121.5, 25.05), 100)], 100.0)]
    router = FakeRouter()
    for call in (
        lambda: cut_at(router, deadline(), segments, RoutePos(3, 0.0)),
        lambda: reroute(router, deadline(), segments, RoutePos(0, 10.0), RoutePos(0, 500.0), "shortest", []),
        lambda: drag(router, deadline(), segments, RoutePos(0, -1.0), (121.5, 25.05)),
    ):
        assert rejected(call).code == "not_on_route"
    assert router.calls == []


# ---- 重畫 ----


@pytest.mark.valhalla
@pytest.mark.parametrize("name, x0, way_id", [
    ("shortest_oneway_yes", EDIT_ONEWAY_YES_X, EDIT_ONEWAY_YES_WAY_ID),
    ("shortest_oneway_reverse", EDIT_ONEWAY_REVERSE_X, EDIT_ONEWAY_REVERSE_WAY_ID),
], ids=["oneway=yes", "oneway=-1"])
def test_shortest_reroute_does_not_ride_a_one_way_backwards(request, name: str, x0: float, way_id: int) -> None:
    """兩點之間最近的是往東逆向走單行道（前提斷言）；最短模式只用 Valhalla 的 route，改走南邊 40 m 的平行路。
    不逆向由 Valhalla 的 bicycle 規則保證，ops 沒有自己的一行可以拿掉；前提斷言確保這個測試不是空的
    （新路線若不是來自 Valhalla、直接接 a→b，safety_problem 會抓到 way_id 逆向）。"""
    router, original, result = run(request, name)
    shortcut = [meter_point(x0 + x, 0) for x in (130.0, 200.0, 400.0, 470.0)]
    assert any(e.way_id == way_id and e.wrong_way for e in router.edges(shortcut))
    points = route_points(result)
    assert distance_to_polyline_m(meter_point(x0 + 300, -40), points) <= 1  # 走平行路
    assert distance_to_polyline_m(meter_point(x0 + 300, -120), points) > 30  # 不再走原本的繞路
    assert safety_problem(router, result, frozenset()) is None


def _mean_distance_to_target(points) -> float:
    dense = densify(points, 10.0)
    return sum(distance_to_polyline_m(q, SHAPE_TARGET) for q in dense) / len(dense)


@pytest.mark.valhalla
def test_shape_reroute_follows_the_target(request: pytest.FixtureRequest) -> None:
    _, _, shape = run(request, "shape")
    _, _, shortest = run(request, "shortest_on_shape_map")
    assert distance_to_polyline_m(meter_point(EDIT_SHAPE_X + 500, 100), route_points(shape)) <= 5  # 走凸起那條路
    assert _mean_distance_to_target(route_points(shape)) < _mean_distance_to_target(route_points(shortest))
    assert {s.stroke_index for s in shape} == {0}


@pytest.mark.valhalla
@pytest.mark.parametrize("mode", ["shortest", "shape"])
def test_reroute_does_not_depend_on_which_point_comes_first(edit_router: Router, mode) -> None:
    segments = shape_route(edit_router)
    forward = reroute(edit_router, deadline(), segments, SHAPE_A, SHAPE_B, mode, SHAPE_STROKES)
    assert reroute(edit_router, deadline(), segments, SHAPE_B, SHAPE_A, mode, SHAPE_STROKES) == forward


@pytest.mark.valhalla
def test_shortest_reroute_that_turns_back_at_the_cut_is_refused(prune_router: Router) -> None:
    """去程 X 前 100 m 到回程 X 後 150 m：最短路是在 a 原地迴轉往西 50 m。
    把 reroute 的 splice 換成直接串接（[*before, 新段, *after]）會紅。"""
    segments = ring_route(prune_router)
    a = RoutePos(0, offset_of(segments[0], RING_X) - 100.0)
    b = RoutePos(2, offset_of(segments[2], RING_X) + 150.0)
    error = rejected(lambda: reroute(prune_router, deadline(), segments, a, b, "shortest", []))
    assert error.code == "turn_not_rideable"


def test_reroute_between_points_too_close_is_refused() -> None:
    segments = [Segment("stroke", 0, [(121.5, 25.05), east((121.5, 25.05), 100)], 100.0)]
    router = FakeRouter()
    error = rejected(lambda: reroute(router, deadline(), segments, RoutePos(0, 50.0), RoutePos(0, 51.5), "shortest", []))
    assert error.code == "no_route" and "太近" in error.message
    assert router.calls == []


W0 = (121.5, 25.05)
LINE = [W0, east(W0, 600)]
LINE_STROKES = [StrokeInput(list(LINE), closed=False)]


@pytest.mark.parametrize("segments", [
    [Segment("stroke", 0, [W0, east(W0, 300)], 300.0), Segment("stroke", 1, [east(W0, 300), east(W0, 600)], 300.0)],
    [Segment("stroke", 0, [W0, east(W0, 300)], 300.0),
     Segment("connector", None, [east(W0, 300), east(W0, 600)], 300.0)],
], ids=["two_strokes", "connector"])
def test_shape_reroute_across_strokes_is_refused(segments) -> None:
    router = FakeRouter()
    error = rejected(lambda: reroute(router, deadline(), segments, RoutePos(0, 100.0), RoutePos(1, 100.0), "shape",
                                     LINE_STROKES * 2))
    assert error.code == "no_route" and "最短" in error.message
    assert router.calls == []


def _line_route() -> list[Segment]:
    return [Segment("stroke", 0, list(LINE), polyline_length_m(LINE))]


@pytest.mark.parametrize("side", ["start", "end"])
def test_shape_reroute_checks_its_new_junctions_strictly(side: str) -> None:
    """吸附結果剛好從切點出發、在切點結束：connect 判斷相鄰（直行 0°，原本的規則不必量）就直接串。
    接點的直行其實騎不過去（從探針出發的 route 繞一大圈）：只有嚴格版量得出來。
    把 reroute 傳給 splice 的 strict 拿掉（或改成空的）會紅。"""
    segments = _line_route()
    a, b = RoutePos(0, 200.0), RoutePos(0, 400.0)
    cut = resolve(segments, a if side == "start" else b).point
    probe = toward(cut, W0, 8.0)  # 接點往回 8 m 的探針
    router = FakeRouter(blocked_from=[probe], trace=lambda points: points)
    assert rejected(lambda: reroute(router, deadline(), segments, a, b, "shape", LINE_STROKES)).code == "turn_not_rideable"
    result = reroute(FakeRouter(trace=lambda points: points), deadline(), segments, a, b, "shape", LINE_STROKES)
    assert [s.kind for s in result] == ["stroke", "edit", "stroke"]  # 對照：騎得過去就收


def test_shape_reroute_checks_the_link_junctions_strictly() -> None:
    """吸附結果的起點在切點東邊 5 m：connect 補一段 link（切點 → 吸附起點），多出兩個接點；
    吸附起點那個接點（link|吸附結果）的直行騎不過去。
    新接點只算 connect 的第一個接點（漏掉 link 之後那個）會紅。"""
    segments = _line_route()
    a, b = RoutePos(0, 200.0), RoutePos(0, 400.0)
    cut = resolve(segments, a).point
    shifted = lambda points: [east(points[0], 5), *points[1:]]  # noqa: E731
    probe = toward(cut, W0, 3.0)  # link 之後那個接點往回 8 m：link 的 5 m 再加切點之前 3 m
    router = FakeRouter(blocked_from=[probe], trace=shifted)
    assert rejected(lambda: reroute(router, deadline(), segments, a, b, "shape", LINE_STROKES)).code == "turn_not_rideable"
    result = reroute(FakeRouter(trace=shifted), deadline(), segments, a, b, "shape", LINE_STROKES)
    assert [s.kind for s in result] == ["stroke", "edit", "edit", "stroke"]  # 對照：link 與吸附結果都是 edit


S0 = W0
S1 = east(S0, 200)
S2 = north(S1, 200)
S3 = north(S0, 200)
SQUARE = StrokeInput([S0, S1, S2, S3], closed=True)


@pytest.mark.parametrize("route, stroke, passes, avoids", [
    ([S0, S1, S2, S3, S0], SQUARE, [S1, S2], [S3, S0]),  # 照畫的方向，不跨收尾
    ([S2, S3, S0, S1, S2], SQUARE, [S3, S0], [S1, S2]),  # 從 t = 500 走到 t = 100：跨過收尾（S0）
    ([S2, S1, S0], StrokeInput([S0, S1, S2], closed=False), [S1], [S2, S0]),  # 開放筆畫反著騎：目標倒過來取
], ids=["closed", "closed_wraps", "open_reversed"])
def test_shape_reroute_takes_the_target_between_the_two_points(route, stroke, passes, avoids) -> None:
    """吸附原樣回傳目標，所以換上的段落就是取出來的那段目標：從 a 沿畫的方向（開放筆畫依先後）走到 b。"""
    segments = [Segment("stroke", 0, route, polyline_length_m(route))]
    a, b = 100.0, 300.0 if len(route) == 3 else 500.0
    result = reroute(FakeRouter(trace=lambda points: points), deadline(), segments, RoutePos(0, a), RoutePos(0, b),
                     "shape", [stroke])
    (edit,) = [s for s in result if s.kind == "edit"]
    assert all(near(edit.points, q, 1) for q in passes) and not any(near(edit.points, q, 50) for q in avoids)
    assert polyline_length_m(edit.points) == pytest.approx(b - a, abs=1)  # 一路往前，沒有來回


def test_shape_reroute_needs_a_single_clean_match() -> None:
    """吸附有斷開或警告就不收：收下的話中間會缺一段，或是吸附自己丟掉的部分被當成走過了。"""
    segments = _line_route()

    def broken(points):
        raise RoutingError("吸附不到")

    router = FakeRouter(trace=broken, fail=True)  # trace 與繞路的 route 全部失敗：吸附只剩警告
    error = rejected(lambda: reroute(router, deadline(), segments, RoutePos(0, 200.0), RoutePos(0, 400.0), "shape",
                                     LINE_STROKES))
    assert error.code == "no_route"


# ---- 拖曳 ----


@pytest.mark.valhalla
def test_drag_onto_a_parallel_road(request: pytest.FixtureRequest) -> None:
    """結果經過放開的位置 10 m 內；抓點前後 200 m 以外的點與原路線完全相同（前段、後段的點是原本的點）。"""
    _, original, result = run(request, "drag")
    points = route_points(result)
    assert distance_to_polyline_m(DRAG_TO, points) <= 10
    assert any(s.kind == "edit" for s in result)
    old = route_points(original)
    arcs = [polyline_length_m(old[: k + 1]) for k in range(len(old))]
    lo, hi = DRAG_GRAB.offset_m - DRAG_SPAN_M, DRAG_GRAB.offset_m + DRAG_SPAN_M
    head = [q for q, s in zip(old, arcs) if s < lo - 1e-6]
    tail = [q for q, s in zip(old, arcs) if s > hi + 1e-6]
    assert head and tail  # 前提：範圍外兩側都有點
    assert points[: len(head)] == head and points[-len(tail):] == tail


@pytest.mark.valhalla
def test_drag_into_empty_land_is_no_road(edit_router: Router) -> None:
    segments = drag_route(edit_router)
    to = meter_point(EDIT_DRAG_X + 600, -200)  # 最近的路在 200 m 外
    error = rejected(lambda: drag(edit_router, deadline(), segments, DRAG_GRAB, to))
    assert (error.code, error.message) == ("no_road", DEFAULT_MESSAGES["no_road"])


def _long_line() -> list[Segment]:
    points = [east(W0, -500), east(W0, 500)]
    return [Segment("stroke", 0, points, polyline_length_m(points))]


def test_drag_result_must_pass_near_the_release_point() -> None:
    """route 成功、端點也對，但沒有經過放開的位置（離 80 m）：不收。拿掉 drag 的 DRAG_SNAP_M 檢查會紅。"""
    to = north(W0, 80)
    assert 80 > DRAG_SNAP_M
    router = FakeRouter(ignore_via=True)
    assert rejected(lambda: drag(router, deadline(), _long_line(), RoutePos(0, 500.0), to)).code == "no_road"
    assert router.calls and router.calls[0][0] == "route_through"


def test_drag_routing_failure_is_no_road() -> None:
    router = FakeRouter(fail=True)
    assert rejected(lambda: drag(router, deadline(), _long_line(), RoutePos(0, 500.0), north(W0, 20))).code == "no_road"


def test_drag_keeps_turn_not_rideable() -> None:
    """拖曳的路找到了，是接回原路線的轉彎騎不過去：照樣回 turn_not_rideable（不是 no_road，使用者要的是放大範圍）。
    把 drag 的 splice 也包進「失敗一律 no_road」會紅。"""
    segments = _long_line()
    start = resolve(segments, pos_at_arc(segments, 500.0 - DRAG_SPAN_M)).point
    router = FakeRouter(blocked_from=[toward(start, segments[0].points[0], 8.0)])
    error = rejected(lambda: drag(router, deadline(), segments, RoutePos(0, 500.0), north(W0, 20)))
    assert error.code == "turn_not_rideable"
    result = drag(FakeRouter(), deadline(), segments, RoutePos(0, 500.0), north(W0, 20))  # 對照
    assert [s.kind for s in result] == ["stroke", "edit", "stroke"]
