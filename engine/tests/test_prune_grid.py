"""剪小刺與小圈（P2.5 A4）：合成地圖＋真的 Valhalla。路線由 router.route 的真實結果首尾相接組成（同 compose 的輸出）。"""

from pathlib import Path

import pytest

from engine.bench.runner import bad_turns, safety_problem
from engine.matching.compose import route_points
from engine.matching.deadline import Deadline
from engine.matching.join import junction_ok
from engine.matching.prune import prune_loops
from engine.matching.score import score, target_extent_m
from engine.matching.types import JOIN_TOLERANCE_M, Segment, segment_from
from engine.routing.geo import haversine_m
from engine.routing.router import Router
from osm_fixture import (
    BRIDGE_GAP_COL,
    PRUNE_BLOCK_ROW,
    PRUNE_BRIDGE_ROW,
    PRUNE_MOTORWAY_WAY_ID,
    PRUNE_RING_ROW,
    PRUNE_SPUR_ROW,
    X5,
    grid_point,
)

pytestmark = pytest.mark.valhalla

DENIED = frozenset({PRUNE_MOTORWAY_WAY_ID})


@pytest.fixture(scope="module")
def router(prune_config: Path) -> Router:
    return Router(prune_config)


def p(col: float, row: float) -> tuple[float, float]:
    return grid_point(X5 + col, row)


def chain_of(router: Router, *stops) -> list[Segment]:
    """依序 route 相鄰兩站（shortest，同吸附流程），組成首尾相接的路線。"""
    return [segment_from("stroke", 0, router.route(a, b, shortest=True)) for a, b in zip(stops, stops[1:])]


def assert_valid(router: Router, segments: list[Segment]) -> None:
    """bench 的安全斷言：接點 ≤ 1 m、不經過禁行的 way、每個接點 Valhalla 都會直接騎。"""
    for a, b in zip(segments, segments[1:]):
        assert haversine_m(a.points[-1], b.points[0]) <= JOIN_TOLERANCE_M
    assert safety_problem(router, segments, DENIED) is None
    assert bad_turns(router, segments) == 0


def assert_points_from(result: list[Segment], original: list[Segment]) -> None:
    """輸出的每一段都是原本某一段的連續切片：點全是 Valhalla 給的點、騎的順序不變。"""
    for s in result:
        assert any(
            o.points[k : k + len(s.points)] == s.points for o in original for k in range(len(o.points) - len(s.points) + 1)
        )


def prune(router: Router, segments: list[Segment], targets) -> list[Segment]:
    return prune_loops(router, segments, targets, Deadline(30), extent_m=target_extent_m(targets))


def near(point, target, tol_m: float = 1.0) -> bool:
    return haversine_m(point, target) <= tol_m


# ---- a. 直線旁的死巷：A → X → 死巷底 → X → C 剪成 A → X → C ----

SPUR_A, SPUR_X, SPUR_TIP, SPUR_C = (p(c, r) for c, r in ((0, PRUNE_SPUR_ROW), (3, PRUNE_SPUR_ROW), (3, PRUNE_SPUR_ROW + 0.25), (6, PRUNE_SPUR_ROW)))


def test_dead_end_spur_off_a_straight_line_is_cut(router: Router) -> None:
    segments = chain_of(router, SPUR_A, SPUR_TIP, SPUR_C)
    assert_valid(router, segments)  # 前提：原本的路線是吸附流程收得下的
    assert any(near(q, SPUR_TIP) for q in route_points(segments))
    result = prune(router, segments, [[SPUR_A, SPUR_C]])
    points = route_points(result)
    assert not any(haversine_m(q, SPUR_TIP) < 30 for q in points)  # 死巷剪掉了
    assert near(points[0], SPUR_A) and near(points[-1], SPUR_C)
    assert_points_from(result, segments)
    assert_valid(router, result)
    assert sum(s.length_m for s in result) == pytest.approx(haversine_m(SPUR_A, SPUR_C), rel=0.01)


# ---- b. 直線旁的街廓圈：在 X 往北繞一個街廓回到 X，再繼續往東 ----

BX, BY = p(3, PRUNE_BLOCK_ROW), p(3.25, PRUNE_BLOCK_ROW)
BN1, BN2 = p(3, PRUNE_BLOCK_ROW + 0.12), p(3.25, PRUNE_BLOCK_ROW + 0.12)
BLOCK_A, BLOCK_C = p(0, PRUNE_BLOCK_ROW), p(6, PRUNE_BLOCK_ROW)
BLOCK_LOOP = [BX, BN1, BN2, BY, BX]


