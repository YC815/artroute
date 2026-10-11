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

SHORT_PRIMARY_WAY_ID = 400
X2 = 60  # 與其他地圖分開
CYCLEWAY_DETOUR_ROW = -2

SHORTEST_MAP: list[GridWay] = [
    # 預設的 bicycle 成本偏好自行車道、懲罰主幹道：兩點之間走 cycleway 繞路（約 2.1 km）；
    # shortest 只看距離：走 primary 直線（約 1.2 km）。primary 本身可騎（沒有 bicycle=no）。
    GridWay(SHORT_PRIMARY_WAY_ID, [(X2 + c, 1) for c in range(7)], {"highway": "primary"}),
    GridWay(410, [(X2, 1), (X2, CYCLEWAY_DETOUR_ROW)], {"highway": "cycleway"}),
    GridWay(411, [(X2 + c, CYCLEWAY_DETOUR_ROW) for c in range(7)], {"highway": "cycleway"}),
    GridWay(412, [(X2 + 6, CYCLEWAY_DETOUR_ROW), (X2 + 6, 1)], {"highway": "cycleway"}),
]

X3 = 80  # 與其他地圖分開
BRIDGE_WAY_ID = 500
UNDER_WAY_ID = 501

CROSSING_MAP: list[GridWay] = [
    # 橋由西往東跨過下面那條路：兩者在 (X3 + 1, 1) 交叉但沒有共用節點，平面上是同一點，實際上不相通
    GridWay(BRIDGE_WAY_ID, [(X3, 1), (X3 + 2, 1)], {**RESIDENTIAL, "bridge": "yes", "layer": "1"}),
    GridWay(UNDER_WAY_ID, [(X3 + 1, 0), (X3 + 1, 2)], RESIDENTIAL),
    # 外圈：橋的兩端與下面那條路的兩端用一般道路連起來；從橋上到下面那條路只能繞外圈
    GridWay(502, [(X3, 1), (X3, 0), (X3 + 1, 0)], RESIDENTIAL),
    GridWay(503, [(X3 + 2, 1), (X3 + 2, 2), (X3 + 1, 2)], RESIDENTIAL),
]

# ---- 樓梯與人行道（Task 14）：每個情境各寫成獨立的 pbf，彼此不會互相吸附 ----
X4 = 100  # 與其他地圖分開
STEPS_WAY_ID = 700
STEPS_ONLY_WAY_ID = 710
STEPS = {"highway": "steps"}
SIDEWALK_WAY_ID = 720
SIDEWALK_ONLY_WAY_ID = 730
CLOSED_FOOTWAY_WAY_ID = 740
NO_BICYCLE_FOOTWAY_WAY_ID = 741
OPPOSITE_FOOTWAY_WAY_ID = 750

# edge_walk 遇到兩條 way 共用同一對端點時可能配錯 way（實測：2 點的捷徑被配成同端點的 4 點道路），
# 所以捷徑與繞路不共用端點：兩端各接一段住宅街，繞路從住宅街的外端出發
STEPS_MAP: list[GridWay] = [
    # 樓梯捷徑（經樓梯約 0.6 km）對住宅街繞路（約 2.4 km）。未修補時 shortest 走樓梯，預設成本本來就繞開樓梯
    GridWay(STEPS_WAY_ID, [(X4, 1), (X4 + 1, 1)], STEPS),
    GridWay(701, [(X4 - 1, 1), (X4, 1)], RESIDENTIAL),
    GridWay(702, [(X4 + 1, 1), (X4 + 2, 1)], RESIDENTIAL),
    GridWay(703, [(X4 - 1, 1), (X4 - 1, DETOUR_ROW), (X4 + 2, DETOUR_ROW), (X4 + 2, 1)], RESIDENTIAL),
]

