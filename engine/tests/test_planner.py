"""描形規劃器（P2.5 spec §6.2、§8）：合成地圖先跑 patch_pbf 再建可騎路網圖（與建圖流程相同），不需要 Valhalla。

每個場景驗證一件事；所有規劃結果都過 check_piece（方向合法、邊相連、點都在邊上、沒有跳點）。
"""

import json
import math
import time
from pathlib import Path

import numpy as np
import pytest

from engine.data.bike_patch import Overrides, patch_pbf
from engine.data.ridegraph import extract_graph
from engine.matching.deadline import Deadline, MatchCancelled
from engine.planner import plan as plan_module
from engine.planner.graph import BACKWARD, FORWARD, Region, RideGraph
from engine.planner.plan import PlannedPiece, PlannerWeights, StrokePlan, plan_shape, region_bounds
from engine.search.geometry import place
from engine.search.types import NormStroke, Placement
from osm_fixture import (
    CORNER_FORK,
    CORNER_MAP,
    CORNER_NEAR,
    CORNER_TARGET,
    CORNER_X,
    DIAGONAL_MAP,
    DIAGONAL_ROAD,
    DIAGONAL_TARGET,
    GAP_MAP,
    GAP_TARGET,
    ONEWAY_MAP,
    ONEWAY_TARGET,
    ONEWAY_X,
    PARALLEL_MAP,
    PARALLEL_TARGET,
    RESIDENTIAL,
    SPIKE_MAP,
    SPIKE_TARGET,
    SPIKE_X,
    SQUARE_MAP,
    SQUARE_TARGET,
    MeterWay,
    meter_point,
    to_meters,
    write_meter_pbf,
)

NO_OVERRIDES = Overrides(deny=frozenset(), allow=frozenset())
Meters = list[tuple[float, float]]


def build_graph(tmp_path: Path, ways: list[MeterWay]) -> RideGraph:
    src, patched, out = tmp_path / "src.osm.pbf", tmp_path / "patched.osm.pbf", tmp_path / "build"
    write_meter_pbf(src, ways)
    patch_pbf(src, patched, NO_OVERRIDES)
    extract_graph(patched, out)
    return RideGraph.load(out)


def region_around(graph: RideGraph, strokes: list[Meters], margin_m: float = 400.0) -> Region:
    xs = [x for s in strokes for x, _ in s]
    ys = [y for s in strokes for _, y in s]
    low, high = meter_point(min(xs) - margin_m, min(ys) - margin_m), meter_point(max(xs) + margin_m, max(ys) + margin_m)
    return graph.region(*low, *high)


def build_region(tmp_path: Path, ways: list[MeterWay], strokes: list[Meters]) -> Region:
    return region_around(build_graph(tmp_path, ways), strokes)


def run(region: Region, strokes: list[Meters], closed: list[bool], *, budget_s: float = 30.0,
        weights: PlannerWeights = PlannerWeights()) -> list[StrokePlan]:
    lonlat = [[meter_point(x, y) for x, y in s] for s in strokes]
    plans = plan_shape(region, lonlat, closed, deadline=Deadline(60), budget_s=budget_s, weights=weights)
    for plan in plans:
        for piece in [*plan.forward, *(plan.backward or [])]:
            check_piece(region, piece)
    return plans


# ---- 檢查與量測 ----


def _on_edges(region: Region, edges: list[int], point: tuple[float, float]) -> bool:
    """點落在這些邊的形狀上（局部平面 5 cm 內）。"""
    p = np.array(to_meters([point])[0])
    for e in edges:
        shape = np.array(to_meters([tuple(xy) for xy in region.geometry(e).tolist()]))
        a, b = shape[:-1], shape[1:]
        d = b - a
        t = np.clip(np.einsum("ij,ij->i", p - a, d) / np.maximum(np.einsum("ij,ij->i", d, d), 1e-12), 0, 1)
        if np.min(np.hypot(*(p - (a + d * t[:, None])).T)) < 0.05:
            return True
    return False


