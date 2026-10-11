"""bench 的 SVG 疊圖（P2.5 spec §4.5、§8）：給人看路線像不像，不加任何繪圖依賴，手寫 SVG 字串。

- overlay_svg：目標（淡色粗線）與路線（依 kind 上色）疊在同一張圖。畫布與 v3 評分同一套正規化：
  目標外框投影到以外框中心為原點的局部平面（北朝上），長邊對齊畫布扣掉留邊；路線用同一個轉換。
  超出畫布的路線照畫，由 svg 視窗裁掉（看得出路線跑出目標範圍，又不會把目標縮小）。
- thumbnail_svg：只有路線、單色，各自縮放到框內，模擬手機動態牆上的縮圖
  （同 tests/data/likeness/source/pairs.py 的 fit：長邊貼齊框扣掉留邊、置中、北朝上）。
- case_svg：上方幾行文字（案例名稱、分數），下面疊圖與縮圖並排。
"""

import math
import re
from collections.abc import Iterable, Sequence
from xml.sax.saxutils import escape

from engine.matching.types import Segment, SegmentKind
from engine.routing.router import LonLat

OVERLAY_PX = 600
THUMB_PX = 300
MARGIN_PX = 30.0  # 疊圖的留邊：目標外框長邊 = 畫布 − 2 × 留邊
THUMB_PAD_PX = 10.0  # 縮圖的留邊（同 pairs.py 的 fit）
TITLE_LINE_PX = 20  # case_svg 上方每行文字的高度
GAP_PX = 16  # 疊圖與縮圖之間的間隔

TARGET_COLOR = "#94a3b8"
KIND_COLORS: dict[SegmentKind, str] = {"stroke": "#2563eb", "detour": "#ea580c", "connector": "#7c3aed"}
KIND_OPACITY: dict[SegmentKind, float] = {"stroke": 0.95, "detour": 0.95, "connector": 0.5}  # 連接線不是圖的一部分：畫淡
KIND_LABELS: dict[SegmentKind, str] = {"stroke": "筆畫", "detour": "繞路", "connector": "連接線"}
THUMB_COLOR = "#111827"
_FONT = 'font-family="sans-serif"'

type Px = tuple[float, float]


def safe_name(name: str) -> str:
    """案例名稱 → 檔名：字母、數字、底線、點、連字號以外的字元（`@`、`/`、空白、符號）換成底線，連續的併成一個；
    去掉頭尾的底線與點（不會變成 `..` 或隱藏檔）。全部被換掉時回傳 `case`。"""
    return re.sub(r"[^\w.\-]+", "_", name).strip("._") or "case"


def _projector(points: Iterable[LonLat]):
    """以這些點的外框中心為原點的局部平面（公尺，x 向東、y 向北），常數同 matching.score。"""
    pts = list(points)
    lons, lats = [lon for lon, _ in pts], [lat for _, lat in pts]
    lon0, lat0 = (min(lons) + max(lons)) / 2, (min(lats) + max(lats)) / 2
    kx, ky = 111_320.0 * math.cos(math.radians(lat0)), 110_574.0

    def project(line: Sequence[LonLat]) -> list[tuple[float, float]]:
        return [((lon - lon0) * kx, (lat - lat0) * ky) for lon, lat in line]

    return project


def _fitter(lines: list[list[tuple[float, float]]], size: float, margin: float):
    """局部平面 → 畫布像素：外框長邊 = size − 2 × margin、外框中心在畫布中心；SVG 的 y 向下，所以北（y 大）在上。"""
    xs = [x for line in lines for x, _ in line]
    ys = [y for line in lines for _, y in line]
    cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    extent = max(max(xs) - min(xs), max(ys) - min(ys)) or 1.0  # 只有一點時隨便給個比例，不除以 0
    scale = (size - 2 * margin) / extent

    def fit(line: list[tuple[float, float]]) -> list[Px]:
        return [(size / 2 + (x - cx) * scale, size / 2 - (y - cy) * scale) for x, y in line]

    return fit


def _points(line: list[Px]) -> str:
    """polyline 的 points；取到 0.1 px，連續重複的點只留一個。"""
    out: list[str] = []
    for x, y in line:
        p = f"{x:.1f},{y:.1f}"
        if not out or out[-1] != p:
            out.append(p)
    return " ".join(out)


def _polyline(line: list[Px], color: str, width: float, opacity: float) -> str:
    return (
        f'<polyline points="{_points(line)}" fill="none" stroke="{color}" stroke-width="{width:g}" '
        f'stroke-opacity="{opacity:g}" stroke-linecap="round" stroke-linejoin="round"/>'
    )


def _text(x: float, y: float, text: str, size: int = 12, extra: str = "") -> str:
    return f'<text x="{x:g}" y="{y:g}" font-size="{size}" {_FONT}{extra}>{escape(text)}</text>'


