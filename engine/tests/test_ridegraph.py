"""可騎路網圖：合成地圖先跑 patch_pbf，再對修補後的 pbf 建圖（與建圖流程相同）；讀取端 RideGraph／Region。"""

import json
from pathlib import Path

import numpy as np
import osmium
import pytest
from scipy.sparse.csgraph import dijkstra

from engine.data.bike_patch import Overrides, patch_pbf
from engine.data.build import build_with_patch
from engine.data.checks import graph_checks, validate_build
from engine.data.rideable import is_rideable
from engine.data.ridegraph import MAX_EDGE_M, GraphStats, direction_flags, extract_graph
from engine.planner.graph import (
    BACKWARD,
    FORWARD,
    GRAPH_CELLS,
    GRAPH_COORDS,
    GRAPH_EDGES,
    GRAPH_FILES,
    GRAPH_LENGTHS,
    GRAPH_META,
    GRAPH_NODES,
    GraphDataMissing,
    RideGraph,
    has_graph_data,
)
from engine.routing.geo import haversine_m
from osm_fixture import EXPRESSWAY_MAP, EXPRESSWAY_WAY_ID, PLAIN_TRUNK_WAY_ID, RESIDENTIAL, grid_point, write_grid_pbf

NO_OVERRIDES = Overrides(deny=frozenset(), allow=frozenset())
BOTH = FORWARD | BACKWARD
Way = tuple[int, list[int], dict[str, str]]


def q(point: tuple[float, float]) -> tuple[float, float]:
    """取到 pbf 的精度（1e-7 度）：讀回來的座標才會與寫入的完全相等。"""
    return (round(point[0], 7), round(point[1], 7))


def p(col: float, row: float) -> tuple[float, float]:
    """約 50 m 的網格（grid_point 的 1/4）：邊都短於 MAX_EDGE_M，不會被切開。"""
    return q(grid_point(col / 4, row / 4))


def east(point: tuple[float, float], degrees: float) -> tuple[float, float]:
    return q((point[0] + degrees, point[1]))


def write_pbf(path: Path, nodes: dict[int, tuple[float, float]], ways: list[Way]) -> None:
    """任意節點 id 與座標；way 可以參照不存在的節點。"""
    writer = osmium.SimpleWriter(str(path))
    try:
        for node_id, location in sorted(nodes.items()):
            writer.add_node(osmium.osm.mutable.Node(id=node_id, location=location, version=1))
        for way_id, refs, tags in sorted(ways, key=lambda w: w[0]):
            writer.add_way(osmium.osm.mutable.Way(id=way_id, nodes=refs, tags=tags, version=1))
    finally:
        writer.close()


def build(tmp_path: Path, nodes: dict[int, tuple[float, float]], ways: list[Way]) -> tuple[RideGraph, GraphStats]:
    """寫合成地圖 → patch_pbf → extract_graph → RideGraph.load。"""
    tmp_path.mkdir(parents=True, exist_ok=True)
    src, patched, out = tmp_path / "src.osm.pbf", tmp_path / "patched.osm.pbf", tmp_path / "build"
    write_pbf(src, nodes, ways)
    patch_pbf(src, patched, NO_OVERRIDES)
    out.mkdir()
    stats = extract_graph(patched, out)
    return RideGraph.load(out), stats


def node_xy(graph: RideGraph, index: int) -> tuple[float, float]:
    return (float(graph.nodes[index, 0]), float(graph.nodes[index, 1]))


def edge_set(graph: RideGraph) -> set[tuple[tuple[float, float], tuple[float, float], int, int]]:
    """每條邊：(from 座標, to 座標, 旗標, 形狀點數)；與節點編號無關。"""
    out = set()
    for e in range(len(graph)):
        f, t, _, flags = (int(v) for v in graph.edges[e])
        shape = graph.coords[graph.edges[e, 2]:graph.coord_end(e)]
        out.add((node_xy(graph, f), node_xy(graph, t), flags, len(shape)))
    return out


