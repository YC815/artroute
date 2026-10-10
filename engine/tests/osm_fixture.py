"""用 pyosmium 產生小型合成地圖 pbf，讓 Valhalla 測試結果固定、可重現。"""

from dataclasses import dataclass, field
from pathlib import Path

import osmium

LON0, LAT0 = 121.50, 25.05
STEP = 0.002  # 經度約 201m、緯度約 222m


def grid_point(col: float, row: float) -> tuple[float, float]:
    """網格座標 → (lon, lat)。"""
    return (LON0 + col * STEP, LAT0 + row * STEP)


@dataclass(frozen=True)
class GridWay:
    id: int
    nodes: list[tuple[int, int]]
    tags: dict[str, str] = field(default_factory=dict)


def write_grid_pbf(path: Path, ways: list[GridWay]) -> None:
    node_ids: dict[tuple[int, int], int] = {}
    for way in ways:
        for coord in way.nodes:
            node_ids.setdefault(coord, len(node_ids) + 1)
    writer = osmium.SimpleWriter(str(path))
    try:
        for coord, node_id in sorted(node_ids.items(), key=lambda item: item[1]):
            writer.add_node(
                osmium.osm.mutable.Node(id=node_id, location=grid_point(*coord), version=1)
            )
        for way in ways:
            writer.add_way(
                osmium.osm.mutable.Way(
                    id=way.id,
                    nodes=[node_ids[c] for c in way.nodes],
                    tags=way.tags,
                    version=1,
                )
            )
    finally:
        writer.close()


RESIDENTIAL = {"highway": "residential"}
BOTTOM_ONEWAY_WAY_ID = 100
CONTRAFLOW_WAY_ID = 101
MOTORWAY_WAY_ID = 102

TEST_MAP: list[GridWay] = [
    GridWay(1, [(c, 1) for c in range(7)], RESIDENTIAL),
    *[GridWay(10 + c, [(c, 0), (c, 1)], RESIDENTIAL) for c in (0, 1, 2, 4, 5, 6)],
    GridWay(20, [(0, 1), (0, 2), (0, 3)], RESIDENTIAL),
    GridWay(21, [(6, 1), (6, 2), (6, 3)], RESIDENTIAL),
    GridWay(BOTTOM_ONEWAY_WAY_ID, [(0, 0), (1, 0), (2, 0)], {**RESIDENTIAL, "oneway": "yes"}),
    GridWay(
        CONTRAFLOW_WAY_ID,
        [(4, 0), (5, 0), (6, 0)],
        {**RESIDENTIAL, "oneway": "yes", "oneway:bicycle": "no"},
    ),
    GridWay(MOTORWAY_WAY_ID, [(c, 3) for c in range(7)], {"highway": "motorway"}),
]
