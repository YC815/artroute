"""自動搜尋的合成城市：10 × 10 的棋盤街道，街廓是約 222 m 的正方形（約 2 × 2 km），其中一條東西向是單行道。"""

from osm_fixture import GridWay, grid_point

COL0 = 200  # 與 osm_fixture 的其他地圖分開
SIZE = 10
ONEWAY_ROW = 4
RESIDENTIAL = {"highway": "residential"}
# osm_fixture 的網格經緯度步長相同（0.002°），在 25° N 東西向約 201.7 m、南北向約 222.4 m；
# 欄距乘上這個比例讓街廓變成正方形，正方形的圖形才擺得上街道
COL_SCALE = 1.103


def node(c: int, r: int) -> tuple[float, int]:
    return (COL0 + c * COL_SCALE, r)

CITY_MAP: list[GridWay] = [
    *[
        GridWay(9000 + r, [node(c, r) for c in range(SIZE)], {**RESIDENTIAL, "oneway": "yes"} if r == ONEWAY_ROW else RESIDENTIAL)
        for r in range(SIZE)
    ],
    *[GridWay(9100 + c, [node(c, r) for r in range(SIZE)], RESIDENTIAL) for c in range(SIZE)],
]

CITY_CENTER = grid_point(COL0 + (SIZE - 1) / 2 * COL_SCALE, (SIZE - 1) / 2)