def shape_of(graph: RideGraph, e: int) -> list[tuple[float, float]]:
    return [(float(x), float(y)) for x, y in graph.coords[graph.edges[e, 2]:graph.coord_end(e)]]


def all_points(graph: RideGraph) -> set[tuple[float, float]]:
    return {(float(x), float(y)) for x, y in graph.coords}


# ---- 方向旗標：Valhalla 3.9.1 graph lua 與 tag 字面意思的交集 ----


@pytest.mark.parametrize(
    "tags, expected",
    [
        ({"highway": "residential"}, BOTH),
        ({"highway": "residential", "oneway": "yes"}, FORWARD),
        ({"highway": "residential", "oneway": "true"}, FORWARD),
        ({"highway": "residential", "oneway": "1"}, FORWARD),
        ({"highway": "residential", "oneway": "-1"}, BACKWARD),
        ({"highway": "residential", "oneway": "reverse"}, BACKWARD),  # lua 不認得（當雙向），tag 字面是逆向：保守取逆向
        ({"highway": "residential", "oneway": "no"}, BOTH),
        ({"highway": "residential", "oneway": "reversible"}, BOTH),
        ({"highway": "residential", "junction": "roundabout"}, FORWARD),
        ({"highway": "residential", "junction": "circular"}, FORWARD),
        # lua 不管 oneway=no，圓環一律單向：保守取順向
        ({"highway": "residential", "junction": "roundabout", "oneway": "no"}, FORWARD),
        ({"highway": "residential", "oneway": "yes", "oneway:bicycle": "no"}, BOTH),
        # lua 只在 oneway 剛好是 yes 時看 oneway:bicycle=no：oneway=true 時 Valhalla 照樣單向
        ({"highway": "residential", "oneway": "true", "oneway:bicycle": "no"}, FORWARD),
        ({"highway": "residential", "oneway:bicycle": "yes"}, FORWARD),
        ({"highway": "residential", "oneway:bicycle": "-1"}, BACKWARD),
        ({"highway": "residential", "oneway": "yes", "oneway:bicycle": "-1"}, BACKWARD),
        ({"highway": "residential", "oneway": "-1", "oneway:bicycle": "-1"}, 0),  # lua 翻兩次變順向，字面是逆向：沒有交集
        ({"highway": "residential", "oneway": "yes", "cycleway": "opposite"}, BOTH),
        ({"highway": "residential", "oneway": "yes", "cycleway:left": "opposite_lane"}, BOTH),
        ({"highway": "residential", "oneway": "yes", "cycleway:right": "opposite_track"}, BOTH),
        ({"highway": "residential", "oneway": "yes", "cycleway:both": "opposite_lane"}, BOTH),
        ({"highway": "residential", "oneway": "yes", "cycleway:both": "opposite"}, FORWARD),  # lua 的 cycleway:both 表沒有 opposite
        ({"highway": "residential", "oneway": "-1", "cycleway": "opposite"}, BOTH),
        ({"highway": "residential", "oneway": "yes", "oneway:bicycle": "yes", "cycleway": "opposite"}, 0),
        ({"highway": "residential", "bicycle:backward": "no"}, FORWARD),
        ({"highway": "residential", "oneway": "yes", "bicycle:backward": "yes"}, FORWARD),  # 字面仍是單行道
        ({"highway": "residential", "vehicle:forward": "no"}, BACKWARD),
        # 沒有 bicycle tag、只有 cycleway=opposite 的人行道：lua 只經 bike_reverse 放行逆向（但 is_rideable 判為不可騎，不進圖）
        ({"highway": "footway", "cycleway": "opposite"}, BACKWARD),
        ({"highway": "footway", "bicycle": "dismount", "cycleway": "opposite"}, BOTH),
        ({"highway": "footway", "bicycle": "dismount"}, BOTH),
    ],
)
def test_direction_flags(tags: dict[str, str], expected: int) -> None:
    assert direction_flags(tags) == expected