@pytest.fixture(scope="module")
def block_route(router: Router) -> list[Segment]:
    segments = chain_of(router, BLOCK_A, BN1, BN2, BY, BX, BLOCK_C)
    assert_valid(router, segments)
    return segments


def test_block_loop_beside_a_straight_line_is_cut(router: Router, block_route: list[Segment]) -> None:
    result = prune(router, block_route, [[BLOCK_A, BLOCK_C]])
    points = route_points(result)
    assert not any(haversine_m(q, BN1) < 10 or haversine_m(q, BN2) < 10 for q in points)
    assert near(points[0], BLOCK_A) and near(points[-1], BLOCK_C)
    assert_points_from(result, block_route)
    assert_valid(router, result)
    assert sum(s.length_m for s in result) == pytest.approx(haversine_m(BLOCK_A, BLOCK_C), rel=0.01)


# ---- c. 只能靠繞圈迴轉：主街走到底，繞小環回頭。剪了就是在 X 原地迴轉 → 不剪 ----

RING_A, RING_X = p(0, PRUNE_RING_ROW), p(5, PRUNE_RING_ROW)
RING_R2, RING_R3 = p(5.25, PRUNE_RING_ROW), p(5.1, PRUNE_RING_ROW - 0.12)


def test_loop_that_is_the_only_way_to_turn_around_is_kept(router: Router) -> None:
    segments = chain_of(router, RING_A, RING_R2, RING_R3, RING_A)
    assert_valid(router, segments)
    targets = [[RING_A, RING_X]]  # 目標是來回的那條街：環不在目標裡，剪了 v3 會上升
    assert prune(router, segments, targets) == segments


# ---- d. 新接點轉角 > 30° 但 Valhalla 不會直接騎（橋跨過下面那條路）→ 不剪 ----

BRIDGE_W, BRIDGE_E = p(0, PRUNE_BRIDGE_ROW), p(3.1, PRUNE_BRIDGE_ROW)
BRIDGE_K_UPPER = p(3 + BRIDGE_GAP_COL, PRUNE_BRIDGE_ROW)
UNDER_K, UNDER_N = p(3, PRUNE_BRIDGE_ROW), p(3, PRUNE_BRIDGE_ROW + 3)


def test_cut_onto_the_road_under_a_bridge_is_not_made(router: Router) -> None:
    segments = chain_of(router, BRIDGE_W, BRIDGE_E, UNDER_N)
    assert_valid(router, segments)
    targets = [[BRIDGE_W, UNDER_K, UNDER_N]]
    assert prune(router, segments, targets) == segments
    # 不是因為別的條件沒剪：在 K' / K 剪掉那一圈，距離 0.3 m、v3 上升，只差新接點不能騎
    first, second = segments
    i = next(k for k, q in enumerate(first.points) if near(q, BRIDGE_K_UPPER, 0.05))
    j = next(k for k, q in enumerate(second.points) if near(q, UNDER_K, 0.05))
    cut = [Segment("stroke", 0, first.points[: i + 1], 0.0), Segment("stroke", 0, second.points[j:], 0.0)]
    assert haversine_m(cut[0].points[-1], cut[1].points[0]) <= JOIN_TOLERANCE_M
    assert score(targets, route_points(cut)).exact > score(targets, route_points(segments)).exact
    assert not junction_ok(router, Deadline(30), cut, 0, strict=True)


# ---- e. 目標本身就有那個圈：剪了 v3 下降 → 不剪 ----


def test_loop_drawn_in_the_target_is_kept(router: Router, block_route: list[Segment]) -> None:
    assert prune(router, block_route, [[BLOCK_A, BLOCK_C], BLOCK_LOOP]) == block_route


# ---- f. 圈比 0.15 × 目標外框長邊還長 → 不剪 ----


def test_loop_longer_than_the_cap_is_kept(router: Router, block_route: list[Segment]) -> None:
    short_target = [[p(1.5, PRUNE_BLOCK_ROW), p(4.5, PRUNE_BLOCK_ROW)]]  # 外框長邊約 605 m：上限約 91 m，街廓圈約 154 m
    assert 0.15 * target_extent_m(short_target) < 100
    assert prune(router, block_route, short_target) == block_route