def steps_only_map(steps_tags: dict[str, str]) -> list[GridWay]:
    """樓梯是兩段住宅街之間唯一的路。起訖要在住宅街上：Valhalla 不會把 route 的起訖吸附到樓梯上（實測）。"""
    return [
        GridWay(STEPS_ONLY_WAY_ID, [(X4 + 4, 1), (X4 + 5, 1)], steps_tags),
        GridWay(711, [(X4 + 3, 1), (X4 + 4, 1)], RESIDENTIAL),
        GridWay(712, [(X4 + 5, 1), (X4 + 6, 1)], RESIDENTIAL),
    ]


# 未修補時走得通，標成 bicycle=no 後必須無路可走
STEPS_ONLY_MAP = steps_only_map(STEPS)

SIDEWALK = {"highway": "footway", "footway": "sidewalk", "bicycle": "yes"}  # 明確允許自行車的人行道（台灣最常見）

SIDEWALK_MAP: list[GridWay] = [
    # 經人行道捷徑約 0.8 km（人行道 0.4 km）對稍長的道路（經列 0，約 1.25 km）
    GridWay(SIDEWALK_WAY_ID, [(X4 + 8, 1), (X4 + 10, 1)], SIDEWALK),
    GridWay(721, [(X4 + 7, 1), (X4 + 8, 1)], RESIDENTIAL),
    GridWay(722, [(X4 + 10, 1), (X4 + 11, 1)], RESIDENTIAL),
    GridWay(723, [(X4 + 7, 1), (X4 + 7, 0), (X4 + 11, 0), (X4 + 11, 1)], RESIDENTIAL),
]

SIDEWALK_ONLY_MAP: list[GridWay] = [
    # 人行道是兩段住宅街之間唯一的路：盡量避免但仍要能到達
    GridWay(SIDEWALK_ONLY_WAY_ID, [(X4 + 13, 1), (X4 + 15, 1)], SIDEWALK),
    GridWay(731, [(X4 + 12, 1), (X4 + 13, 1)], RESIDENTIAL),
    GridWay(732, [(X4 + 15, 1), (X4 + 16, 1)], RESIDENTIAL),
]

CLOSED_FOOTWAY_MAP: list[GridWay] = [
    # Valhalla 3.9.1 的 footway／pedestrian 預設不給自行車走；沒有允許自行車的 tag、或明確 bicycle=no，修補後也必須照樣不通
    GridWay(CLOSED_FOOTWAY_WAY_ID, [(X4 + 19, 1), (X4 + 21, 1)], {"highway": "footway", "footway": "sidewalk"}),
    GridWay(742, [(X4 + 18, 1), (X4 + 19, 1)], RESIDENTIAL),
    GridWay(743, [(X4 + 21, 1), (X4 + 22, 1)], RESIDENTIAL),
    GridWay(NO_BICYCLE_FOOTWAY_WAY_ID, [(X4 + 19, 3), (X4 + 21, 3)], {"highway": "pedestrian", "bicycle": "no"}),
    GridWay(744, [(X4 + 18, 3), (X4 + 19, 3)], RESIDENTIAL),
    GridWay(745, [(X4 + 21, 3), (X4 + 22, 3)], RESIDENTIAL),
]


def opposite_footway_map(cycleway: str) -> list[GridWay]:
    """沒有 bicycle tag、只有 cycleway=opposite* 的人行道：lua 只把逆向（東→西）放行給自行車，順向（西→東）不通。"""
    return [
        GridWay(OPPOSITE_FOOTWAY_WAY_ID, [(X4 + 25, 1), (X4 + 27, 1)], {"highway": "footway", "cycleway": cycleway}),
        GridWay(751, [(X4 + 24, 1), (X4 + 25, 1)], RESIDENTIAL),
        GridWay(752, [(X4 + 27, 1), (X4 + 28, 1)], RESIDENTIAL),
    ]


