"""在合成地圖上驗證分治吸附：逆向段改成合法繞路，不會逆騎單行道。"""

from pathlib import Path

import pytest

from engine.matching.deadline import Deadline
from engine.matching.stroke import match_stroke
from engine.routing.router import Router
from grid_helpers import densify, rides_westward_on_row
from osm_fixture import grid_point

pytestmark = pytest.mark.valhalla


@pytest.fixture(scope="module")
def router(grid_config: Path) -> Router:
    return Router(grid_config)


def run(router: Router, points):
    return match_stroke(router, points, stroke_index=0, search_radius_m=50, min_piece_m=100, deadline=Deadline(30))


def flatten(chain):
    points = []
    for seg in chain:
        points.extend(seg.points if not points else seg.points[1:])
    return points


def test_legal_stroke_is_single_trace(router: Router) -> None:
    result = run(router, densify(grid_point(0, 1), grid_point(6, 1), n=48))
    assert [[s.kind for s in c] for c in result.chains] == [["stroke"]]
    assert sum(s.length_m for s in result.chains[0]) == pytest.approx(1206, abs=60)


def test_reverse_oneway_becomes_legal_detour(router: Router) -> None:
    result = run(router, densify(grid_point(2, 0), grid_point(0, 0), n=16))
    assert len(result.chains) == 1
    chain = result.chains[0]
    assert "detour" in {s.kind for s in chain}
    assert not rides_westward_on_row(flatten(chain), row=0, col_min=0, col_max=2)
    # 整段逆向只繞一圈（約 850 m），不是每個小段各繞一圈（曾經 6.4 km）
    assert sum(s.length_m for s in chain if s.kind == "detour") <= 1000


def test_mixed_legal_reverse_legal_keeps_legal_parts(router: Router) -> None:
    legal = densify(grid_point(0, 0), grid_point(2, 0), n=16)
    reverse = densify(grid_point(2, 0), grid_point(0, 0), n=16)
    result = run(router, legal + reverse[1:] + legal[1:])
    assert len(result.chains) == 1
    chain = result.chains[0]
    assert {s.kind for s in chain} == {"stroke", "detour"}
    assert not rides_westward_on_row(flatten(chain), row=0, col_min=0, col_max=2)
    kinds = [s.kind for s in chain]
    assert kinds.count("detour") == 1  # 逆向那一段只有一次繞路
    i = kinds.index("detour")
    assert 0 < i < len(kinds) - 1  # 繞路夾在兩段合法的 stroke 之間
    assert set(kinds[:i]) == {"stroke"} and set(kinds[i + 1 :]) == {"stroke"}
    assert chain[i].length_m <= 1200


def test_far_from_roads_warns_no_route(router: Router) -> None:
    result = run(router, densify((121.0, 24.0), (121.003, 24.0), n=8))
    assert result.chains == []
    assert [w.code for w in result.warnings] == ["no_route"]
