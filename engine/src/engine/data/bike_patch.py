"""建圖前把台灣快速道路標成 bicycle=no（spec §6.1）。

台灣的快速道路多半標成 highway=trunk，Valhalla 對 bicycle 視為可騎；
但一般省道（台1線等）也是 trunk，所以用名稱、編號與 motorroad 判斷，而不是全面禁止 trunk。
"""

import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import osmium

EXPRESSWAY_REFS = frozenset(
    {"台61", "台61甲", "台62", "台62甲", "台64", "台65", "台66", "台68",
     "台72", "台74", "台76", "台78", "台82", "台84", "台86", "台88"}
)
NAME_KEYWORDS = ("快速道路", "快速公路", "快速路", "高架")
TRUNK = frozenset({"trunk", "trunk_link"})


def is_expressway(tags: Mapping[str, str]) -> bool:
    if tags.get("highway") not in TRUNK:
        return False
    if tags.get("motorroad") == "yes":
        return True
    if any(keyword in tags.get("name", "") for keyword in NAME_KEYWORDS):
        return True
    refs = {ref.strip() for ref in tags.get("ref", "").split(";")}
    return bool(refs & EXPRESSWAY_REFS)


@dataclass(frozen=True)
class Overrides:
    deny: frozenset[int]
    allow: frozenset[int]


def load_overrides(path: Path) -> Overrides:
    data = tomllib.loads(path.read_text())
    return Overrides(deny=frozenset(data.get("deny", [])), allow=frozenset(data.get("allow", [])))


@dataclass(frozen=True)
class DeniedWay:
    id: int
    name: str
    ref: str
    first: tuple[float, float]
    last: tuple[float, float]


def _tags(obj) -> dict[str, str]:
    return {tag.k: tag.v for tag in obj.tags}


def _denied(way_id: int, tags: Mapping[str, str], overrides: Overrides) -> bool:
    return (is_expressway(tags) or way_id in overrides.deny) and way_id not in overrides.allow


def patch_pbf(src: Path, dst: Path, overrides: Overrides) -> list[DeniedWay]:
    # 第一遍只看 way：找出要標記的 way 與它們首尾節點的 id
    targets: dict[int, tuple[dict[str, str], int, int]] = {}
    for way in osmium.FileProcessor(str(src), osmium.osm.WAY):
        tags = _tags(way)
        if _denied(way.id, tags, overrides):
            targets[way.id] = (tags, way.nodes[0].ref, way.nodes[-1].ref)
    needed = {ref for _, first, last in targets.values() for ref in (first, last)}

    # 第二遍完整複製；節點在 way 之前，順便記下需要的座標
    locations: dict[int, tuple[float, float]] = {}
    writer = osmium.SimpleWriter(str(dst), overwrite=True)
    try:
        for obj in osmium.FileProcessor(str(src)):
            if obj.is_node():
                if obj.id in needed:
                    locations[obj.id] = (obj.location.lon, obj.location.lat)
                writer.add_node(obj)
            elif obj.is_way():
                if obj.id in targets:
                    writer.add_way(obj.replace(tags={**targets[obj.id][0], "bicycle": "no"}))
                else:
                    writer.add_way(obj)
            elif obj.is_relation():
                writer.add_relation(obj)
    finally:
        writer.close()

    return [
        DeniedWay(way_id, tags.get("name", ""), tags.get("ref", ""), locations[first], locations[last])
        for way_id, (tags, first, last) in sorted(targets.items())
    ]
