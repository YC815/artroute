from pathlib import Path

import pytest

from engine.routing.tiles import build_tiles
from osm_fixture import CROSSING_MAP, TEST_MAP, write_grid_pbf


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
