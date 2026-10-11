"""真實 Valhalla：立體交叉在平面上是同一點，接點轉彎檢查要分得出「真的路口」與「橋跨過下面那條路」。"""

from pathlib import Path

import pytest

from engine.matching.compose import Part, compose
from engine.matching.deadline import Deadline
from engine.matching.turns import rideable
from engine.matching.types import JOIN_TOLERANCE_M, Segment
from engine.routing.geo import haversine_m, polyline_length_m
from engine.routing.router import Router
from osm_fixture import X3, grid_point
from turn_router import every_junction_rideable

pytestmark = pytest.mark.valhalla

CROSS = grid_point(X3 + 1, 1)  # 橋與下面那條路在平面上的交叉點（沒有共用節點）


@pytest.fixture(scope="module")
def router(crossing_config: Path) -> Router:
    return Router(crossing_config)


def seg(points, index: int) -> Segment:
    return Segment("stroke", index, points, polyline_length_m(points))


def test_turn_at_a_shared_node_is_rideable(router: Router) -> None:
    # 502 由南往北到 (X3, 1)，在那裡轉上橋往東：兩條路共用這個節點
    node = grid_point(X3, 1)
    assert rideable(router, Deadline(30), [grid_point(X3, 0), node], [node, CROSS])


def test_bridge_to_the_road_underneath_is_not_rideable(router: Router) -> None:
    assert not rideable(router, Deadline(30), [grid_point(X3, 1), CROSS], [CROSS, grid_point(X3 + 1, 2)])


def test_compose_never_joins_the_bridge_to_the_road_underneath(router: Router) -> None:
    bridge = Part([seg([grid_point(X3, 1), CROSS], 0)], closed=False)
    under = Part([seg([CROSS, grid_point(X3 + 1, 2)], 1)], closed=False)
    result = compose(router, [bridge, under], Deadline(30))
    assert [s.kind for s in result.segments] != ["stroke", "stroke"]  # 舊版：交叉點相距 0 m，直接接成一條
    for a, b in zip(result.segments, result.segments[1:]):
        assert haversine_m(a.points[-1], b.points[0]) <= JOIN_TOLERANCE_M
    assert every_junction_rideable(router, result.segments)
