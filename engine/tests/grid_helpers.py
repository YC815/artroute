"""合成地圖測試共用的小工具。"""

from osm_fixture import grid_point


def densify(start: tuple[float, float], end: tuple[float, float], n: int = 16) -> list[tuple[float, float]]:
    (x1, y1), (x2, y2) = start, end
    return [(x1 + (x2 - x1) * i / n, y1 + (y2 - y1) * i / n) for i in range(n + 1)]


def rides_westward_on_row(points: list[tuple[float, float]], row: int, col_min: int, col_max: int) -> bool:
    """路線是否在 row 這條街、col_min..col_max 之間往西騎。"""
    lat = grid_point(0, row)[1]
    lo, hi = grid_point(col_min, row)[0], grid_point(col_max, row)[0]
    for (x1, y1), (x2, y2) in zip(points, points[1:]):
        on_row = abs(y1 - lat) < 1e-5 and abs(y2 - lat) < 1e-5
        within = lo - 1e-6 <= min(x1, x2) and max(x1, x2) <= hi + 1e-6
        if on_row and within and x2 < x1 - 1e-7:
            return True
    return False