def _legend() -> str:
    rows = [("目標", TARGET_COLOR, 8.0, 0.45)] + [
        (KIND_LABELS[kind], KIND_COLORS[kind], 3.0, KIND_OPACITY[kind]) for kind in KIND_COLORS
    ]
    parts = [f'<rect x="6" y="6" width="96" height="{12 + 18 * len(rows)}" fill="#ffffff" fill-opacity="0.85"/>']
    for i, (label, color, width, opacity) in enumerate(rows):
        y = 22 + 18 * i
        parts.append(
            f'<line x1="14" y1="{y - 4}" x2="40" y2="{y - 4}" stroke="{color}" stroke-width="{width:g}" '
            f'stroke-opacity="{opacity:g}" stroke-linecap="round"/>'
        )
        parts.append(_text(48, y, label))
    return "".join(parts)


def _overlay_body(targets: list[list[LonLat]], segments: list[Segment], size: int) -> str:
    target_points = [p for line in targets for p in line]
    route_points = [p for s in segments for p in s.points]
    parts = [f'<rect width="{size}" height="{size}" fill="#ffffff"/>']
    anchor = target_points or route_points  # 正規化看目標；沒有目標才退而看路線
    if anchor:
        project = _projector(anchor)
        target_lines = [project(line) for line in targets if line]
        fit = _fitter(target_lines or [project(route_points)], size, MARGIN_PX)
        for line in target_lines:
            parts.append(_polyline(fit(line), TARGET_COLOR, 10, 0.45))
        for segment in segments:
            if len(segment.points) >= 2:
                parts.append(
                    _polyline(fit(project(segment.points)), KIND_COLORS[segment.kind], 2.5, KIND_OPACITY[segment.kind])
                )
    parts.append(_legend())
    return "".join(parts)


def _thumbnail_body(segments: list[Segment], size: int) -> str:
    parts = [f'<rect width="{size}" height="{size}" fill="#f8fafc"/>']
    lines = [s.points for s in segments if len(s.points) >= 2]
    if lines:
        project = _projector(p for line in lines for p in line)
        local = [project(line) for line in lines]
        fit = _fitter(local, size, THUMB_PAD_PX)
        parts.extend(_polyline(fit(line), THUMB_COLOR, 2, 1) for line in local)
    else:
        parts.append(_text(size / 2, size / 2, "（沒有路線）", extra=' text-anchor="middle" fill="#64748b"'))
    return "".join(parts)


def _svg(width: float, height: float, body: str, *, x: float | None = None, y: float | None = None) -> str:
    """最外層（帶 xmlns）或巢狀的 svg；巢狀 svg 預設 overflow hidden，超出的線在它的視窗邊界被裁掉。"""
    position = "" if x is None else f' x="{x:g}" y="{y:g}"'
    xmlns = ' xmlns="http://www.w3.org/2000/svg"' if x is None else ""
    return f'<svg{xmlns}{position} width="{width:g}" height="{height:g}" viewBox="0 0 {width:g} {height:g}">{body}</svg>'


def overlay_svg(targets: list[list[LonLat]], segments: list[Segment], *, size: int = OVERLAY_PX) -> str:
    """目標與路線的疊圖（北朝上；畫布同 v3 的外框正規化），附圖例。"""
    return _svg(size, size, _overlay_body(targets, segments, size))


def thumbnail_svg(segments: list[Segment], *, size: int = THUMB_PX) -> str:
    """只有路線的縮圖：單色、縮放到框內、北朝上（模擬手機動態牆）。"""
    return _svg(size, size, _thumbnail_body(segments, size))


def case_svg(
    name: str, targets: list[list[LonLat]], segments: list[Segment], *, extra_lines: Iterable[str] = ()
) -> str:
    """一個案例一張：上方案例名稱（粗體）與 extra_lines（分數等）各一行，下面疊圖與縮圖並排。"""
    lines = [name, *extra_lines]
    top = TITLE_LINE_PX * len(lines) + 8
    width = OVERLAY_PX + GAP_PX + THUMB_PX
    header = [
        _text(8, TITLE_LINE_PX * (i + 1), line, 14 if i == 0 else 12, ' font-weight="bold"' if i == 0 else "")
        for i, line in enumerate(lines)
    ]
    body = "".join(
        [
            f'<rect width="{width}" height="{top + OVERLAY_PX}" fill="#ffffff"/>',
            *header,
            _svg(OVERLAY_PX, OVERLAY_PX, _overlay_body(targets, segments, OVERLAY_PX), x=0, y=top),
            _svg(THUMB_PX, THUMB_PX, _thumbnail_body(segments, THUMB_PX), x=OVERLAY_PX + GAP_PX, y=top),
            _text(OVERLAY_PX + GAP_PX, top + THUMB_PX + 18, "縮圖（只有路線，模擬手機動態牆）", extra=' fill="#64748b"'),
        ]
    )
    return _svg(width, top + OVERLAY_PX, body)