def check_piece(region: Region, piece: PlannedPiece) -> None:
    """方向合法；前一條邊的終點就是下一條邊的起點；每個點與相鄰兩點的中點都在這段的邊上（沒有跳過空地的直線）。"""
    assert piece.edges and len(piece.points) >= 2
    for e, forward in piece.edges:
        assert region.flags[e] & (FORWARD if forward else BACKWARD), f"邊 {e} 逆向（forward={forward}）"
    for (e0, f0), (e1, f1) in zip(piece.edges, piece.edges[1:]):
        head = region.edge_nodes[e0, 1 if f0 else 0]
        tail = region.edge_nodes[e1, 0 if f1 else 1]
        assert head == tail, f"邊 {e0} 與 {e1} 沒有相連"
    edges = [e for e, _ in piece.edges]
    for p, q in zip(piece.points, piece.points[1:]):
        assert p != q
        middle = ((p[0] + q[0]) / 2, (p[1] + q[1]) / 2)
        assert _on_edges(region, edges, p) and _on_edges(region, edges, middle)
    assert _on_edges(region, edges, piece.points[-1])


def meters(piece: PlannedPiece) -> np.ndarray:
    return np.array(to_meters(piece.points))


def length(points: np.ndarray) -> float:
    return float(np.hypot(*np.diff(points, axis=0).T).sum())


def densify(points: np.ndarray, step: float = 2.0) -> np.ndarray:
    out = [points[0]]
    for a, b in zip(points[:-1], points[1:]):
        count = max(1, math.ceil(math.dist(a, b) / step))
        out.extend(a + (b - a) * k / count for k in range(1, count + 1))
    return np.array(out)


def distance_to(points: np.ndarray, line: Meters) -> np.ndarray:
    """每個點到折線 line 的距離。"""
    poly = np.array(line)
    a, d = poly[:-1], poly[1:] - poly[:-1]
    rel = points[:, None, :] - a[None]
    t = np.clip(np.einsum("nij,ij->ni", rel, d) / np.einsum("ij,ij->i", d, d), 0, 1)
    return np.hypot(*(rel - d[None] * t[..., None]).transpose(2, 0, 1)).min(axis=1)


def turns(points: np.ndarray) -> np.ndarray:
    """路線上的轉彎角度（度，不分左右）：相鄰兩段的夾角，忽略不到 1 m 的線段。"""
    step = np.diff(points, axis=0)
    step = step[np.hypot(*step.T) > 1.0]
    heading = np.arctan2(step[:, 1], step[:, 0])
    return np.degrees(np.abs((np.diff(heading) + np.pi) % (2 * np.pi) - np.pi))


# ---- 場景 ----


def test_parallel_road_is_used_instead_of_detouring(tmp_path: Path) -> None:
    """正下方的路斷掉：走 40 m 外的平行路，不從北邊 300 m 繞出去再繞回來。"""
    region = build_region(tmp_path, PARALLEL_MAP, [PARALLEL_TARGET])
    plan = run(region, [PARALLEL_TARGET], [False])[0]
    assert plan.unplanned == [] and len(plan.forward) == 1
    piece = plan.forward[0]
    assert piece.target_range == (0, len(PARALLEL_TARGET) - 1)
    route = meters(piece)
    assert np.abs(route[:, 1]).max() <= 41.0  # 沒有往北繞
    assert length(route) <= 1600 * 1.08  # 長度接近目標（最多兩次 40 m 的換路）
    assert distance_to(densify(route), PARALLEL_TARGET).max() <= 41.0


def test_corner_takes_the_junction_with_the_closer_angle(tmp_path: Path) -> None:
    """60° 的折角：提早 15 m 的 60° 岔路比正下方 85° 的路口像（後者位置比較近），選岔路。"""
    region = build_region(tmp_path, CORNER_MAP, [CORNER_TARGET])
    plan = run(region, [CORNER_TARGET], [False])[0]
    assert plan.unplanned == [] and len(plan.forward) == 1
    route = meters(plan.forward[0])
    angles = turns(route)
    assert 55 <= angles.max() <= 65 and np.sum(angles > 20) == 1
    assert distance_to(densify(route[route[:, 0] > CORNER_X + 790]), CORNER_FORK).max() < 0.5  # 轉彎之後都在岔路上
    # 沒有走 85° 路口往北的那 20 m（離路口 5 m 以外的部分離路線都超過 3 m）
    stub = densify(np.array(CORNER_NEAR[:2]), step=1.0)
    stub = stub[np.hypot(*(stub - CORNER_NEAR[0]).T) > 5.0]
    assert distance_to(stub, [tuple(p) for p in route]).min() > 3.0


