import json
from pathlib import Path

import pytest
from valhalla import Actor

from engine.routing.tiles import TileBuildError, build_tiles
from osm_fixture import grid_point

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
