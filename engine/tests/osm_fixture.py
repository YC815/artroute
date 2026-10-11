"""用 pyosmium 產生小型合成地圖 pbf，讓 Valhalla 測試結果固定、可重現。"""

import math
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


# ---- 描形規劃器的場景（P2.5 spec §8）：座標以公尺給（東, 北），原點 PLANNER_ORIGIN（與上面的網格地圖分開）。
# 每個場景各自的 x 偏移相距 10 km，放進同一個 pbf 也不會互相干擾。目標也用同樣的公尺座標。 ----

PLANNER_ORIGIN = (121.20, 24.90)
_METERS_PER_DEG_LAT = 6_371_008.8 * math.pi / 180


def meter_point(x: float, y: float) -> tuple[float, float]:
    """(東, 北) 公尺 → (lon, lat)，取到 pbf 的精度（1e-7 度），讀回來的座標與寫入的完全相等。"""
    lon = PLANNER_ORIGIN[0] + x / (_METERS_PER_DEG_LAT * math.cos(math.radians(PLANNER_ORIGIN[1])))
    return (round(lon, 7), round(PLANNER_ORIGIN[1] + y / _METERS_PER_DEG_LAT, 7))


def to_meters(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """meter_point 的反函數（不含取整）。"""
    kx = _METERS_PER_DEG_LAT * math.cos(math.radians(PLANNER_ORIGIN[1]))
    return [((lon - PLANNER_ORIGIN[0]) * kx, (lat - PLANNER_ORIGIN[1]) * _METERS_PER_DEG_LAT) for lon, lat in points]


@dataclass(frozen=True)
class MeterWay:
    id: int
    nodes: list[tuple[float, float]]  # (東, 北) 公尺；座標相同（取整後）的點是同一個 OSM 節點
    tags: dict[str, str] = field(default_factory=dict)


def write_meter_pbf(path: Path, ways: list[MeterWay]) -> None:
    node_ids: dict[tuple[float, float], int] = {}
    for way in ways:
        for xy in way.nodes:
            node_ids.setdefault(meter_point(*xy), len(node_ids) + 1)
    writer = osmium.SimpleWriter(str(path))
    try:
        for location, node_id in sorted(node_ids.items(), key=lambda item: item[1]):
            writer.add_node(osmium.osm.mutable.Node(id=node_id, location=location, version=1))
        for way in sorted(ways, key=lambda w: w.id):
            refs = [node_ids[meter_point(*xy)] for xy in way.nodes]
            writer.add_way(osmium.osm.mutable.Way(id=way.id, nodes=refs, tags=way.tags, version=1))
    finally:
        writer.close()


def _shift(points: list[tuple[float, float]], dx: float) -> list[tuple[float, float]]:
    return [(x + dx, y) for x, y in points]


# 平行路：目標是 1600 m 的東西向直線（L = 1600、Δ = 16 m、候選半徑 80 m）。正下方的路在 600–1000 m 斷掉；
# 40 m 外有完整的平行路，兩者在 0、400、1200、1600 m 有連絡道。斷口北方 300 m 有一條繞過去的路（「繞出去再繞回來」）。
PARALLEL_X = 0.0
PARALLEL_TARGET = _shift([(0.0, 0.0), (400.0, 0.0), (800.0, 0.0), (1200.0, 0.0), (1600.0, 0.0)], PARALLEL_X)
PARALLEL_MAP: list[MeterWay] = [
    MeterWay(1001, _shift([(0.0, 0.0), (200.0, 0.0), (400.0, 0.0), (600.0, 0.0)], PARALLEL_X), RESIDENTIAL),
    MeterWay(1002, _shift([(1000.0, 0.0), (1200.0, 0.0), (1400.0, 0.0), (1600.0, 0.0)], PARALLEL_X), RESIDENTIAL),
    MeterWay(1003, _shift([(float(x), -40.0) for x in range(0, 1601, 200)], PARALLEL_X), RESIDENTIAL),
    *[MeterWay(1010 + i, _shift([(x, 0.0), (x, -40.0)], PARALLEL_X), RESIDENTIAL)
      for i, x in enumerate((0.0, 400.0, 1200.0, 1600.0))],
    MeterWay(1020, _shift([(600.0, 0.0), (600.0, 150.0), (600.0, 300.0), (800.0, 300.0), (1000.0, 300.0),
                           (1000.0, 150.0), (1000.0, 0.0)], PARALLEL_X), RESIDENTIAL),
]

# 轉角：目標向東 800 m 後左轉 60° 再走 120 m（像字母的短筆畫；L = 860、Δ = 15 m、候選半徑 43 m、免費平移 8.6 m、
# 轉角窗 43 m）。東西向的路延伸過轉角。兩種轉法：
# - 提早 15 m 的 60° 岔路：角度一樣，但與第二段平行、相距約 13 m（超出免費平移）；
# - 轉角正下方 85° 的路口：往北 20 m 後右轉 25°（不到轉彎門檻）接上與第二段平行、相距約 8.5 m 的路（免費平移內）。
# 只看位置是第二種比較近；轉角的角度差讓第一種勝出。
CORNER_X = 10_000.0


def _polar(origin: tuple[float, float], degrees: float, distance: float) -> tuple[float, float]:
    return (origin[0] + distance * math.cos(math.radians(degrees)), origin[1] + distance * math.sin(math.radians(degrees)))


CORNER_TARGET = _shift([(0.0, 0.0), (800.0, 0.0), _polar((800.0, 0.0), 60, 120)], CORNER_X)
CORNER_FORK = _shift([(785.0, 0.0), _polar((785.0, 0.0), 60, 250)], CORNER_X)
_CORNER_BEND = _polar((800.0, 0.0), 85, 20)
CORNER_NEAR = _shift([(800.0, 0.0), _CORNER_BEND, _polar(_CORNER_BEND, 60, 200)], CORNER_X)  # 85° 路口那條路
CORNER_MAP: list[MeterWay] = [
    MeterWay(2001, _shift([(-100.0, 0.0), (100.0, 0.0), (300.0, 0.0), (500.0, 0.0), (700.0, 0.0), (785.0, 0.0),
                           (800.0, 0.0), (1000.0, 0.0)], CORNER_X), RESIDENTIAL),
    MeterWay(2002, CORNER_FORK, RESIDENTIAL),
    MeterWay(2003, CORNER_NEAR, RESIDENTIAL),
]

# 斜路：70 m 的棋盤，目標是穿過棋盤節點的 45° 斜線（L = 1960、Δ = 19.6 m、候選半徑 98 m）。斜路與目標平行、往東南偏一個
# 街廓（約 49.5 m），經過棋盤節點。貼著目標的鋸齒（每 70 m 轉一次）離目標比較近，但方向差 45°、轉彎多。
DIAGONAL_X = 20_000.0
DIAGONAL_STEP = 70.0
DIAGONAL_TARGET = _shift([(0.0, 0.0), (1960.0, 1960.0)], DIAGONAL_X)
_DIAGONAL_LINES = range(-2, 31)  # 棋盤線：-140 m 到 2100 m
DIAGONAL_ROAD = _shift([((k + 1) * DIAGONAL_STEP, k * DIAGONAL_STEP) for k in range(-2, 30)], DIAGONAL_X)
DIAGONAL_MAP: list[MeterWay] = [
    *[MeterWay(3000 + i, _shift([(c * DIAGONAL_STEP, r * DIAGONAL_STEP) for c in _DIAGONAL_LINES], DIAGONAL_X),
               RESIDENTIAL) for i, r in enumerate(_DIAGONAL_LINES)],
    *[MeterWay(3100 + i, _shift([(c * DIAGONAL_STEP, r * DIAGONAL_STEP) for r in _DIAGONAL_LINES], DIAGONAL_X),
               RESIDENTIAL) for i, c in enumerate(_DIAGONAL_LINES)],
    MeterWay(3200, DIAGONAL_ROAD, RESIDENTIAL),
]

# 單行道：目標是向東 1200 m 的直線（L = 1200、Δ = 15 m、候選半徑 60 m）。正下方的路分五種：
# 200–400 m oneway=yes 但畫成往西（只能往西）；400–600 m oneway=-1 畫成往東（只能往西）；
# 600–800 m oneway=yes＋oneway:bicycle=no 畫成往西（自行車雙向）；800–1000 m oneway=yes＋cycleway=opposite_lane 畫成往西
# （自行車可以逆向往東）；1080–1120 m 是一段只有 40 m、畫成往西的 oneway=yes（短到一次轉移就能穿過：Dijkstra
# 必須有方向）。40 m 外有雙向的平行路，每 200 m 一條連絡道，短單行道兩側 1060、1140 m 也有。
ONEWAY_X = 30_000.0
ONEWAY_TARGET = _shift([(0.0, 0.0), (1200.0, 0.0)], ONEWAY_X)
ONEWAY_MAP: list[MeterWay] = [
    MeterWay(4001, _shift([(0.0, 0.0), (200.0, 0.0)], ONEWAY_X), RESIDENTIAL),
    MeterWay(4002, _shift([(400.0, 0.0), (200.0, 0.0)], ONEWAY_X), {**RESIDENTIAL, "oneway": "yes"}),
    MeterWay(4003, _shift([(400.0, 0.0), (600.0, 0.0)], ONEWAY_X), {**RESIDENTIAL, "oneway": "-1"}),
    MeterWay(4004, _shift([(800.0, 0.0), (600.0, 0.0)], ONEWAY_X), {**RESIDENTIAL, "oneway": "yes", "oneway:bicycle": "no"}),
    MeterWay(4005, _shift([(1000.0, 0.0), (800.0, 0.0)], ONEWAY_X),
             {**RESIDENTIAL, "oneway": "yes", "cycleway": "opposite_lane"}),
    MeterWay(4006, _shift([(1000.0, 0.0), (1060.0, 0.0), (1080.0, 0.0)], ONEWAY_X), RESIDENTIAL),
    MeterWay(4008, _shift([(1120.0, 0.0), (1080.0, 0.0)], ONEWAY_X), {**RESIDENTIAL, "oneway": "yes"}),
    MeterWay(4009, _shift([(1120.0, 0.0), (1140.0, 0.0), (1200.0, 0.0)], ONEWAY_X), RESIDENTIAL),
    MeterWay(4007, _shift([(float(x), -40.0) for x in (0, 200, 400, 600, 800, 1000, 1060, 1140, 1200)], ONEWAY_X),
             RESIDENTIAL),
    *[MeterWay(4010 + i, _shift([(float(x), 0.0), (float(x), -40.0)], ONEWAY_X), RESIDENTIAL)
      for i, x in enumerate((0, 200, 400, 600, 800, 1000, 1060, 1140, 1200))],
]

# 小刺：目標向東 600 m、往北 55 m 再原路退回、再向東 600 m（L = 1200、候選半徑 60 m）。目標下方的直路在 600 m 有一條
# 55 m 的死巷正好沿著目標往北的那一段：照著畫就要在死巷底迴轉（騎進去又退出來）。
SPIKE_X = 40_000.0
SPIKE_TARGET = _shift([(0.0, 0.0), (600.0, 0.0), (600.0, 55.0), (600.0, 0.0), (1200.0, 0.0)], SPIKE_X)
SPIKE_MAP: list[MeterWay] = [
    MeterWay(5001, _shift([(-100.0, 0.0), (200.0, 0.0), (400.0, 0.0), (600.0, 0.0), (800.0, 0.0), (1000.0, 0.0),
                           (1300.0, 0.0)], SPIKE_X), RESIDENTIAL),
    MeterWay(5002, _shift([(600.0, 0.0), (600.0, 55.0)], SPIKE_X), RESIDENTIAL),
]

# 封閉筆畫：100 m 的棋盤上 800 m 見方的正方形目標，往東北偏 10 m（平移免費的範圍內）。
SQUARE_X = 50_000.0
SQUARE_TARGET = _shift([(10.0, 10.0), (810.0, 10.0), (810.0, 810.0), (10.0, 810.0)], SQUARE_X)
SQUARE_MAP: list[MeterWay] = [
    *[MeterWay(6000 + r, _shift([(c * 100.0, r * 100.0) for c in range(-1, 11)], SQUARE_X), RESIDENTIAL)
      for r in range(-1, 11)],
    *[MeterWay(6100 + c, _shift([(c * 100.0, r * 100.0) for r in range(-1, 11)], SQUARE_X), RESIDENTIAL)
      for c in range(-1, 11)],
]

# 斷開：目標是向東 1400 m 的直線，每 100 m 一個輸入點（L = 1400、候選半徑 70 m）。下方的路在 500–900 m 之間什麼都沒有
# （沒有候選）；1030–1060 m 之間兩段路的端點只差 30 m（都有候選，但不相通：轉移不可達）。
GAP_X = 60_000.0
GAP_TARGET = _shift([(float(x), 0.0) for x in range(0, 1401, 100)], GAP_X)
GAP_MAP: list[MeterWay] = [
    MeterWay(7001, _shift([(0.0, 0.0), (250.0, 0.0), (500.0, 0.0)], GAP_X), RESIDENTIAL),
    MeterWay(7002, _shift([(900.0, 0.0), (1030.0, 0.0)], GAP_X), RESIDENTIAL),
    MeterWay(7003, _shift([(1060.0, 0.0), (1250.0, 0.0), (1400.0, 0.0)], GAP_X), RESIDENTIAL),
]

# 部分退回 fast：目標是向東 1400 m 的直線，每 100 m 一個輸入點（L = 1400、候選半徑 70 m、fast 的搜尋半徑 49 m）。
# 500–900 m 之間的路往北繞 120 m：中間那段目標 70 m 內沒有路（規劃器斷開、交給 fast），fast 的 trace 也吸不到，
# 要靠合法繞路；兩側照著走。
DETOUR_X = 70_000.0
DETOUR_TARGET = _shift([(float(x), 0.0) for x in range(0, 1401, 100)], DETOUR_X)
DETOUR_MAP: list[MeterWay] = [
    MeterWay(8001, _shift([(0.0, 0.0), (250.0, 0.0), (500.0, 0.0)], DETOUR_X), RESIDENTIAL),
    MeterWay(8002, _shift([(500.0, 0.0), (500.0, 120.0), (700.0, 120.0), (900.0, 120.0), (900.0, 0.0)], DETOUR_X),
             RESIDENTIAL),
    MeterWay(8003, _shift([(900.0, 0.0), (1150.0, 0.0), (1400.0, 0.0)], DETOUR_X), RESIDENTIAL),
]

# 照著走的結果也會被剪：向東 1200 m 的直路，600 m 處往北一條 9 m 的死巷（短到 Valhalla 繞進去再出來的結果與規劃路徑的
# Hausdorff 仍 ≤ 10 m，會被收下；又長過剪小刺的最短長度 MIN_LOOP_M = 16 m 的一半）。目標就是這條直路。
SHORT_SPUR_X = 80_000.0
SHORT_SPUR_TIP = (600.0 + SHORT_SPUR_X, 9.0)
SHORT_SPUR_TARGET = _shift([(0.0, 0.0), (1200.0, 0.0)], SHORT_SPUR_X)
SHORT_SPUR_MAP: list[MeterWay] = [
    MeterWay(9001, _shift([(float(x), 0.0) for x in range(-100, 1301, 100)], SHORT_SPUR_X), RESIDENTIAL),
    MeterWay(9002, [(600.0 + SHORT_SPUR_X, 0.0), SHORT_SPUR_TIP], RESIDENTIAL),
]

# 重疊的 footway 與 cycleway：兩條 way 共用同一組節點與形狀（第 0 排：兩條畫同方向；第 1 排：cycleway 畫反方向），
# 兩端各接一條 residential。edge_walk 沿這個形狀走會一半配到 cycleway（6002／6022）、一半配到 footway（6001／6021，
# traversability: none），bench 的安全斷言因此誤報逆向（真實路網 hi@taipei_daan 的 connector 就是這樣）；
# 自行車的 route 只會走 cycleway。
OVERLAP_X = 90_000.0
OVERLAP_ROW_M = 100.0


def overlap_shape(row: int, *, reverse: bool = False) -> list[tuple[float, float]]:
    y = row * OVERLAP_ROW_M
    shape = [(OVERLAP_X, y), (OVERLAP_X + 30.0, y + 4.0), (OVERLAP_X + 60.0, y)]
    return shape[::-1] if reverse else shape


OVERLAP_MAP: list[MeterWay] = [
    way
    for row, (footway_id, cycleway_id, cycleway_reversed) in enumerate([(6001, 6002, False), (6021, 6022, True)])
    for way in (
        MeterWay(footway_id, overlap_shape(row), {"highway": "footway"}),
        MeterWay(cycleway_id, overlap_shape(row, reverse=cycleway_reversed), {"highway": "cycleway"}),
        MeterWay(6100 + 2 * row, [(OVERLAP_X, row * OVERLAP_ROW_M), (OVERLAP_X - 50.0, row * OVERLAP_ROW_M)], RESIDENTIAL),
        MeterWay(6101 + 2 * row, [(OVERLAP_X + 60.0, row * OVERLAP_ROW_M), (OVERLAP_X + 110.0, row * OVERLAP_ROW_M)],
                 RESIDENTIAL),
    )
]

# 所有規劃器場景放進同一個 pbf（各自相距 10 km，互不干擾）：tests/conftest.py 的 planner_build 用它建 tiles 與路網圖
PLANNER_MAPS: list[MeterWay] = [
    *PARALLEL_MAP, *CORNER_MAP, *DIAGONAL_MAP, *ONEWAY_MAP, *SPIKE_MAP, *SQUARE_MAP, *GAP_MAP, *DETOUR_MAP,
    *SHORT_SPUR_MAP, *OVERLAP_MAP,
]


# ---- 剪小刺與小圈（P2.5 A4）：每個場景在不同的列、彼此不相連 ----
X5 = 140  # 與其他地圖分開（search_city.py 從 200 開始）
PRUNE_SPUR_ROW = 0  # 主街旁一條約 55 m 的死巷
PRUNE_BLOCK_ROW = 2  # 主街旁一個約 50 m × 27 m 的街廓（南邊就是主街）
PRUNE_RING_ROW = 4  # 主街走到底，只有一個小環可以迴轉
PRUNE_BRIDGE_ROW = 6  # 橋跨過下面那條路（平面上相距 0.3 m，不相通），只能繞一小圈下橋
PRUNE_UTURN_ROW = 8  # 剪了會在路口旁 5 m 的路中間迴轉（兩側的頂點不同）
UTURN_N_COL = 13  # 路口 N（往東錯開，不與立體交叉那條南北向的路重疊）
UTURN_J_COL = UTURN_N_COL + 5 / 201.7  # J 在路口 N 東邊 5 m
UTURN_E1_COL = UTURN_J_COL + 20 / 201.7
UTURN_LOOP_ROWS = 12 / 222.4
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
    # 路中間迴轉（審查 B1）：主街 W = (X5 + 10, 8) → 路口 N = (X5 + 13, 8) → 東 5 m 的 J → 再東 20 m 的 E1；
    # J 接一個往南的小圈 E1 → SE → S → J（約 64 m）；N 往北一條街到 (X5 + 13, 10)
    GridWay(
        840,
        [(X5 + c, PRUNE_UTURN_ROW) for c in (UTURN_N_COL - 3, UTURN_N_COL, UTURN_J_COL, UTURN_E1_COL)],
        RESIDENTIAL,
    ),
    GridWay(
        841,
        [
            (X5 + UTURN_E1_COL, PRUNE_UTURN_ROW),
            (X5 + UTURN_E1_COL, PRUNE_UTURN_ROW - UTURN_LOOP_ROWS),
            (X5 + UTURN_J_COL, PRUNE_UTURN_ROW - UTURN_LOOP_ROWS),
            (X5 + UTURN_J_COL, PRUNE_UTURN_ROW),
        ],
        RESIDENTIAL,
    ),
    GridWay(842, [(X5 + UTURN_N_COL, PRUNE_UTURN_ROW), (X5 + UTURN_N_COL, PRUNE_UTURN_ROW + 2)], RESIDENTIAL),
    # 禁行的快速道路（bench 安全斷言用），離所有場景都遠
    GridWay(PRUNE_MOTORWAY_WAY_ID, [(X5 + c, -3) for c in range(7)], {"highway": "motorway"}),
]
