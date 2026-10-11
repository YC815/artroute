from pathlib import Path

import pytest

from engine.data.build import build_with_patch
from engine.routing.tiles import build_tiles
from osm_fixture import (
    CROSSING_MAP,
    EDIT_MAPS,
    EDIT_VERIFY_NEW_MAP,
    EDIT_VERIFY_OLD_MAP,
    PLANNER_MAPS,
    PRUNE_MAP,
    TEST_MAP,
    MeterWay,
    write_grid_pbf,
    write_meter_pbf,
)


@pytest.fixture(scope="session")
def grid_config(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """合成地圖建好的 Valhalla 設定檔路徑（整個測試只建一次）。"""
    root = tmp_path_factory.mktemp("grid")
    pbf = root / "grid.osm.pbf"
    write_grid_pbf(pbf, TEST_MAP)
    return build_tiles(pbf, root / "build")


@pytest.fixture(scope="session")
def crossing_config(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """立體交叉合成地圖建好的 Valhalla 設定檔路徑（整個測試只建一次）。"""
    root = tmp_path_factory.mktemp("crossing")
    pbf = root / "crossing.osm.pbf"
    write_grid_pbf(pbf, CROSSING_MAP)
    return build_tiles(pbf, root / "build")


@pytest.fixture(scope="session")
def prune_config(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """剪小刺與小圈合成地圖建好的 Valhalla 設定檔路徑（整個測試只建一次）。"""
    root = tmp_path_factory.mktemp("prune")
    pbf = root / "prune.osm.pbf"
    write_grid_pbf(pbf, PRUNE_MAP)
    return build_tiles(pbf, root / "build")


@pytest.fixture(scope="session")
def edit_config(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """手動微調合成地圖建好的 Valhalla 設定檔路徑（整個測試只建一次）。"""
    root = tmp_path_factory.mktemp("edit")
    pbf = root / "edit.osm.pbf"
    write_meter_pbf(pbf, EDIT_MAPS)
    return build_tiles(pbf, root / "build")


def _meter_config(tmp_path_factory: pytest.TempPathFactory, name: str, ways: list[MeterWay]) -> Path:
    root = tmp_path_factory.mktemp(name)
    pbf = root / f"{name}.osm.pbf"
    write_meter_pbf(pbf, ways)
    return build_tiles(pbf, root / "build")


@pytest.fixture(scope="session")
def verify_old_config(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """/verify 的舊路網：200–400 m 的路雙向。"""
    return _meter_config(tmp_path_factory, "verify_old", EDIT_VERIFY_OLD_MAP)


@pytest.fixture(scope="session")
def verify_new_config(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """/verify 的新路網：同一張圖，200–400 m 的路改成 oneway=yes（只能往西）。"""
    return _meter_config(tmp_path_factory, "verify_new", EDIT_VERIFY_NEW_MAP)


@pytest.fixture(scope="session")
def planner_build(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """描形規劃器的場景地圖走正式的建圖流程（build_with_patch）：同一份修補後的 pbf 建 Valhalla tiles 與可騎路網圖，
    兩者看到的路網（含單行道語意）一致。回傳 build 目錄（整個測試只建一次）。"""
    root = tmp_path_factory.mktemp("planner")
    pbf = root / "planner.osm.pbf"
    write_meter_pbf(pbf, PLANNER_MAPS)
    build = root / "build"
    build.mkdir()
    build_with_patch(pbf, build)
    return build
