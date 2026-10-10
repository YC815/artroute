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
        for way in sorted(ways, key=lambda w: w.id):  # Valhalla 要求 pbf 內物件依 id 排序，不排會 "Detected unsorted input data"
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


EXPRESSWAY_WAY_ID = 200   # trunk + ref 台64：要被標記
PLAIN_TRUNK_WAY_ID = 201  # 一般 trunk（例如台1線）：不標記
X0 = 20  # 與 TEST_MAP 分開
# 住宅街繞路所在的列。Valhalla 的 bicycle costing 對 trunk 本身就有懲罰：繞路只長一點（列 0、-1）時
# 未修補的路線也會繞開快速道路，測試就分不出 patch 有沒有作用；列 -2 起繞路才長到讓未修補時走 trunk，
# 取 -3 多留一列餘裕。
DETOUR_ROW = -3

EXPRESSWAY_MAP: list[GridWay] = [
    # 上：快速公路直線；下：住宅街繞路（約 4×0.22 + 0.8 + 4×0.22 km，遠長於直線的 0.8 km）
    GridWay(EXPRESSWAY_WAY_ID, [(X0 + c, 1) for c in range(5)], {"highway": "trunk", "ref": "台64", "name": "八里新店線"}),
    GridWay(210, [(X0, 1), (X0, DETOUR_ROW)], RESIDENTIAL),
    GridWay(211, [(X0 + c, DETOUR_ROW) for c in range(5)], RESIDENTIAL),
    GridWay(212, [(X0 + 4, DETOUR_ROW), (X0 + 4, 1)], RESIDENTIAL),
    # 一般 trunk：唯一通路，必須可騎
    GridWay(PLAIN_TRUNK_WAY_ID, [(X0 + 4, 1), (X0 + 4, 2), (X0 + 4, 3)], {"highway": "trunk", "ref": "台1"}),
]

EXPRESSWAY_ONLY_WAY_ID = 300
X1 = 40  # 與 TEST_MAP、EXPRESSWAY_MAP 分開

EXPRESSWAY_ONLY_MAP: list[GridWay] = [
    # 快速公路是兩點之間唯一的路：未修補時走得通，標成 bicycle=no 後必須無路可走
    GridWay(EXPRESSWAY_ONLY_WAY_ID, [(X1 + c, 1) for c in range(5)], {"highway": "trunk", "ref": "台64"}),
]