def test_opposite_footway_without_bicycle_tag_is_not_rideable() -> None:
    """只放行逆向的人行道以 is_rideable 為準：不可騎，所以不在圖裡（下面的萃取測試也驗）。"""
    assert not is_rideable({"highway": "footway", "cycleway": "opposite"})


# ---- 拓樸：節點與邊 ----


def test_shared_nodes_split_edges_and_unshared_middle_nodes_do_not(tmp_path: Path) -> None:
    nodes = {1: p(0, 0), 2: p(1, 0), 3: p(2, 0), 4: p(3, 0), 5: p(1, 1), 6: p(1, 2), 7: p(2, 1)}
    graph, stats = build(tmp_path, nodes, [(10, [1, 2, 3, 4], RESIDENTIAL), (11, [2, 5, 6], RESIDENTIAL), (12, [5, 7], RESIDENTIAL)])
    assert stats.ways == 3 and stats.nodes == 6 and stats.edges == 5 and stats.coords == 11
    assert len(graph) == 5 and len(graph.nodes) == 6 and len(graph.coords) == 11
    assert {node_xy(graph, i) for i in range(6)} == {nodes[i] for i in (1, 2, 4, 5, 6, 7)}  # 3 沒有被共用：不是節點
    assert edge_set(graph) == {
        (nodes[1], nodes[2], BOTH, 2),
        (nodes[2], nodes[4], BOTH, 3),
        (nodes[2], nodes[5], BOTH, 2),
        (nodes[5], nodes[6], BOTH, 2),
        (nodes[5], nodes[7], BOTH, 2),
    }
    long_edge = next(e for e in range(len(graph)) if len(shape_of(graph, e)) == 3)
    assert shape_of(graph, long_edge) == [nodes[2], nodes[3], nodes[4]]
    assert graph.lengths[long_edge] == pytest.approx(haversine_m(nodes[2], nodes[3]) + haversine_m(nodes[3], nodes[4]), rel=1e-6)


def test_way_passing_a_node_twice_splits_there(tmp_path: Path) -> None:
    nodes = {1: p(0, 0), 2: p(1, 0), 3: p(2, 0), 4: p(2, 1), 5: p(0, 1)}
    graph, _ = build(tmp_path, nodes, [(10, [1, 2, 3, 4, 2, 5], RESIDENTIAL)])
    assert edge_set(graph) == {
        (nodes[1], nodes[2], BOTH, 2),
        (nodes[2], nodes[2], BOTH, 4),  # 2 → 3 → 4 → 2 的自環
        (nodes[2], nodes[5], BOTH, 2),
    }


def test_closed_way_is_one_loop_edge_and_repeated_refs_collapse(tmp_path: Path) -> None:
    nodes = {1: p(0, 0), 2: p(1, 0), 3: p(1, 1), 11: p(5, 0), 12: p(6, 0), 13: p(7, 0)}
    graph, _ = build(tmp_path, nodes, [(10, [1, 2, 3, 1], RESIDENTIAL), (20, [11, 12, 12, 13], RESIDENTIAL)])
    assert edge_set(graph) == {(nodes[1], nodes[1], BOTH, 4), (nodes[11], nodes[13], BOTH, 3)}


def test_missing_node_breaks_the_way_into_unconnected_pieces(tmp_path: Path) -> None:
    nodes = {1: p(0, 0), 2: p(1, 0), 3: p(2, 0), 4: p(3, 0), 11: p(0, 2), 12: p(1, 2), 13: p(2, 2)}
    graph, stats = build(tmp_path, nodes, [(10, [1, 2, 999, 3, 4], RESIDENTIAL), (20, [11, 998, 12, 13], RESIDENTIAL)])
    assert edge_set(graph) == {(nodes[1], nodes[2], BOTH, 2), (nodes[3], nodes[4], BOTH, 2), (nodes[12], nodes[13], BOTH, 2)}
    assert stats.ways == 2
    # 斷開的兩段不共用節點：各自的兩端是 4 個不同的節點
    assert len(graph.nodes) == 6 and len(set(graph.edges[:, :2].ravel().tolist())) == 6


