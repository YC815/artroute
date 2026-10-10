import json
from pathlib import Path

import pytest

from engine.data.bike_patch import Overrides, is_expressway, load_overrides, patch_pbf
from engine.data.build import DENIED_FILE, build_with_patch, load_denied
from engine.routing.router import Router, RoutingError
from engine.routing.tiles import build_tiles
from osm_fixture import (
    EXPRESSWAY_MAP,
    EXPRESSWAY_ONLY_MAP,
    EXPRESSWAY_ONLY_WAY_ID,
    EXPRESSWAY_WAY_ID,
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
    config = build_dir / "valhalla.json"  # 本 worktree 的 tiles.py 尚無 config_path()，合併後由 controller 改回
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
