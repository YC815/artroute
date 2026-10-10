import json
from pathlib import Path

import osmium
import pytest

from engine.data.bike_patch import Overrides, is_expressway, load_overrides, patch_pbf
from engine.data.build import DENIED_FILE, build_with_patch, load_denied
from engine.routing.router import Router, RoutingError
from engine.routing.tiles import build_tiles, config_path
from osm_fixture import (
    EXPRESSWAY_MAP,
    EXPRESSWAY_ONLY_MAP,
    EXPRESSWAY_ONLY_WAY_ID,
    EXPRESSWAY_WAY_ID,
    GridWay,
    PLAIN_TRUNK_WAY_ID,
    X0,
    X1,
    grid_point,
    write_grid_pbf,
)


@pytest.mark.parametrize(
    "tags, expected",
    [
        ({"highway": "trunk", "ref": "台64"}, True),
        ({"highway": "trunk", "ref": "台1;台64"}, True),
        ({"highway": "trunk_link", "name": "洲美快速道路"}, True),
        ({"highway": "trunk", "name": "市民大道高架"}, True),
        ({"highway": "trunk", "motorroad": "yes"}, True),
        ({"highway": "trunk", "ref": "台1"}, False),
        ({"highway": "primary", "name": "某某快速道路"}, False),
        ({"highway": "residential"}, False),
    ],
)
def test_is_expressway(tags: dict[str, str], expected: bool) -> None:
    assert is_expressway(tags) is expected


def test_load_overrides(tmp_path: Path) -> None:
    path = tmp_path / "o.toml"
    path.write_text("deny = [1, 2]\nallow = [3]\n")
    assert load_overrides(path) == Overrides(deny=frozenset({1, 2}), allow=frozenset({3}))


def test_patch_marks_expressway_and_respects_overrides(tmp_path: Path) -> None:
    src = tmp_path / "src.osm.pbf"
    write_grid_pbf(src, EXPRESSWAY_MAP)
    denied = patch_pbf(src, tmp_path / "out.osm.pbf", Overrides(deny=frozenset({211}), allow=frozenset()))
    assert {d.id for d in denied} == {EXPRESSWAY_WAY_ID, 211}
    expressway = next(d for d in denied if d.id == EXPRESSWAY_WAY_ID)
    assert expressway.ref == "台64"
    assert expressway.first == pytest.approx(grid_point(X0, 1))
    assert expressway.last == pytest.approx(grid_point(X0 + 4, 1))

    allowed = patch_pbf(src, tmp_path / "out2.osm.pbf", Overrides(deny=frozenset(), allow=frozenset({EXPRESSWAY_WAY_ID})))
    assert allowed == []


@pytest.mark.valhalla
def test_built_tiles_keep_bicycles_off_expressway_but_on_plain_trunk(tmp_path: Path) -> None:
    src = tmp_path / "src.osm.pbf"
    write_grid_pbf(src, EXPRESSWAY_MAP)
    build_dir = tmp_path / "build"
    build_dir.mkdir()
    config = config_path(build_dir)
    assert build_with_patch(src, build_dir) == config
    assert not list(build_dir.glob("*.osm.pbf"))  # 修補用的暫存 pbf 已刪除
    assert [d.id for d in load_denied(build_dir)] == [EXPRESSWAY_WAY_ID]
    assert json.loads((build_dir / DENIED_FILE).read_text())[0]["id"] == EXPRESSWAY_WAY_ID

    # 對照組：同一張圖不修補直接建，路線必須走快速道路；否則下面「修補後不走」的斷言在 patch 沒作用時也會過
    control = Router(build_tiles(src, tmp_path / "control"))
    control_route = control.route(grid_point(X0, 1), grid_point(X0 + 4, 1))
    assert EXPRESSWAY_WAY_ID in {e.way_id for e in control.edges(control_route.points)}

    router = Router(config)
    along = router.route(grid_point(X0, 1), grid_point(X0 + 4, 1))
    assert EXPRESSWAY_WAY_ID not in {e.way_id for e in router.edges(along.points)}
    assert along.length_km > 1.0  # 必須走下面的住宅街繞路
    assert along.length_km > control_route.length_km

    up = router.route(grid_point(X0 + 4, 1), grid_point(X0 + 4, 3))
    assert PLAIN_TRUNK_WAY_ID in {e.way_id for e in router.edges(up.points)}


@pytest.mark.valhalla
def test_expressway_only_path_becomes_unroutable_after_patch(tmp_path: Path) -> None:
    src = tmp_path / "src.osm.pbf"
    write_grid_pbf(src, EXPRESSWAY_ONLY_MAP)
    start, end = grid_point(X1, 1), grid_point(X1 + 4, 1)

    control = Router(build_tiles(src, tmp_path / "control"))
    control_route = control.route(start, end)
    assert {e.way_id for e in control.edges(control_route.points)} == {EXPRESSWAY_ONLY_WAY_ID}

    patched_dir = tmp_path / "patched"
    patched_dir.mkdir()
    router = Router(build_with_patch(src, patched_dir))
    with pytest.raises(RoutingError):
        router.route(start, end)


