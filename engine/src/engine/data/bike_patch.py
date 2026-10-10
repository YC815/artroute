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

Coord = tuple[float, float] | None  # 端點節點不在 pbf 裡（或 way 不到 2 個節點）時是 None


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
    first: Coord
    last: Coord


def _tags(obj) -> dict[str, str]:
    return {tag.k: tag.v for tag in obj.tags}


# 實測（真實 Valhalla）會讓 bicycle=no 的路重新可騎的 tag：oneway:bicycle=no 讓單行路雙向放行；
# vehicle:forward／backward 會蓋過 bicycle=no（lua：bk_forward = bicycle:forward or vehicle:forward）。
# access:forward／backward 與其他 conditional 變體實測不影響。引擎只算自行車路線，移除 vehicle 方向 tag 沒有副作用
REOPENING_KEYS = frozenset({"oneway:bicycle", "vehicle:forward", "vehicle:backward"})


def _is_bicycle_tag(key: str) -> bool:
    return key.startswith("bicycle:") or key.startswith("cycleway") or key in REOPENING_KEYS


def _denied_tags(tags: Mapping[str, str]) -> dict[str, str]:
    """bicycle=no 要蓋過所有讓這條路可騎的 tag：bicycle:forward=yes、cycleway:both=track、oneway:bicycle=no、vehicle:forward=yes 等都會讓 Valhalla 照樣放行。"""
    return {**{k: v for k, v in tags.items() if not _is_bicycle_tag(k)}, "bicycle": "no"}


def _denied(way_id: int, tags: Mapping[str, str], overrides: Overrides) -> bool:
    return (is_expressway(tags) or way_id in overrides.deny) and way_id not in overrides.allow


def patch_pbf(src: Path, dst: Path, overrides: Overrides) -> list[DeniedWay]:
    # 第一遍只看 way：找出要標記的 way 與它們首尾節點的 id（不到 2 個節點的壞資料不記端點，tag 照改）
    targets: dict[int, tuple[dict[str, str], tuple[int, int] | None]] = {}
    for way in osmium.FileProcessor(str(src), osmium.osm.WAY):
        tags = _tags(way)
        if _denied(way.id, tags, overrides):
            ends = (way.nodes[0].ref, way.nodes[-1].ref) if len(way.nodes) >= 2 else None
            targets[way.id] = (tags, ends)
    needed = {ref for _, ends in targets.values() if ends for ref in ends}

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
                    writer.add_way(obj.replace(tags=_denied_tags(targets[obj.id][0])))
                else:
                    writer.add_way(obj)
            elif obj.is_relation():
                writer.add_relation(obj)
    finally:
        writer.close()

    # 清單只是驗證用的中繼資料：缺節點時座標記 None，不能讓整個 build 掛掉
    return [
        DeniedWay(
            way_id,
            tags.get("name", ""),
            tags.get("ref", ""),
            locations.get(ends[0]) if ends else None,
            locations.get(ends[1]) if ends else None,
        )
        for way_id, (tags, ends) in sorted(targets.items())
    ]
