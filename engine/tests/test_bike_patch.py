import json
from pathlib import Path

import osmium
import pytest

from engine.data.bike_patch import Overrides, is_expressway, load_overrides, patch_pbf
from engine.data.build import DENIED_FILE, build_with_patch, load_denied
from engine.routing.router import Router, RoutingError
from engine.routing.tiles import build_tiles, config_path
from osm_fixture import (
    CLOSED_FOOTWAY_MAP,
    EXPRESSWAY_MAP,
    EXPRESSWAY_ONLY_MAP,
    EXPRESSWAY_ONLY_WAY_ID,
    EXPRESSWAY_WAY_ID,
    SIDEWALK_MAP,
    SIDEWALK_ONLY_MAP,
    SIDEWALK_ONLY_WAY_ID,
    SIDEWALK_WAY_ID,
    STEPS_MAP,
    STEPS_ONLY_MAP,
    STEPS_ONLY_WAY_ID,
    STEPS_WAY_ID,
    GridWay,
    PLAIN_TRUNK_WAY_ID,
    X0,
    X1,
    OPPOSITE_FOOTWAY_WAY_ID,
    X4,
    grid_point,
    opposite_footway_map,
    steps_only_map,
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


@pytest.mark.parametrize(
    "tags, expected",
    [
        ({"highway": "trunk", "ref": "61"}, True),
        ({"highway": "trunk", "ref": "台61"}, True),
        ({"highway": "trunk", "ref": "臺61"}, True),
        ({"highway": "trunk", "ref": "61甲"}, True),
        ({"highway": "trunk_link", "ref": "台62甲"}, True),
        ({"highway": "trunk", "ref": "1;61"}, True),
        ({"highway": "trunk", "ref": " 61 "}, True),
        ({"highway": "trunk", "ref": "1"}, False),
        ({"highway": "trunk", "ref": "北100"}, False),
        ({"highway": "trunk", "ref": "161"}, False),
        ({"highway": "trunk", "ref": "610"}, False),
        ({"highway": "trunk", "ref": "6"}, False),
        ({"highway": "trunk", "ref": "61乙"}, False),
        ({"highway": "trunk", "ref": ""}, False),
        ({"highway": "primary", "ref": "61"}, False),
    ],
)
def test_is_expressway_ref_with_or_without_tai_prefix(tags: dict[str, str], expected: bool) -> None:
    """OSM 的快速公路 ref 多半寫成不帶「台」的 61、62甲；台／臺前綴也要認得。"""
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
    # bicycle_road、cycle_network 保留。Valhalla 3.9.1 的 lua 其實會讀 bicycle_road（與 cyclestreet）：
    # bike_tag = bicycle or cycleway or bicycle_road or cyclestreet or ...，留著仍然安全，只因為 or 鏈以 bicycle 開頭，
    # bicycle=no 已經先決定結果（"false" 在 Lua 是 truthy），後面的 tag 不會被讀。升級 pyvalhalla 要重新核對
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


# 會（或曾被懷疑會）讓 bicycle=no 的路重新可騎的 tag 組合：快速道路與樓梯共用
REOPENING_EXTRAS = [
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
    # lua 會讀，但 bicycle=no 在 or 鏈裡先判定，所以保留也不會重新放行（升級 pyvalhalla 時靠這兩組把關）
    {"bicycle_road": "yes"},
    {"cyclestreet": "yes"},
    # 實測不會重新放行（Valhalla 不讀這些），留作回歸護欄
    {"access:forward": "yes"},
    {"access:backward": "yes"},
    {"vehicle": "yes", "access": "yes"},
    # lua：service=driveway 且沒有 access 時所有交通方式都放行，實測會蓋過 trunk 的 bicycle=no
    {"service": "driveway"},
]
REOPENING_IDS = [
    "bicycle-direction-yes", "cycleway-both-track", "cycleway-sides-lane", "cycleway-lane", "bicycle-designated",
    "bicycle-yes-conditional", "oneway-bicycle-no", "vehicle-forward", "vehicle-backward",
    "oneway-vehicle-forward", "oneway-reversed-vehicle-backward", "bicycle-road", "cyclestreet",
    "access-forward", "access-backward", "vehicle-access-yes", "service-driveway",
]


@pytest.mark.valhalla
@pytest.mark.parametrize("extra", REOPENING_EXTRAS, ids=REOPENING_IDS)
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


# ---- 樓梯禁行（使用者決定：禁走樓梯，沒有例外）----


def test_patch_marks_every_steps_way_and_strips_reopening_tags(tmp_path: Path) -> None:
    src, dst = tmp_path / "src.osm.pbf", tmp_path / "out.osm.pbf"
    write_raw_pbf(
        src,
        [node(1, 0), node(2, 1), node(3, 2), node(4, 3)],
        [
            way(700, [1, 2], {"highway": "steps"}),
            # 有自行車牽引道的樓梯也一樣禁行；ramp:bicycle 不是 Valhalla 會讀的 tag，照樣保留
            way(701, [2, 3], {"highway": "steps", "bicycle": "yes", "ramp:bicycle": "yes", "step_count": "12"}),
            way(702, [3, 4], {
                "highway": "steps", "bicycle": "designated", "bicycle:forward": "yes", "cycleway": "lane",
                "oneway": "yes", "oneway:bicycle": "no", "vehicle:forward": "yes", "service": "driveway",
            }),
        ],
    )
    denied = patch_pbf(src, dst, Overrides(deny=frozenset(), allow=frozenset()))
    assert [(d.id, d.highway) for d in denied] == [(700, "steps"), (701, "steps"), (702, "steps")]
    assert denied[0].first == pytest.approx(grid_point(0, 1))
    assert denied[0].last == pytest.approx(grid_point(1, 1))
    _, ways, _ = read_pbf(dst)
    assert ways[700][0] == {"highway": "steps", "bicycle": "no"}
    assert ways[701][0] == {"highway": "steps", "bicycle": "no", "ramp:bicycle": "yes", "step_count": "12"}
    assert ways[702][0] == {"highway": "steps", "bicycle": "no", "oneway": "yes"}


def test_allow_override_does_not_exempt_steps(tmp_path: Path) -> None:
    src, dst = tmp_path / "src.osm.pbf", tmp_path / "out.osm.pbf"
    write_raw_pbf(src, [node(1, 0), node(2, 1)], [way(700, [1, 2], {"highway": "steps"})])
    denied = patch_pbf(src, dst, Overrides(deny=frozenset(), allow=frozenset({700})))
    assert [d.id for d in denied] == [700]
    assert read_pbf(dst)[1][700][0]["bicycle"] == "no"


@pytest.mark.valhalla
def test_steps_shortcut_is_never_taken_after_patch(tmp_path: Path) -> None:
    src = tmp_path / "src.osm.pbf"
    write_grid_pbf(src, STEPS_MAP)
    start, end = grid_point(X4 - 1, 1), grid_point(X4 + 2, 1)

    # 對照組：未修補時 shortest 走樓梯（預設成本本來就重罰樓梯，所以只有 shortest 能證明 patch 有作用）
    control = Router(build_tiles(src, tmp_path / "control"))
    control_route = control.route(start, end, shortest=True)
    assert STEPS_WAY_ID in {e.way_id for e in control.edges(control_route.points)}

    build_dir = tmp_path / "build"
    build_dir.mkdir()
    router = Router(build_with_patch(src, build_dir))
    assert [(d.id, d.highway) for d in load_denied(build_dir)] == [(STEPS_WAY_ID, "steps")]
    for shortest in (False, True):
        route = router.route(start, end, shortest=shortest)
        assert STEPS_WAY_ID not in {e.way_id for e in router.edges(route.points)}
        assert route.length_km > 2.0  # 走住宅街繞路


@pytest.mark.valhalla
def test_steps_only_path_becomes_unroutable_after_patch(tmp_path: Path) -> None:
    src = tmp_path / "src.osm.pbf"
    write_grid_pbf(src, STEPS_ONLY_MAP)
    start, end = grid_point(X4 + 3, 1), grid_point(X4 + 6, 1)

    control = Router(build_tiles(src, tmp_path / "control"))
    for shortest in (False, True):
        route = control.route(start, end, shortest=shortest)
        assert STEPS_ONLY_WAY_ID in {e.way_id for e in control.edges(route.points)}

    build_dir = tmp_path / "build"
    build_dir.mkdir()
    router = Router(build_with_patch(src, build_dir))
    for shortest in (False, True):
        for a, b in ((start, end), (end, start)):
            with pytest.raises(RoutingError):
                router.route(a, b, shortest=shortest)


@pytest.mark.valhalla
@pytest.mark.parametrize("extra", REOPENING_EXTRAS, ids=REOPENING_IDS)
def test_other_bicycle_tags_cannot_reopen_steps(tmp_path: Path, extra: Tags) -> None:
    src = tmp_path / "src.osm.pbf"
    write_grid_pbf(src, steps_only_map({"highway": "steps", **extra}))
    build_dir = tmp_path / "build"
    build_dir.mkdir()
    router = Router(build_with_patch(src, build_dir))
    west, east = grid_point(X4 + 3, 1), grid_point(X4 + 6, 1)
    for start, end in ((west, east), (east, west)):
        for shortest in (False, True):
            with pytest.raises(RoutingError):
                router.route(start, end, shortest=shortest)


# ---- 人行道盡量避免：自行車本來就能走的人行道標成 bicycle=dismount；本來不能走的維持不能走 ----


@pytest.mark.parametrize(
    "tags, bicycle",
    [
        ({"highway": "footway", "footway": "sidewalk", "bicycle": "yes"}, "dismount"),
        ({"highway": "footway", "bicycle": "designated", "segregated": "yes"}, "dismount"),
        ({"highway": "footway", "bicycle": "permissive"}, "dismount"),
        ({"highway": "footway", "footway": "sidewalk", "bicycle": "use_sidepath"}, "dismount"),
        ({"highway": "footway", "footway": "crossing", "bicycle": "yes"}, "dismount"),
        ({"highway": "pedestrian", "bicycle": "yes"}, "dismount"),
        ({"highway": "footway", "bicycle": "dismount"}, "dismount"),
        # 沒有 bicycle tag 時，lua 也讀 cycleway 與 cycleway:both
        ({"highway": "footway", "footway": "sidewalk", "cycleway": "shared"}, "dismount"),
        ({"highway": "footway", "cycleway:both": "track"}, "dismount"),
        # 自行車本來就不能走：不能因為標記反而放行
        ({"highway": "footway", "footway": "sidewalk"}, None),
        ({"highway": "footway", "bicycle": "no"}, "no"),
        ({"highway": "pedestrian"}, None),
        ({"highway": "footway", "cycleway": "no"}, None),
        ({"highway": "footway", "cycleway:both": "yes"}, None),  # lua 的 cycleway:both 只認 lane／track／shared 類
        ({"highway": "footway", "cycleway:left": "track"}, None),  # 只有一側不會放行
        # opposite* 只在 lua 的 bike_reverse 表：只放行逆向。標 dismount 會連順向一起打開（等於放行逆向騎）
        ({"highway": "footway", "cycleway": "opposite"}, None),
        ({"highway": "footway", "cycleway": "opposite_lane"}, None),
        ({"highway": "pedestrian", "cycleway": "opposite_track"}, None),
        # path、cycleway 不動
        ({"highway": "path", "bicycle": "yes"}, "yes"),
        ({"highway": "cycleway"}, None),
        ({"highway": "residential", "sidewalk": "both"}, None),
    ],
)
def test_patch_marks_rideable_footways_as_dismount(tmp_path: Path, tags: Tags, bicycle: str | None) -> None:
    src, dst = tmp_path / "src.osm.pbf", tmp_path / "out.osm.pbf"
    write_raw_pbf(src, [node(1, 0), node(2, 1)], [way(720, [1, 2], tags)])
    assert patch_pbf(src, dst, Overrides(deny=frozenset(), allow=frozenset())) == []  # 人行道不是禁行
    patched = read_pbf(dst)[1][720][0]
    assert patched.get("bicycle") == bicycle
    assert {k: v for k, v in patched.items() if k != "bicycle"} == {k: v for k, v in tags.items() if k != "bicycle"}


@pytest.mark.valhalla
def test_sidewalk_shortcut_is_avoided_by_default_costing_after_patch(tmp_path: Path) -> None:
    src = tmp_path / "src.osm.pbf"
    write_grid_pbf(src, SIDEWALK_MAP)
    start, end = grid_point(X4 + 7, 1), grid_point(X4 + 11, 1)

    # 對照組：未修補時預設成本與 shortest 都走人行道捷徑（預設成本還偏好它勝過道路）
    control = Router(build_tiles(src, tmp_path / "control"))
    for shortest in (False, True):
        route = control.route(start, end, shortest=shortest)
        assert SIDEWALK_WAY_ID in {e.way_id for e in control.edges(route.points)}

    build_dir = tmp_path / "build"
    build_dir.mkdir()
    router = Router(build_with_patch(src, build_dir))
    route = router.route(start, end)
    assert SIDEWALK_WAY_ID not in {e.way_id for e in router.edges(route.points)}
    assert route.length_km > 1.2  # 改走稍長的道路
    # 實測（Valhalla 3.9.1）：shortest 的成本只看長度，dismount 對它沒有作用，照樣走人行道。
    # 升級 pyvalhalla 後這裡變了，就要重新評估繞路用哪種成本（docs/p1-5-quality-findings.md「樓梯與人行道」）
    shortest = router.route(start, end, shortest=True)
    assert SIDEWALK_WAY_ID in {e.way_id for e in router.edges(shortest.points)}


@pytest.mark.valhalla
def test_sidewalk_only_path_stays_reachable_after_patch(tmp_path: Path) -> None:
    src = tmp_path / "src.osm.pbf"
    write_grid_pbf(src, SIDEWALK_ONLY_MAP)
    build_dir = tmp_path / "build"
    build_dir.mkdir()
    router = Router(build_with_patch(src, build_dir))
    west, east = grid_point(X4 + 12, 1), grid_point(X4 + 16, 1)
    for start, end in ((west, east), (east, west)):
        for shortest in (False, True):
            route = router.route(start, end, shortest=shortest)
            assert SIDEWALK_ONLY_WAY_ID in {e.way_id for e in router.edges(route.points)}


@pytest.mark.valhalla
def test_footways_closed_to_bicycles_stay_closed_after_patch(tmp_path: Path) -> None:
    """Valhalla 3.9.1 的 footway／pedestrian 預設不給自行車走（實測）；bicycle=dismount 反而會放行，所以不能一律標記。"""
    src = tmp_path / "src.osm.pbf"
    write_grid_pbf(src, CLOSED_FOOTWAY_MAP)
    build_dir = tmp_path / "build"
    build_dir.mkdir()
    router = Router(build_with_patch(src, build_dir))
    for row in (1, 3):  # 列 1：沒有自行車 tag 的人行道；列 3：bicycle=no 的 pedestrian
        west, east = grid_point(X4 + 18, row), grid_point(X4 + 22, row)
        for start, end in ((west, east), (east, west)):
            for shortest in (False, True):
                with pytest.raises(RoutingError):
                    router.route(start, end, shortest=shortest)


@pytest.mark.valhalla
@pytest.mark.parametrize("cycleway", ["opposite", "opposite_lane", "opposite_track"])
def test_patch_does_not_open_the_closed_direction_of_a_contraflow_footway(tmp_path: Path, cycleway: str) -> None:
    src = tmp_path / "src.osm.pbf"
    write_grid_pbf(src, opposite_footway_map(cycleway))
    west, east = grid_point(X4 + 24, 1), grid_point(X4 + 28, 1)

    # 對照組：未修補時只有東→西走得通（證明這張圖真的是單向，下面「修補後西→東仍不通」不是空洞的）
    control = Router(build_tiles(src, tmp_path / "control"))
    back = control.route(east, west)
    assert OPPOSITE_FOOTWAY_WAY_ID in {e.way_id for e in control.edges(back.points)}
    with pytest.raises(RoutingError):
        control.route(west, east)

    build_dir = tmp_path / "build"
    build_dir.mkdir()
    router = Router(build_with_patch(src, build_dir))
    for shortest in (False, True):
        with pytest.raises(RoutingError):
            router.route(west, east, shortest=shortest)
        route = router.route(east, west, shortest=shortest)
        assert OPPOSITE_FOOTWAY_WAY_ID in {e.way_id for e in router.edges(route.points)}