def test_diagonal_follows_the_diagonal_road_not_a_zigzag(tmp_path: Path) -> None:
    """45° 斜線：走一個街廓外的斜路，不走貼著目標的鋸齒。"""
    region = build_region(tmp_path, DIAGONAL_MAP, [DIAGONAL_TARGET])
    plan = run(region, [DIAGONAL_TARGET], [False])[0]
    assert plan.unplanned == [] and len(plan.forward) == 1
    route = densify(meters(plan.forward[0]))
    on_diagonal = distance_to(route, DIAGONAL_ROAD) < 2.0
    assert on_diagonal.mean() >= 0.85
    assert np.sum(turns(meters(plan.forward[0])) > 30) <= 4


def test_planner_never_rides_against_one_way_streets(tmp_path: Path) -> None:
    """oneway=yes（畫成往西）、oneway=-1 的路段（含只有 40 m 的那段）不往東騎，改走平行路；
    oneway:bicycle=no、cycleway=opposite_lane 可以往東騎就照用。

    check_piece 逐邊檢查方向旗標（forward 與 backward 兩個方向的規劃都檢查）。
    """
    region = build_region(tmp_path, ONEWAY_MAP, [ONEWAY_TARGET])
    plan = run(region, [ONEWAY_TARGET], [False])[0]
    assert plan.unplanned == [] and len(plan.forward) == 1
    route = densify(meters(plan.forward[0]))
    x = route[:, 0] - ONEWAY_X
    assert np.all(np.abs(route[(x > 220) & (x < 580), 1] + 40) < 1.0)  # 單行道那兩段：走平行路
    assert np.all(np.abs(route[(x > 620) & (x < 980), 1]) < 1.0)  # 自行車可以雙向的兩段：照目標走
    assert np.all(np.abs(route[(x > 1081) & (x < 1119), 1] + 40) < 1.0)  # 40 m 的短單行道：也繞平行路
    # 反方向（往西）時兩種單行道都順向：直接走目標下方的路
    back = densify(meters(plan.backward[0])) if plan.backward else np.empty((0, 2))
    assert plan.backward_unplanned == [] and len(plan.backward) == 1
    assert np.all(np.abs(back[:, 1]) < 1.0)


def test_planner_does_not_ride_into_a_dead_end_and_back(tmp_path: Path) -> None:
    """目標有一段沿著死巷的折返：寧可不畫那根刺，也不在死巷底迴轉。"""
    region = build_region(tmp_path, SPIKE_MAP, [SPIKE_TARGET])
    plan = run(region, [SPIKE_TARGET], [False])[0]
    assert plan.unplanned == [] and len(plan.forward) == 1
    route = meters(plan.forward[0])
    assert turns(route).max() < 150
    assert route[:, 1].max() < 1.0  # 沒有騎進死巷
    assert route[0, 0] - SPIKE_X < 15 and route[-1, 0] - SPIKE_X > 1185


def test_closed_stroke_ends_where_it_starts(tmp_path: Path) -> None:
    region = build_region(tmp_path, SQUARE_MAP, [SQUARE_TARGET])
    plan = run(region, [SQUARE_TARGET], [True])[0]
    assert plan.backward is None and plan.backward_unplanned is None
    assert plan.unplanned == [] and len(plan.forward) == 1
    piece = plan.forward[0]
    assert piece.points[0] == piece.points[-1]
    start, end = piece.target_range
    assert end - start == len(SQUARE_TARGET)  # 繞一整圈
    route = meters(piece)
    assert length(route) == pytest.approx(3200, rel=0.02)
    assert distance_to(densify(route), [*SQUARE_TARGET, SQUARE_TARGET[0]]).max() <= 15.0
    # 從轉角開始（四個轉角一樣尖）
    assert min(math.dist(route[0], corner) for corner in SQUARE_TARGET) <= 15.0


