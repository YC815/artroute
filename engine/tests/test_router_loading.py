"""Router 從 build 目錄載入：換掛載路徑也能用、設定在載入時覆寫、錯誤一律是 RoutingError。"""

import json
import shutil
from pathlib import Path

import pytest

from engine.routing.router import CACHE_SIZE_BYTES, Router, RoutingError, _route_result, load_config
from engine.routing.tiles import config_path
from osm_fixture import grid_point


def test_config_path_is_valhalla_json(tmp_path: Path) -> None:
    assert config_path(tmp_path) == tmp_path / "valhalla.json"


@pytest.mark.valhalla
def test_from_build_works_after_moving_build_dir(grid_config: Path, tmp_path: Path) -> None:
    moved = tmp_path / "moved"
    shutil.copytree(grid_config.parent, moved)
    config = json.loads(config_path(moved).read_text())
    config["mjolnir"]["tile_dir"] = "/does/not/exist"  # 模擬搬到別的掛載點後失效的絕對路徑
    config_path(moved).write_text(json.dumps(config))

    result = Router.from_build(moved).route(grid_point(0, 1), grid_point(6, 1))
    assert result.length_km == pytest.approx(1.2, abs=0.05)


def test_load_config_overrides_tile_dir_and_cache(tmp_path: Path) -> None:
    config_path(tmp_path).write_text(json.dumps({"mjolnir": {"tile_dir": "/old", "max_cache_size": 1}}))
    config = load_config(tmp_path)
    assert config["mjolnir"]["tile_dir"] == str((tmp_path / "tiles").resolve())
    assert config["mjolnir"]["max_cache_size"] == CACHE_SIZE_BYTES


def _write_meili_config(build_dir: Path) -> None:
    config_path(build_dir).write_text(
        json.dumps({"mjolnir": {"tile_dir": "/old"}, "meili": {"default": {"sigma_z": 4.07, "search_radius": 50}}})
    )


def test_load_config_overrides_sigma_z_only_when_given(tmp_path: Path) -> None:
    _write_meili_config(tmp_path)
    assert load_config(tmp_path, sigma_z=15.0)["meili"]["default"] == {"sigma_z": 15.0, "search_radius": 50}
    assert load_config(tmp_path, sigma_z=None)["meili"]["default"] == {"sigma_z": 4.07, "search_radius": 50}
    assert load_config(tmp_path)["meili"]["default"]["sigma_z"] == 4.07


def test_load_config_missing_raises_routing_error(tmp_path: Path) -> None:
    with pytest.raises(RoutingError, match="讀不到路網設定"):
        load_config(tmp_path)


def test_load_config_malformed_raises_routing_error(tmp_path: Path) -> None:
    config_path(tmp_path).write_text("{not json")
    with pytest.raises(RoutingError, match="讀不到路網設定"):
        load_config(tmp_path)


def test_malformed_valhalla_response_raises_routing_error() -> None:
    with pytest.raises(RoutingError, match="Valhalla 回應格式不符"):
        _route_result({"trip": {}})


@pytest.mark.valhalla
def test_trace_rejects_single_point(grid_config: Path) -> None:
    with pytest.raises(RoutingError):
        Router(grid_config).trace([grid_point(0, 1)])


@pytest.mark.valhalla
@pytest.mark.parametrize("radius", [0, -5, 151])
def test_trace_rejects_radius_out_of_range(grid_config: Path, radius: float) -> None:
    with pytest.raises(RoutingError):
        Router(grid_config).trace([grid_point(0, 1), grid_point(1, 1)], search_radius_m=radius)


@pytest.mark.valhalla
def test_from_build_without_tiles_raises_routing_error(grid_config: Path, tmp_path: Path) -> None:
    broken = tmp_path / "broken"
    shutil.copytree(grid_config.parent, broken)
    shutil.rmtree(broken / "tiles")  # 模擬 tiles 目錄沒掛載或被刪掉

    with pytest.raises(RoutingError, match="無法載入路網"):
        Router.from_build(broken)