# ---- 剪小刺與小圈（P2.5 A4）：每個場景在不同的列、彼此不相連 ----
X5 = 140  # 與其他地圖分開（search_city.py 從 200 開始）
PRUNE_SPUR_ROW = 0  # 主街旁一條約 55 m 的死巷
PRUNE_BLOCK_ROW = 2  # 主街旁一個約 50 m × 27 m 的街廓（南邊就是主街）
PRUNE_RING_ROW = 4  # 主街走到底，只有一個小環可以迴轉
PRUNE_BRIDGE_ROW = 6  # 橋跨過下面那條路（平面上相距 0.3 m，不相通），只能繞一小圈下橋
PRUNE_MOTORWAY_WAY_ID = 899
BRIDGE_GAP_COL = 0.3 / 201.7  # 橋上的節點在下面那條路東邊 0.3 m：不共用節點，剪小刺找得到這一對


def _row_way(way_id: int, row: float, cols: list[float]) -> GridWay:
    return GridWay(way_id, [(X5 + c, row) for c in cols], RESIDENTIAL)


PRUNE_MAP: list[GridWay] = [
    # 死巷：主街 (X5, 0)–(X5 + 6, 0)，X = (X5 + 3, 0) 往北到 (X5 + 3, 0.25)
    _row_way(800, PRUNE_SPUR_ROW, [0, 1, 2, 3, 4, 5, 6]),
    GridWay(801, [(X5 + 3, PRUNE_SPUR_ROW), (X5 + 3, PRUNE_SPUR_ROW + 0.25)], RESIDENTIAL),
    # 街廓：主街經過 X = (X5 + 3, 2)、Y = (X5 + 3.25, 2)；北邊三條邊 X → (X5 + 3, 2.12) → (X5 + 3.25, 2.12) → Y
    _row_way(810, PRUNE_BLOCK_ROW, [0, 1, 2, 3, 3.25, 4, 5, 6]),
    GridWay(
        811,
        [(X5 + 3 + dc, PRUNE_BLOCK_ROW + dr) for dc, dr in ((0, 0), (0, 0.12), (0.25, 0.12), (0.25, 0))],
        RESIDENTIAL,
    ),
    # 小環：主街 (X5, 4)–(X5 + 5, 4) 到 X 為止，X 接一個約 135 m 的環 X → R1 → R2 → R3 → X
    _row_way(820, PRUNE_RING_ROW, [0, 1, 2, 3, 4, 5]),
    GridWay(
        821,
        [(X5 + 5 + dc, PRUNE_RING_ROW + dr) for dc, dr in ((0, 0), (0.1, 0.08), (0.25, 0), (0.1, -0.12), (0, 0))],
        RESIDENTIAL,
    ),
    # 立體交叉：橋 (X5, 6) → K'（下面那條路東邊 0.3 m）→ E = (X5 + 3.1, 6)；下橋往南、往西接到下面那條路的南端 S，
    # 下面那條路 S = (X5 + 3, 5.9) → K = (X5 + 3, 6) → (X5 + 3, 9)。從橋上到下面那條路只能繞 K' → E → S → K（約 84 m）
    GridWay(
        830,
        [(X5, PRUNE_BRIDGE_ROW), (X5 + 3 + BRIDGE_GAP_COL, PRUNE_BRIDGE_ROW), (X5 + 3.1, PRUNE_BRIDGE_ROW)],
        {**RESIDENTIAL, "bridge": "yes", "layer": "1"},
    ),
    GridWay(831, [(X5 + 3.1, PRUNE_BRIDGE_ROW), (X5 + 3.1, PRUNE_BRIDGE_ROW - 0.1), (X5 + 3, PRUNE_BRIDGE_ROW - 0.1)], RESIDENTIAL),
    GridWay(832, [(X5 + 3, PRUNE_BRIDGE_ROW - 0.1), (X5 + 3, PRUNE_BRIDGE_ROW), (X5 + 3, PRUNE_BRIDGE_ROW + 3)], RESIDENTIAL),
    # 禁行的快速道路（bench 安全斷言用），離所有場景都遠
    GridWay(PRUNE_MOTORWAY_WAY_ID, [(X5 + c, -3) for c in range(7)], {"highway": "motorway"}),
]