def test_open_stroke_is_also_planned_backwards(tmp_path: Path) -> None:
    region = build_region(tmp_path, PARALLEL_MAP, [PARALLEL_TARGET])
    plan = run(region, [PARALLEL_TARGET], [False])[0]
    assert plan.backward is not None and len(plan.backward) == 1 and plan.backward_unplanned == []
    back = plan.backward[0]
    assert back.target_range == (len(PARALLEL_TARGET) - 1, 0)
    route = meters(back)
    assert math.dist(route[0], PARALLEL_TARGET[-1]) <= 41.0 and math.dist(route[-1], PARALLEL_TARGET[0]) <= 41.0
    assert np.abs(route[:, 1]).max() <= 41.0


def test_unplannable_stretches_split_the_plan(tmp_path: Path) -> None:
    """500–900 m 沒有路（沒有候選）、1030–1060 m 兩段路不相通（轉移不可達）：斷成三段，中間記成沒規劃。"""
    region = build_region(tmp_path, GAP_MAP, [GAP_TARGET])
    plan = run(region, [GAP_TARGET], [False])[0]
    # 輸入點每 100 m 一個。路在 500 m 結束，候選半徑 70 m：最後一個有候選的取樣點在 500–600 m 之間，900 m 那段路
    # 的第一個候選在 800–900 m 之間。1030 m 那段路的端點在 1100 m 以前的取樣點都還在候選半徑內，斷在 1100 m 附近
    assert [p.target_range for p in plan.forward] == [(0, 6), (8, 11), (11, 14)]
    assert plan.unplanned == [(5, 9), (10, 12)]
    assert [p.target_range for p in plan.backward or []] == [(14, 9), (10, 8), (6, 0)]
    assert plan.backward_unplanned == [(10, 9), (9, 5)]


def test_zero_budget_plans_nothing(tmp_path: Path) -> None:
    region = build_region(tmp_path, PARALLEL_MAP, [PARALLEL_TARGET])
    plan = run(region, [PARALLEL_TARGET, PARALLEL_TARGET[:3]], [False, True], budget_s=0.0)
    assert plan[0] == StrokePlan([], [], [(0, 4)], [(4, 0)])
    assert plan[1] == StrokePlan([], None, [(0, 3)], None)