def on_line(points: np.ndarray, a: tuple[float, float], b: tuple[float, float]) -> np.ndarray:
    """點落在 a–b 線段上（經緯度平面，1e-9 度內）。"""
    d, r = np.subtract(b, a), points - a
    t = r @ d / (d @ d)
    return (np.abs(r[:, 0] * d[1] - r[:, 1] * d[0]) < 1e-9) & (t >= -1e-9) & (t <= 1 + 1e-9)


def test_long_edges_are_split_into_short_connected_edges(tmp_path: Path) -> None:
    a, b, c = q(grid_point(0, 0)), q(grid_point(9, 4)), q(grid_point(9.5, 4))  # a–b 約 2 km 的斜線，只有兩個節點
    graph, _ = build(tmp_path, {1: a, 2: b, 3: c}, [(10, [1, 2, 3], RESIDENTIAL)])
    total = haversine_m(a, b) + haversine_m(b, c)
    assert len(graph) >= int(total // MAX_EDGE_M) + 1
    assert graph.lengths.max() <= MAX_EDGE_M
    assert float(graph.lengths.sum()) == pytest.approx(total, rel=1e-4)
    # 依離起點的距離串起來：前一條的終點就是下一條的起點，從 a 接到 c，而且原本的轉折點 b 還在形狀裡
    order = sorted(range(len(graph)), key=lambda e: haversine_m(a, node_xy(graph, int(graph.edges[e, 0]))))
    assert node_xy(graph, int(graph.edges[order[0], 0])) == a and node_xy(graph, int(graph.edges[order[-1], 1])) == c
    assert all(graph.edges[x, 1] == graph.edges[y, 0] for x, y in zip(order, order[1:]))
    assert b in all_points(graph)
    for e in range(len(graph)):  # 每條邊的形狀都在原本的折線上，沒有零長度的相鄰點
        shape = np.array(shape_of(graph, e))
        assert np.all(np.any(shape[1:] != shape[:-1], axis=1))
        assert np.all(on_line(shape, a, b) | on_line(shape, b, c))


def test_forbidden_ways_are_not_in_the_graph(tmp_path: Path) -> None:
    """樓梯、motorway、bicycle=no、沒有放行自行車的人行道、只放行逆向的人行道都不在圖裡；快速道路由 patch 標成禁行。"""
    nodes = {i: p(i, 0) for i in range(1, 9)} | {20 + i: p(i, 3) for i in range(1, 9)}
    ways: list[Way] = [
        (1, [1, 2], RESIDENTIAL),
        (2, [2, 3], {"highway": "steps"}),
        (3, [3, 4], {"highway": "motorway"}),
        (4, [4, 5], {"highway": "residential", "bicycle": "no"}),
        (5, [5, 6], {"highway": "footway", "footway": "sidewalk"}),
        (6, [6, 7], {"highway": "footway", "cycleway": "opposite"}),
        (7, [7, 8], {"highway": "trunk", "ref": "台64"}),
        (8, [21, 22], {"highway": "footway", "footway": "sidewalk", "bicycle": "yes"}),  # 放行自行車的人行道：修補成 dismount，仍可騎
        (9, [22, 23], {"highway": "trunk", "ref": "台1"}),
    ]
    graph, stats = build(tmp_path, nodes, ways)
    assert stats.ways == 3
    assert edge_set(graph) == {(nodes[1], nodes[2], BOTH, 2), (nodes[21], nodes[22], BOTH, 2), (nodes[22], nodes[23], BOTH, 2)}


def test_expressway_is_in_the_graph_only_without_the_patch(tmp_path: Path) -> None:
    """對照組：排除快速道路的是 patch 標的 bicycle=no（同一份修補後 pbf），不是建圖另外判斷。"""
    src = tmp_path / "src.osm.pbf"
    write_grid_pbf(src, EXPRESSWAY_MAP)
    expressway = next(w for w in EXPRESSWAY_MAP if w.id == EXPRESSWAY_WAY_ID)
    trunk = next(w for w in EXPRESSWAY_MAP if w.id == PLAIN_TRUNK_WAY_ID)
    for name, patched, has_expressway in (("patched", True, False), ("raw", False, True)):
        pbf = src
        if patched:
            pbf = tmp_path / "patched.osm.pbf"
            patch_pbf(src, pbf, NO_OVERRIDES)
        out = tmp_path / name
        out.mkdir()
        extract_graph(pbf, out)
        points = all_points(RideGraph.load(out))
        assert (q(grid_point(*expressway.nodes[1])) in points) is has_expressway
        assert q(grid_point(*trunk.nodes[1])) in points


def test_written_edges_carry_the_way_direction(tmp_path: Path) -> None:
    nodes = {i: p(i, 0) for i in range(1, 7)}
    ways: list[Way] = [
        (1, [1, 2], {**RESIDENTIAL, "oneway": "yes"}),
        (2, [3, 2], {**RESIDENTIAL, "oneway": "-1"}),
        (3, [3, 4], {**RESIDENTIAL, "oneway": "yes", "oneway:bicycle": "no"}),
        (4, [5, 4], {**RESIDENTIAL, "oneway": "yes", "cycleway": "opposite"}),
        (5, [5, 6], {**RESIDENTIAL, "junction": "roundabout"}),
    ]
    graph, _ = build(tmp_path, nodes, ways)
    assert edge_set(graph) == {
        (nodes[1], nodes[2], FORWARD, 2),
        (nodes[3], nodes[2], BACKWARD, 2),
        (nodes[3], nodes[4], BOTH, 2),
        (nodes[5], nodes[4], BOTH, 2),
        (nodes[5], nodes[6], FORWARD, 2),
    }


def test_ways_with_no_rideable_direction_are_dropped(tmp_path: Path) -> None:
    nodes = {1: p(0, 0), 2: p(1, 0), 3: p(2, 0)}
    graph, stats = build(tmp_path, nodes, [(1, [1, 2], RESIDENTIAL), (2, [2, 3], {**RESIDENTIAL, "oneway": "-1", "oneway:bicycle": "-1"})])
    assert edge_set(graph) == {(nodes[1], nodes[2], BOTH, 2)} and stats.ways == 1


def test_files_are_cell_sorted_and_geometry_follows_the_edges(tmp_path: Path) -> None:
    # 兩群相距 0.05 度的路：分在不同格子，第二群的 way id 比較小，確定是依格子而不是依讀入順序排
    far = 0.05
    nodes = {1: p(0, 0), 2: p(1, 0), 3: p(1, 1)} | {11: east(p(0, 0), far), 12: east(p(1, 0), far)}
    graph, stats = build(tmp_path, nodes, [(5, [11, 12], RESIDENTIAL), (6, [1, 2, 3], RESIDENTIAL)])
    out = tmp_path / "build"
    mid = (graph.nodes[graph.edges[:, 0]] + graph.nodes[graph.edges[:, 1]]) / 2
    cell = np.floor((mid[:, 1] - 21.8) / 0.01).astype(int) * 400 + np.floor((mid[:, 0] - 118.1) / 0.01).astype(int)
    assert np.array_equal(cell, np.repeat(np.arange(400 * 460), np.diff(graph.cells)))
    assert np.all(np.diff(graph.edges[:, 2]) >= 2) and graph.edges[0, 2] == 0
    for e in range(len(graph)):  # 形狀的兩端就是 from／to 節點
        shape = shape_of(graph, e)
        assert shape[0] == node_xy(graph, int(graph.edges[e, 0])) and shape[-1] == node_xy(graph, int(graph.edges[e, 1]))
    meta = json.loads((out / GRAPH_META).read_text())
    assert (meta["nodes"], meta["edges"], meta["coords"]) == (len(graph.nodes), len(graph), len(graph.coords))
    assert stats.bytes == sum((out / name).stat().st_size for name in GRAPH_FILES)
    assert not list(out.glob("*.tmp"))


def test_edges_outside_the_taiwan_grid_are_dropped_with_their_nodes(tmp_path: Path) -> None:
    nodes = {1: p(0, 0), 2: p(1, 0), 11: (125.5, 25.05), 12: (125.501, 25.05)}
    graph, _ = build(tmp_path, nodes, [(1, [1, 2], RESIDENTIAL), (2, [11, 12], RESIDENTIAL)])
    assert edge_set(graph) == {(nodes[1], nodes[2], BOTH, 2)} and len(graph.nodes) == 2


# ---- 讀取 ----


def test_load_uses_mmap_and_missing_files_raise(tmp_path: Path) -> None:
    graph, _ = build(tmp_path, {1: p(0, 0), 2: p(1, 0)}, [(1, [1, 2], RESIDENTIAL)])
    out = tmp_path / "build"
    assert has_graph_data(out)
    assert all(isinstance(a, np.memmap) for a in (graph.nodes, graph.edges, graph.lengths, graph.coords, graph.cells))
    (out / GRAPH_LENGTHS).unlink()
    assert not has_graph_data(out)
    with pytest.raises(GraphDataMissing):
        RideGraph.load(out)
    with pytest.raises(GraphDataMissing):
        RideGraph.load(tmp_path / "nowhere")


def test_region_returns_only_edges_in_nearby_cells(tmp_path: Path) -> None:
    far = 0.05  # 約 5 km：不同格子，也遠超過往外擴的範圍
    nodes = {1: p(0, 0), 2: p(1, 0), 3: p(1, 1), 11: east(p(0, 0), far), 12: east(p(1, 0), far)}
    graph, _ = build(tmp_path, nodes, [(1, [1, 2, 3], RESIDENTIAL), (2, [11, 12], RESIDENTIAL)])
    region = graph.region(*p(0, 0), *p(1, 1))
    assert len(region) == 1 and len(region.nodes) == 2
    assert {tuple(xy) for xy in region.nodes.tolist()} == {nodes[1], nodes[3]}
    assert region.geometry(0).tolist() == [list(nodes[1]), list(nodes[2]), list(nodes[3])]
    assert region.geometry(0, forward=False).tolist() == [list(nodes[3]), list(nodes[2]), list(nodes[1])]
    assert graph.region(nodes[11][0] - 1e-4, nodes[11][1] - 1e-4, nodes[12][0], nodes[12][1] + 1e-4).edge_ids.size == 1
    empty = graph.region(120.0, 23.0, 120.001, 23.001)
    assert len(empty) == 0 and len(empty.nodes) == 0 and empty.csr.shape == (0, 0)


def test_region_includes_edges_whose_midpoint_is_in_the_next_cell(tmp_path: Path) -> None:
    """邊歸在中點的格子：範圍只碰到邊的一端時，要靠往外擴才不會漏掉（拿掉擴張這個測試會紅）。"""
    a, b = (121.5095, 25.055), (121.5113, 25.055)  # 約 180 m，跨過 121.51 的格線：a 在第 340 欄，中點在第 341 欄
    graph, _ = build(tmp_path, {1: a, 2: b}, [(1, [1, 2], RESIDENTIAL)])
    assert graph.lengths[0] < MAX_EDGE_M
    region = graph.region(121.5090, 25.0545, 121.5097, 25.0555)
    assert len(region) == 1


def test_region_csr_follows_directions_and_maps_back_to_edges(tmp_path: Path) -> None:
    # A ─ B → C ← D：A–B 雙向；B→C 是 oneway=yes；way 由 C 畫到 D、oneway=-1，所以只能 D → C
    nodes = {1: p(0, 0), 2: p(1, 0), 3: p(2, 0), 4: p(3, 0)}
    ways: list[Way] = [
        (1, [1, 2], RESIDENTIAL),
        (2, [2, 3], {**RESIDENTIAL, "oneway": "yes"}),  # 只能 B → C
        (3, [3, 4], {**RESIDENTIAL, "oneway": "-1"}),  # 只能 D → C
    ]
    graph, _ = build(tmp_path, nodes, ways)
    region = graph.region(*p(0, -1), *p(3, 1))
    index = {tuple(xy): i for i, xy in enumerate(region.nodes.tolist())}
    a, b, c, d = (index[nodes[i]] for i in (1, 2, 3, 4))
    dense = region.csr.toarray()
    assert dense[a, b] > 0 and dense[b, a] > 0
    assert dense[b, c] > 0 and dense[c, b] == 0
    assert dense[d, c] > 0 and dense[c, d] == 0
    assert region.csr.nnz == 4
    assert np.array_equal(region.csr.indices, region.csr_keys % len(region.nodes))
    edge, forward = region.edge_between([b, d], [c, c])
    assert region.geometry(int(edge[0]), bool(forward[0])).tolist() == [list(nodes[2]), list(nodes[3])]
    assert region.geometry(int(edge[1]), bool(forward[1])).tolist() == [list(nodes[4]), list(nodes[3])]
    assert forward.tolist() == [True, False]
    assert dense[a, b] == pytest.approx(region.lengths[region.edge_between([a], [b])[0][0]])
    with pytest.raises(KeyError):
        region.edge_between([c], [b])
    distance = dijkstra(region.csr, indices=[a, d])
    assert np.isfinite(distance[0, c]) and np.isinf(distance[0, d])  # 從 A 到不了 D：C → D 是逆向
    assert np.isinf(distance[1, a])  # D → C 之後 C → B 也是逆向
    assert distance[1, c] == pytest.approx(haversine_m(nodes[3], nodes[4]), rel=1e-6)


def test_region_keeps_parallel_edges_and_csr_points_to_the_shorter_one(tmp_path: Path) -> None:
    nodes = {1: p(0, 0), 2: p(2, 0), 3: p(1, 1)}
    graph, _ = build(tmp_path, nodes, [(1, [1, 2], RESIDENTIAL), (2, [1, 3, 2], RESIDENTIAL)])
    region = graph.region(*p(0, 0), *p(2, 1))
    assert len(region) == 2 and len(region.nodes) == 2  # 兩條平行邊都在區域裡
    index = {tuple(xy): i for i, xy in enumerate(region.nodes.tolist())}
    u, v = index[nodes[1]], index[nodes[2]]
    assert region.csr.nnz == 2
    straight = haversine_m(nodes[1], nodes[2])
    for tail, head, along in ((u, v, True), (v, u, False)):
        edge, forward = region.edge_between([tail], [head])
        assert region.lengths[edge[0]] == pytest.approx(straight, rel=1e-6) and bool(forward[0]) is along
        assert region.csr[tail, head] == pytest.approx(straight, rel=1e-6)
    bent = next(i for i in range(2) if len(region.geometry(i)) == 3)
    assert region.geometry(bent).tolist()[1] == list(nodes[3])


def test_region_leaves_self_loops_out_of_the_csr(tmp_path: Path) -> None:
    nodes = {1: p(0, 0), 2: p(1, 0), 3: p(1, 1), 4: p(-1, 0)}
    graph, _ = build(tmp_path, nodes, [(1, [1, 2, 3, 1], RESIDENTIAL), (2, [4, 1], RESIDENTIAL)])
    region = graph.region(*p(-1, 0), *p(1, 1))
    assert len(region) == 2 and region.csr.nnz == 2  # 只有 4 ↔ 1


# ---- 建圖驗證：檔案都在、筆數 > 0、索引一致 ----


def small_build(tmp_path: Path) -> Path:
    nodes = {1: p(0, 0), 2: p(1, 0), 3: p(2, 0), 4: p(1, 1)}
    build(tmp_path, nodes, [(1, [1, 2, 3], RESIDENTIAL), (2, [2, 4], {**RESIDENTIAL, "oneway": "yes"})])
    return tmp_path / "build"


def rewrite(path: Path, change) -> None:
    array = np.load(path)
    change(array)
    np.save(path, array)


def test_graph_checks_pass_on_a_good_build(tmp_path: Path) -> None:
    assert graph_checks(small_build(tmp_path)) == []


@pytest.mark.parametrize("name", GRAPH_FILES)
def test_graph_checks_fail_when_a_file_is_missing(tmp_path: Path, name: str) -> None:
    out = small_build(tmp_path)
    (out / name).unlink()
    failures = graph_checks(out)
    assert len(failures) == 1 and "可騎路網圖讀不到" in failures[0]


@pytest.mark.parametrize("name", [GRAPH_NODES, GRAPH_EDGES, GRAPH_COORDS, GRAPH_CELLS])
def test_graph_checks_fail_on_truncated_files(tmp_path: Path, name: str) -> None:
    out = small_build(tmp_path)
    data = (out / name).read_bytes()
    (out / name).write_bytes(data[: len(data) - 16])
    failures = graph_checks(out)
    assert len(failures) == 1 and "可騎路網圖讀不到" in failures[0]


def test_graph_checks_fail_when_there_are_no_edges(tmp_path: Path) -> None:
    build(tmp_path, {1: p(0, 0), 2: p(1, 0)}, [(1, [1, 2], {"highway": "motorway"})])
    failures = graph_checks(tmp_path / "build")
    assert len(failures) == 1 and "沒有任何邊" in failures[0]


def _set_from(edges: np.ndarray) -> None:
    edges[0, 0] = 4  # 只有 4 個節點


def _set_negative_to(edges: np.ndarray) -> None:
    edges[1, 1] = -1


def _swap_offsets(edges: np.ndarray) -> None:
    edges[[1, 2], 2] = edges[[2, 1], 2]


def _offset_past_coords(edges: np.ndarray) -> None:
    edges[-1, 2] = 1000


def _zero_flags(edges: np.ndarray) -> None:
    edges[0, 3] = 0


def _unknown_flags(edges: np.ndarray) -> None:
    edges[0, 3] = 4


@pytest.mark.parametrize("change", [_set_from, _set_negative_to, _swap_offsets, _offset_past_coords, _zero_flags, _unknown_flags])
def test_graph_checks_catch_inconsistent_edges(tmp_path: Path, change) -> None:
    out = small_build(tmp_path)
    rewrite(out / GRAPH_EDGES, change)
    failures = graph_checks(out)
    assert len(failures) == 1 and "可騎路網圖的索引不一致" in failures[0]


def test_graph_checks_catch_bad_cells_and_lengths(tmp_path: Path) -> None:
    out = small_build(tmp_path)
    rewrite(out / GRAPH_CELLS, lambda cells: cells.__setitem__(-1, cells[-1] - 1))
    assert any("可騎路網圖讀不到" in f for f in graph_checks(out))
    out = small_build(tmp_path / "lengths")
    rewrite(out / GRAPH_LENGTHS, lambda lengths: lengths.__setitem__(0, np.nan))
    assert any("可騎路網圖的索引不一致" in f for f in graph_checks(out))


def test_graph_checks_catch_count_mismatch_with_meta(tmp_path: Path) -> None:
    out = small_build(tmp_path)
    meta = json.loads((out / GRAPH_META).read_text())
    (out / GRAPH_META).write_text(json.dumps({**meta, "coords": meta["coords"] + 1}))
    assert any("可騎路網圖讀不到" in f for f in graph_checks(out))


@pytest.mark.valhalla
def test_build_with_patch_writes_graph_and_validate_build_checks_it(tmp_path: Path) -> None:
    src = tmp_path / "src.osm.pbf"
    write_grid_pbf(src, EXPRESSWAY_MAP)
    build_dir = tmp_path / "build"
    build_dir.mkdir()
    build_with_patch(src, build_dir)
    graph = RideGraph.load(build_dir)
    points = all_points(graph)
    expressway = next(w for w in EXPRESSWAY_MAP if w.id == EXPRESSWAY_WAY_ID)
    assert q(grid_point(*expressway.nodes[2])) not in points and len(graph) > 0
    assert not any("路網圖" in f for f in validate_build(build_dir))

    (build_dir / GRAPH_COORDS).unlink()
    assert any("可騎路網圖讀不到" in f for f in validate_build(build_dir))
