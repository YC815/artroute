"""bench 的 SVG 疊圖：合法 XML、依路段種類上色、北朝上、文字跳脫、安全檔名；不需要任何繪圖套件。"""

import sys
import xml.etree.ElementTree as ET

import pytest

from engine.bench import svg
from engine.bench.svg import KIND_COLORS, case_svg, overlay_svg, safe_name, thumbnail_svg
from engine.matching.types import Segment
from engine.routing.geo import polyline_length_m

NS = "{http://www.w3.org/2000/svg}"
SQUARE = [[(121.500, 25.050), (121.510, 25.050), (121.510, 25.059), (121.500, 25.059), (121.500, 25.050)]]


def seg(points, kind="stroke") -> Segment:
    return Segment(kind, 0, points, polyline_length_m(points))


ROUTE = [
    seg([(121.500, 25.050), (121.510, 25.050)]),
    seg([(121.510, 25.050), (121.511, 25.055), (121.510, 25.059)], "detour"),
    seg([(121.510, 25.059), (121.500, 25.059)], "connector"),
]


def parse(text: str) -> ET.Element:
    root = ET.fromstring(text)
    assert root.tag == f"{NS}svg"
    return root


def polylines(root: ET.Element) -> list[ET.Element]:
    return list(root.iter(f"{NS}polyline"))


def coords(element: ET.Element) -> list[tuple[float, float]]:
    return [tuple(map(float, p.split(","))) for p in element.get("points", "").split()]


def test_overlay_is_valid_xml_with_one_colour_per_kind_and_a_legend() -> None:
    root = parse(overlay_svg(SQUARE, ROUTE))
    assert root.get("width") == "600" and root.get("height") == "600"
    strokes = {line.get("stroke") for line in polylines(root)}
    assert {KIND_COLORS["stroke"], KIND_COLORS["detour"], KIND_COLORS["connector"]} <= strokes
    assert len(set(KIND_COLORS.values())) == 3
    legend = "".join(t.text or "" for t in root.iter(f"{NS}text"))
    assert all(word in legend for word in ("目標", "筆畫", "繞路", "連接線"))


def test_overlay_puts_north_up_and_fits_the_target_long_side() -> None:
    root = parse(overlay_svg(SQUARE, [], size=600))
    (target,) = [line for line in polylines(root) if line.get("stroke") == svg.TARGET_COLOR]
    pts = coords(target)
    xs, ys = [x for x, _ in pts], [y for _, y in pts]
    # 外框長邊（東西向 1 km）貼齊畫布扣掉留邊
    assert max(xs) - min(xs) == pytest.approx(600 - 2 * svg.MARGIN_PX, abs=0.2)
    assert max(ys) - min(ys) < max(xs) - min(xs)
    # 第一點在西南角、第三點在東北角：SVG 的 y 往下，北朝上就是北邊的 y 比較小
    (x0, y0), (x2, y2) = pts[0], pts[2]
    assert x0 < x2 and y0 > y2
    assert (min(xs) + max(xs)) / 2 == pytest.approx(300, abs=0.2) and (min(ys) + max(ys)) / 2 == pytest.approx(300, abs=0.2)


def test_overlay_draws_the_route_with_the_same_transform_as_the_target() -> None:
    root = parse(overlay_svg(SQUARE, [seg(SQUARE[0])]))
    target, route = [coords(line) for line in polylines(root)]
    assert route == target


def test_connector_is_fainter_than_stroke_and_detour() -> None:
    root = parse(overlay_svg(SQUARE, ROUTE))
    opacity = {line.get("stroke"): float(line.get("stroke-opacity", "1")) for line in polylines(root)}
    assert opacity[KIND_COLORS["connector"]] < min(opacity[KIND_COLORS["stroke"]], opacity[KIND_COLORS["detour"]])


def test_thumbnail_is_route_only_fitted_to_its_own_box() -> None:
    small = [seg([(121.500, 25.050), (121.501, 25.050)]), seg([(121.501, 25.050), (121.501, 25.0505)], "connector")]
    root = parse(thumbnail_svg(small, size=300))
    lines = polylines(root)
    assert len({line.get("stroke") for line in lines}) == 1  # 單色，不分種類
    pts = [p for line in lines for p in coords(line)]
    xs, ys = [x for x, _ in pts], [y for _, y in pts]
    assert max(xs) - min(xs) == pytest.approx(300 - 2 * svg.THUMB_PAD_PX, abs=0.2)  # 100 m 的路線也放大到框內
    assert max(ys) < 300 and min(ys) > 0
    north = coords(lines[1])[1]
    assert north[1] < coords(lines[1])[0][1]  # 往北騎：y 變小


def test_thumbnail_without_route_is_still_valid() -> None:
    root = parse(thumbnail_svg([]))
    assert not polylines(root)


def test_case_svg_puts_overlay_and_thumbnail_side_by_side_under_a_title() -> None:
    root = parse(case_svg("heart@taipei", SQUARE, ROUTE, extra_lines=["v3 80 dir 70", "v2 75"]))
    texts = [t.text or "" for t in root.iter(f"{NS}text")]
    assert texts[:3] == ["heart@taipei", "v3 80 dir 70", "v2 75"]
    panels = [child for child in root if child.tag == f"{NS}svg"]
    assert [p.get("width") for p in panels] == ["600", "300"]
    assert float(panels[1].get("x")) >= 600
    assert float(panels[0].get("y")) > 0  # 標題在上面


def test_text_is_escaped() -> None:
    name = 'a<b>&"c"@d'
    text = case_svg(name, SQUARE, ROUTE, extra_lines=["x < y & z"])
    texts = [t.text for t in parse(text).iter(f"{NS}text")]
    assert name in texts and "x < y & z" in texts
    assert "<b>" not in text


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("heart@taipei_daan@R5", "heart_taipei_daan_R5"),
        ("a/b c", "a_b_c"),
        ("../x", "x"),
        ("I ♥ TAIPEI", "I_TAIPEI"),
        ("", "case"),
        ("@@", "case"),
    ],
)
def test_safe_name(name: str, expected: str) -> None:
    assert safe_name(name) == expected


def test_no_plotting_dependency() -> None:
    assert "matplotlib" not in sys.modules
