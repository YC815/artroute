"""整個自動搜尋跑在真的 Valhalla（合成城市）上：回傳的每個候選都要通過 bench 的安全斷言。"""

from pathlib import Path

import pytest

from engine.bench.runner import bad_turns, safety_problem
from engine.data.rideable import extract_rideable
from engine.matching.deadline import Deadline
from engine.routing.geo import haversine_m
from engine.routing.router import Router
from engine.routing.tiles import build_tiles
from engine.search.roads import RoadIndex
from engine.search.service import run_search
from engine.search.types import Best, NormStroke, Progress, SearchParams
from osm_fixture import write_grid_pbf
from search_city import CITY_CENTER, CITY_MAP

SQUARE = [NormStroke([(-0.5, -0.5), (0.5, -0.5), (0.5, 0.5), (-0.5, 0.5)], closed=True)]
L_SHAPE = [NormStroke([(-0.25, 0.5), (-0.25, -0.5), (0.25, -0.5)], closed=False)]


@pytest.fixture(scope="module")
def city(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, RoadIndex]:
    """合成城市的 tiles 與可騎道路索引，都由建圖流程的同一份 pbf 產生（與正式建圖相同的兩個步驟）。"""
    root = tmp_path_factory.mktemp("city")
    write_grid_pbf(root / "city.osm.pbf", CITY_MAP)
    extract_rideable(root / "city.osm.pbf", root / "build")
    return build_tiles(root / "city.osm.pbf", root / "build"), RoadIndex.load(root / "build")


@pytest.mark.valhalla
@pytest.mark.parametrize(("strokes", "km"), [(SQUARE, (2.0, 4.0)), (L_SHAPE, (1.0, 2.5))])
def test_search_on_city(city: tuple[Path, RoadIndex], strokes, km) -> None:
    city_config, roads = city
    params = SearchParams(strokes, CITY_CENTER, 800.0, km[0], km[1], -15.0, 15.0)
    events = []
    outcome = run_search(params, [Router(city_config), Router(city_config)], roads, Deadline(120), events.append)
    assert outcome.candidates, outcome.notice
    assert outcome.coarse_candidates > 0 and outcome.evaluated >= len(outcome.candidates)
    assert any(isinstance(e, Progress) and e.stage == "match" for e in events)
    assert any(isinstance(e, Best) for e in events)
    router = Router(city_config)
    for c in outcome.candidates:
        assert haversine_m(c.result.route_points[0], CITY_CENTER) <= params.radius_m
        assert c.start_distance_m == pytest.approx(haversine_m(c.result.route_points[0], CITY_CENTER))
        assert c.length_ok == (km[0] <= c.result.route_length_m / 1000 <= km[1])
        assert safety_problem(router, c.result.segments, frozenset()) is None
        assert bad_turns(router, c.result.segments) == 0
    best = outcome.candidates[0]
    assert best.length_ok
    assert best.result.score.overall >= 70  # 棋盤上的正方形／L 應該畫得很好
