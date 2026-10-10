import json
from pathlib import Path

import pytest
from valhalla import Actor

from engine.routing.tiles import TileBuildError, build_tiles
from osm_fixture import TEST_MAP, grid_point, write_grid_pbf

pytestmark = pytest.mark.valhalla


def test_build_tiles_writes_config_pointing_to_tiles(grid_config: Path) -> None:
    config = json.loads(grid_config.read_text())
    tile_dir = Path(config["mjolnir"]["tile_dir"])
    assert tile_dir.resolve() == (grid_config.parent / "tiles").resolve()
    assert any(tile_dir.rglob("*.gph"))


def test_built_tiles_can_route(grid_config: Path) -> None:
    (lon1, lat1), (lon2, lat2) = grid_point(0, 1), grid_point(6, 1)
    result = Actor(str(grid_config)).route(
        {"locations": [{"lon": lon1, "lat": lat1}, {"lon": lon2, "lat": lat2}], "costing": "bicycle"}
    )
    assert result["trip"]["summary"]["length"] == pytest.approx(1.2, abs=0.05)


def test_missing_pbf_raises(tmp_path: Path) -> None:
    with pytest.raises(TileBuildError):
        build_tiles(tmp_path / "nope.osm.pbf", tmp_path / "build")


def test_build_tiles_accepts_relative_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    write_grid_pbf(Path("g.osm.pbf"), TEST_MAP)
    config_path = build_tiles(Path("g.osm.pbf"), Path("build"))
    config = json.loads(config_path.read_text())
    assert Path(config["mjolnir"]["tile_dir"]).is_absolute()
    assert any(Path(config["mjolnir"]["tile_dir"]).rglob("*.gph"))


def test_build_tiles_works_without_venv_bin_on_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    pbf = tmp_path / "g.osm.pbf"
    write_grid_pbf(pbf, TEST_MAP)
    config_path = build_tiles(pbf, tmp_path / "build")
    assert any(Path(json.loads(config_path.read_text())["mjolnir"]["tile_dir"]).rglob("*.gph"))


def test_config_raises_trace_search_radius_limit_to_150m(grid_config: Path) -> None:
    config = json.loads(grid_config.read_text())
    assert config["service_limits"]["trace"]["max_search_radius"] == 150
    # 只調 service_limits 沒用：meili 的預設上限 100 會把請求的半徑悄悄壓回 100 m
    assert config["meili"]["default"]["max_search_radius"] == 150
