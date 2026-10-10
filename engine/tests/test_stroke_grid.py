"""在合成地圖上驗證分治吸附：逆向段改成合法繞路，不會逆騎單行道。"""

from pathlib import Path

import pytest

from engine.matching.deadline import Deadline
from engine.matching.stroke import match_stroke
from engine.matching.types import JOIN_TOLERANCE_M
from engine.routing.geo import haversine_m, polyline_length_m
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


def shift_north(points, meters: float):
    return [(lon, lat + meters / 110_574) for lon, lat in points]


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


def test_shifted_stroke_still_has_a_single_detour(router: Router) -> None:
    # 整條線偏離道路約 10 m：trace 的端點被吸附到道路上，跟輸入點不同。
    # 繞路必須接在鄰段實際端點之間，不能再補一條往回的橋（單行道上會多繞一整圈）
    legal = densify(grid_point(0, 0), grid_point(2, 0), n=16)
    reverse = densify(grid_point(2, 0), grid_point(0, 0), n=16)
    result = run(router, shift_north(legal + reverse[1:] + legal[1:], 10))
    assert len(result.chains) == 1
    chain = result.chains[0]
    for a, b in zip(chain, chain[1:]):
        assert haversine_m(a.points[-1], b.points[0]) <= JOIN_TOLERANCE_M
    detours = [s for s in chain if s.kind == "detour"]
    assert len(detours) == 1
    assert sum(s.length_m for s in detours) <= 1000
    assert not rides_westward_on_row(flatten(chain), row=0, col_min=0, col_max=2)


def block_loop(half_m: float = 35):
    """街區正中央的封閉小方框（邊長 2 * half_m），離四周道路都超過吸附半徑。"""
    cx, cy = grid_point(2.5, 1.5)
    dx, dy = half_m / 100_840, half_m / 110_574
    return [(cx - dx, cy - dy), (cx + dx, cy - dy), (cx + dx, cy + dy), (cx - dx, cy + dy), (cx - dx, cy - dy)]


def test_closed_loop_far_from_roads_is_not_a_zero_length_detour(router: Router) -> None:
    loop = block_loop()
    result = run(router, loop)
    segments = [s for chain in result.chains for s in chain]
    assert all(s.length_m > 0 for s in segments)
    # 要嘛真的繞出夠長的路，要嘛有 warning 告知；不能無聲少掉
    assert result.warnings or sum(s.length_m for s in segments) >= 0.7 * polyline_length_m(loop)
    assert result.chains or [w.code for w in result.warnings] == ["no_route"]


def test_single_point_stroke_warns_no_route(router: Router) -> None:
    result = run(router, [block_loop()[0]])
    assert result.chains == []
    assert [w.code for w in result.warnings] == ["no_route"]