# ---- 加固：其他自行車 tag、缺節點、空 way、relation 與 node 原樣通過、allow 優先 ----

Tags = dict[str, str]


def read_pbf(path: Path) -> tuple[dict, dict, dict]:
    """讀回 pbf 的 node／way／relation（含 tag、座標、節點清單、成員與 role），用來比對修補前後。"""
    nodes, ways, relations = {}, {}, {}
    for obj in osmium.FileProcessor(str(path)):
        tags = {tag.k: tag.v for tag in obj.tags}
        if obj.is_node():
            nodes[obj.id] = (tags, (obj.location.lon, obj.location.lat))
        elif obj.is_way():
            ways[obj.id] = (tags, [node.ref for node in obj.nodes])
        elif obj.is_relation():
            relations[obj.id] = (tags, [(m.type, m.ref, m.role) for m in obj.members])
    return nodes, ways, relations


def write_raw_pbf(path: Path, nodes: list, ways: list, relations: list | None = None) -> None:
    """直接寫 osmium 物件（可寫出缺節點、空 way 這類 write_grid_pbf 寫不出的壞資料）。"""
    writer = osmium.SimpleWriter(str(path), overwrite=True)
    try:
        for node in nodes:
            writer.add_node(node)
        for way in ways:
            writer.add_way(way)
        for relation in relations or []:
            writer.add_relation(relation)
    finally:
        writer.close()


def node(node_id: int, col: float, tags: Tags | None = None) -> osmium.osm.mutable.Node:
    return osmium.osm.mutable.Node(id=node_id, location=grid_point(col, 1), tags=tags or {}, version=1)


def way(way_id: int, nodes: list[int], tags: Tags) -> osmium.osm.mutable.Way:
    return osmium.osm.mutable.Way(id=way_id, nodes=nodes, tags=tags, version=1)


EXPRESSWAY_TAGS = {"highway": "trunk", "ref": "台64"}


def test_patch_strips_other_bicycle_tags_and_keeps_the_rest(tmp_path: Path) -> None:
    src, dst = tmp_path / "src.osm.pbf", tmp_path / "out.osm.pbf"
    tags = {
        **EXPRESSWAY_TAGS,
        "name": "八里新店線",
        "maxspeed": "90",
        "bicycle": "yes",
        "bicycle:forward": "yes",
        "bicycle:conditional": "yes @ (Sa,Su)",
        "cycleway": "lane",
        "cycleway:both": "track",
        "cycleway:right:lane": "exclusive",
        "oneway": "yes",
        "oneway:bicycle": "no",
        "bicycle_road": "yes",
        "cycle_network": "TW:NCN",
        "vehicle:forward": "yes",
        "vehicle:backward": "yes",
        "vehicle": "yes",
    }
    write_raw_pbf(src, [node(1, 0), node(2, 1)], [way(500, [1, 2], tags)])
    patch_pbf(src, dst, Overrides(deny=frozenset(), allow=frozenset()))
    _, ways, _ = read_pbf(dst)
    # bicycle_road、cycle_network 只是名字長得像，不是 Valhalla 讀的自行車通行 tag，必須留著
    assert ways[500][0] == {
        **EXPRESSWAY_TAGS,
        "name": "八里新店線",
        "maxspeed": "90",
        "oneway": "yes",
        "bicycle_road": "yes",
        "cycle_network": "TW:NCN",
        "vehicle": "yes",
        "bicycle": "no",
    }


@pytest.mark.valhalla
@pytest.mark.parametrize(
    "extra",
    [
        {"bicycle:forward": "yes", "bicycle:backward": "yes"},
        {"cycleway:both": "track"},
        {"cycleway:right": "lane", "cycleway:left": "lane"},
        {"cycleway": "lane"},
        {"bicycle": "designated"},
        {"bicycle": "yes", "bicycle:conditional": "yes @ (Sa,Su)"},
        {"oneway": "yes", "oneway:bicycle": "no"},
        # Valhalla lua：bk_forward = bicycle:forward or vehicle:forward，會蓋過 bicycle=no
        {"vehicle:forward": "yes"},
        {"vehicle:backward": "yes"},
        {"oneway": "yes", "vehicle:forward": "yes"},
        {"oneway": "-1", "vehicle:backward": "yes"},
        # 實測不會重新放行（Valhalla 不讀這些），留作回歸護欄
        {"access:forward": "yes"},
        {"access:backward": "yes"},
        {"vehicle": "yes", "access": "yes"},
    ],
    ids=[
        "bicycle-direction-yes", "cycleway-both-track", "cycleway-sides-lane", "cycleway-lane", "bicycle-designated",
        "bicycle-yes-conditional", "oneway-bicycle-no", "vehicle-forward", "vehicle-backward",
        "oneway-vehicle-forward", "oneway-reversed-vehicle-backward", "access-forward", "access-backward", "vehicle-access-yes",
    ],
)
def test_other_bicycle_tags_cannot_reopen_expressway(tmp_path: Path, extra: Tags) -> None:
    src = tmp_path / "src.osm.pbf"
    write_grid_pbf(src, [GridWay(EXPRESSWAY_ONLY_WAY_ID, [(X1 + c, 1) for c in range(5)], {**EXPRESSWAY_TAGS, **extra})])
    build_dir = tmp_path / "build"
    build_dir.mkdir()
    router = Router(build_with_patch(src, build_dir))
    west, east = grid_point(X1, 1), grid_point(X1 + 4, 1)
    for start, end in ((west, east), (east, west)):  # 方向性的 tag 只會重開其中一個方向，兩個方向都要擋住
        with pytest.raises(RoutingError):
            router.route(start, end)