def test_running_out_of_budget_stops_midway(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """假時鐘每讀一次走 10 ms、預算 0.3 s：規劃到一半停下，剩下的記成沒規劃，不丟例外。"""
    region = build_region(tmp_path, PARALLEL_MAP, [PARALLEL_TARGET])
    clock = iter(np.arange(0.0, 1000.0, 0.01))
    monkeypatch.setattr(plan_module.time, "monotonic", lambda: float(next(clock)))
    plan = run(region, [PARALLEL_TARGET], [False], budget_s=0.3)[0]
    assert len(plan.forward) == 1
    start, end = plan.forward[0].target_range
    assert start == 0 and 0 < end < len(PARALLEL_TARGET) - 1
    (gap_start, gap_end), = plan.unplanned
    assert gap_end == len(PARALLEL_TARGET) - 1 and end - 1 <= gap_start <= end
    assert plan.backward == [] and plan.backward_unplanned == [(len(PARALLEL_TARGET) - 1, 0)]


def test_cancelled_deadline_raises(tmp_path: Path) -> None:
    region = build_region(tmp_path, PARALLEL_MAP, [PARALLEL_TARGET])
    deadline = Deadline(60)
    deadline.cancel()
    with pytest.raises(MatchCancelled):
        plan_shape(region, [[meter_point(*p) for p in PARALLEL_TARGET]], [False], deadline=deadline, budget_s=10)


def test_same_input_gives_the_same_plan(tmp_path: Path) -> None:
    region = build_region(tmp_path, DIAGONAL_MAP, [DIAGONAL_TARGET, SQUARE_TARGET])
    strokes = [DIAGONAL_TARGET, [(x - 50_000 + 20_000 + 300, y + 300) for x, y in SQUARE_TARGET]]
    first = run(region, strokes, [False, True])
    assert first == run(region, strokes, [False, True])
    assert first[1].forward  # 正方形在棋盤上也規劃得出來


def test_region_bounds_cover_candidates_and_transitions() -> None:
    """外框往外擴 R + 4Δ：L = 1600 m 時 R = 80 m、Δ = 16 m，所以 144 m（經度方向在這個緯度上保守一點點）。"""
    lonlat = [[meter_point(x, y) for x, y in PARALLEL_TARGET]]
    min_lon, min_lat, max_lon, max_lat = region_bounds(lonlat)
    (x0, y0), (x1, y1) = to_meters([(min_lon, min_lat), (max_lon, max_lat)])
    assert y0 == pytest.approx(-144.0, abs=0.01) and y1 == pytest.approx(144.0, abs=0.01)
    assert -144.1 <= x0 <= -144.0 and 1744.0 <= x1 <= 1744.1


def test_empty_region_leaves_everything_unplanned(tmp_path: Path) -> None:
    graph = build_graph(tmp_path, [MeterWay(1, [(0.0, 0.0), (100.0, 0.0)], RESIDENTIAL)])
    far = [(90_000.0, 0.0), (91_000.0, 0.0)]
    plan = run(region_around(graph, [far]), [far], [False])[0]
    assert plan == StrokePlan([], [], [(0, 1)], [(1, 0)])


# ---- 效能：約 2 萬節點的棋盤、2 km 大小的網站預設文字 ----

BIG_STEP = 50.0
BIG_SIZE = 140
DEFAULT_TEXT = Path(__file__).resolve().parents[1] / "src" / "engine" / "bench" / "default_text.json"


def big_city() -> list[MeterWay]:
    """140 × 140、50 m 的棋盤，每 10 個街廓一條雙向斜路（經過棋盤節點），每 7 條街一條單行道。"""
    lines = range(BIG_SIZE)
    ways = [MeterWay(100_000 + r, [(c * BIG_STEP, r * BIG_STEP) for c in lines],
                     {**RESIDENTIAL, "oneway": "yes"} if r % 7 == 3 else RESIDENTIAL) for r in lines]
    ways += [MeterWay(200_000 + c, [(c * BIG_STEP, r * BIG_STEP) for r in lines], RESIDENTIAL) for c in lines]
    for k in range(-BIG_SIZE + 10, BIG_SIZE - 10, 10):
        diagonal = [((i + k) * BIG_STEP, i * BIG_STEP) for i in lines if 0 <= i + k < BIG_SIZE]
        ways.append(MeterWay(300_000 + k + BIG_SIZE, diagonal, RESIDENTIAL))
    return ways


def default_text_strokes(size_m: float) -> tuple[list[list[tuple[float, float]]], list[bool]]:
    raw = json.loads(DEFAULT_TEXT.read_text())
    strokes = [NormStroke([tuple(p) for p in s["points"]], s["closed"]) for s in raw["strokes"]]
    center = meter_point(BIG_SIZE * BIG_STEP / 2, BIG_SIZE * BIG_STEP / 2)
    placed = place(strokes, Placement(center, size_m, 0.0))
    return [s.points for s in placed], [s.closed for s in placed]


def test_default_text_on_a_big_grid_plans_within_a_loose_limit(tmp_path: Path) -> None:
    """寬鬆上限（防止爆炸），實際數字印出來：目標 ≤ 1.5 s（P2.5 計畫 B2）。"""
    graph = build_graph(tmp_path, big_city())
    assert len(graph.nodes) > 19_000
    strokes, closed = default_text_strokes(2000.0)
    meters_strokes = [to_meters(s) for s in strokes]
    started = time.perf_counter()
    region = region_around(graph, meters_strokes, margin_m=200.0)
    region_s = time.perf_counter() - started
    started = time.perf_counter()
    plans = plan_shape(region, strokes, closed, deadline=Deadline(60), budget_s=60.0)
    plan_s = time.perf_counter() - started
    planned = sum(1 for p in plans if p.forward)
    print(f"\n預設文字 2 km：region {region_s:.2f} s（{len(region)} 條邊）、規劃 {plan_s:.2f} s、"
          f"{planned}/{len(plans)} 筆有規劃")
    assert plan_s < 10.0
    assert planned == len(plans)
    for plan in plans:
        for piece in [*plan.forward, *(plan.backward or [])]:
            for e, forward in piece.edges:
                assert region.flags[e] & (FORWARD if forward else BACKWARD)