def missing_node_pbf(path: Path) -> None:
    write_raw_pbf(
        path,
        [node(1, 0), node(2, 1)],
        [
            way(500, [1, 2, 999], EXPRESSWAY_TAGS),  # 終點節點 999 不在檔案裡
            way(600, [], {"highway": "trunk"}),  # 空 way
            way(601, [1], {"highway": "trunk"}),  # 只有一個節點
        ],
    )


def test_patch_tolerates_missing_endpoint_node_and_degenerate_ways(tmp_path: Path) -> None:
    src, dst = tmp_path / "src.osm.pbf", tmp_path / "out.osm.pbf"
    missing_node_pbf(src)
    denied = {d.id: d for d in patch_pbf(src, dst, Overrides(deny=frozenset({600, 601}), allow=frozenset()))}
    assert set(denied) == {500, 600, 601}
    assert denied[500].first == pytest.approx(grid_point(0, 1))
    assert denied[500].last is None  # 缺節點：座標記 None，不讓整個 build 掛掉
    assert (denied[600].first, denied[600].last) == (None, None)
    assert (denied[601].first, denied[601].last) == (None, None)
    _, ways, _ = read_pbf(dst)
    assert all(ways[way_id][0]["bicycle"] == "no" for way_id in (500, 600, 601))  # tag 照改


def test_build_with_patch_writes_null_coordinates_and_loads_them_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    src = tmp_path / "src.osm.pbf"
    missing_node_pbf(src)
    build_dir = tmp_path / "build"
    build_dir.mkdir()
    monkeypatch.setattr("engine.data.build.build_tiles", lambda pbf, out: config_path(out))  # 這裡只測標記與清單
    build_with_patch(src, build_dir)
    assert json.loads((build_dir / DENIED_FILE).read_text())[0]["last"] is None
    loaded = load_denied(build_dir)
    assert [d.id for d in loaded] == [500]
    assert loaded[0].first == pytest.approx(grid_point(0, 1))
    assert loaded[0].last is None
    assert not list(build_dir.glob("*.osm.pbf"))


def test_patch_passes_nodes_and_relations_through_unchanged(tmp_path: Path) -> None:
    src, dst = tmp_path / "src.osm.pbf", tmp_path / "out.osm.pbf"
    relation = osmium.osm.mutable.Relation(
        id=900,
        members=[("w", 500, "outer"), ("n", 1, "stop"), ("w", 501, "")],
        tags={"type": "route", "route": "bicycle", "name": "環島 1 號"},
        version=1,
    )
    write_raw_pbf(
        src,
        [node(1, 0, {"name": "起點", "highway": "crossing"}), node(2, 1), node(3, 2)],
        [way(500, [1, 2], EXPRESSWAY_TAGS), way(501, [2, 3], {"highway": "residential"})],
        [relation],
    )
    patch_pbf(src, dst, Overrides(deny=frozenset(), allow=frozenset()))
    src_nodes, src_ways, src_relations = read_pbf(src)
    dst_nodes, dst_ways, dst_relations = read_pbf(dst)
    assert [role for _, _, role in src_relations[900][1]] == ["outer", "stop", ""]  # 確認讀得到 role
    assert src_nodes[1][0] == {"name": "起點", "highway": "crossing"}
    assert dst_nodes == src_nodes
    assert dst_relations == src_relations
    assert dst_ways[501] == src_ways[501]  # 沒被標記的 way 也原樣


def test_allow_beats_deny_when_same_way_is_in_both(tmp_path: Path) -> None:
    src, dst = tmp_path / "src.osm.pbf", tmp_path / "out.osm.pbf"
    write_grid_pbf(src, EXPRESSWAY_MAP)
    both = frozenset({EXPRESSWAY_WAY_ID, 211})  # 200 本來就是快速道路、211 只靠 deny；兩個都同時 allow
    assert patch_pbf(src, dst, Overrides(deny=both, allow=both)) == []
    _, ways, _ = read_pbf(dst)
    assert all("bicycle" not in tags for tags, _ in ways.values())
