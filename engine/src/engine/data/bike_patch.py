"""建圖前修補自行車通行 tag（spec §6.1）。

- 快速道路標成 bicycle=no：台灣的快速道路多半標成 highway=trunk，Valhalla 對 bicycle 視為可騎；
  但一般省道（台1線等）也是 trunk，所以用名稱、編號與 motorroad 判斷，而不是全面禁止 trunk。
- 樓梯一律標成 bicycle=no（使用者決定：禁走樓梯，沒有例外）。Valhalla 3.9.1 預設讓自行車走樓梯。
- 自行車本來就能走的人行道（footway／pedestrian）標成 bicycle=dismount：仍可通行，但 Valhalla 的預設成本
  以牽車速度計，盡量避開（使用者決定：人行道盡量避免）。Valhalla 3.9.1 的 footway／pedestrian 預設不給自行車走，
  dismount 卻會放行，所以本來不能走的不能標，否則反而多出上萬條可騎的人行道。
"""

import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import osmium

# 快速公路編號，不帶「台」。OSM 實際寫成 61、62甲（舊的帶「台」寫法在真實資料命中 0 條），比對前先去掉開頭的台／臺
EXPRESSWAY_REFS = frozenset(
    {"61", "61甲", "62", "62甲", "64", "65", "66", "68",
     "72", "74", "76", "78", "82", "84", "86", "88"}
)
NAME_KEYWORDS = ("快速道路", "快速公路", "快速路", "高架")
TRUNK = frozenset({"trunk", "trunk_link"})
STEPS = "steps"
FOOTWAYS = frozenset({"footway", "pedestrian"})
# Valhalla 3.9.1 lua 的 bicycle 表裡對應 "true" 的值：bicycle 是這些值的路自行車可以走
BICYCLE_ACCESS = frozenset(
    {"yes", "designated", "use_sidepath", "permissive", "destination", "dismount", "lane", "track", "shared",
     "shared_lane", "sidepath", "share_busway", "allowed", "private", "official", "permit", "residents"}
)
# 沒有 bicycle tag 時，lua 的 or 鏈接著讀 cycleway（cycleway 表）。opposite／opposite_lane／opposite_track 不在表裡，
# 只經 bike_reverse 放行逆向；標成 dismount 會連順向一起打開（等於放行逆向騎），所以不算
CYCLEWAY_ACCESS = frozenset(
    {"yes", "designated", "use_sidepath", "permissive", "destination", "dismount", "lane", "track", "shared",
     "shared_lane", "sidepath", "share_busway", "allowed", "private", "cyclestreet", "crossing"}
)
# cycleway:both 只認 lua 的 shared／separated／dedicated 表
CYCLEWAY_BOTH_ACCESS = frozenset(
    {"shared", "shared_lane", "share_busway", "lane", "opposite_lane", "buffered_lane", "track", "opposite_track"}
)

Coord = tuple[float, float] | None  # 端點節點不在 pbf 裡（或 way 不到 2 個節點）時是 None


def is_expressway(tags: Mapping[str, str]) -> bool:
    if tags.get("highway") not in TRUNK:
        return False
    if tags.get("motorroad") == "yes":
        return True
    if any(keyword in tags.get("name", "") for keyword in NAME_KEYWORDS):
        return True
    refs = {ref.strip().removeprefix("台").removeprefix("臺") for ref in tags.get("ref", "").split(";")}
    return bool(refs & EXPRESSWAY_REFS)


def rideable_footway(tags: Mapping[str, str]) -> bool:
    """Valhalla 3.9.1 會讓自行車走的人行道（footway／pedestrian，含 sidewalk、crossing）。

    只看最常見的放行方式：bicycle（lua 的 or 鏈以它開頭，有值就由它決定），沒有 bicycle 時再看 cycleway、cycleway:both。
    其他少見的放行方式（cycleway:left 加 right、vehicle、bicycle_road……）不處理：漏掉只是那條人行道沒被避開，不影響安全。
    """
    if tags.get("highway") not in FOOTWAYS:
        return False
    bicycle = tags.get("bicycle")
    if bicycle is not None:
        return bicycle in BICYCLE_ACCESS
    return tags.get("cycleway") in CYCLEWAY_ACCESS or tags.get("cycleway:both") in CYCLEWAY_BOTH_ACCESS


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
    highway: str = ""  # 被標記的 way 的 highway（例如 trunk、steps）；舊版清單沒有這個欄位


def _tags(obj) -> dict[str, str]:
    return {tag.k: tag.v for tag in obj.tags}


# 實測（真實 Valhalla）會讓 bicycle=no 的路重新可騎的 tag：oneway:bicycle=no 讓單行路雙向放行；
# vehicle:forward／backward 會蓋過 bicycle=no（lua：bk_forward = bicycle:forward or vehicle:forward）；
# service=driveway 且沒有 access 時 lua 讓所有交通方式通行（實測會蓋過 trunk 的 bicycle=no）。
# access:forward／backward 與其他 conditional 變體實測不影響；ramp:bicycle lua 不讀。
# 引擎只算自行車路線，移除 vehicle 方向 tag、service=driveway 沒有副作用
REOPENING_KEYS = frozenset({"oneway:bicycle", "vehicle:forward", "vehicle:backward"})


def _reopens(key: str, value: str) -> bool:
    return (
        key.startswith("bicycle:") or key.startswith("cycleway") or key in REOPENING_KEYS
        or (key, value) == ("service", "driveway")
    )


def _denied_tags(tags: Mapping[str, str]) -> dict[str, str]:
    """bicycle=no 要蓋過所有讓這條路可騎的 tag：bicycle:forward=yes、cycleway:both=track、oneway:bicycle=no、vehicle:forward=yes 等都會讓 Valhalla 照樣放行。"""
    return {**{k: v for k, v in tags.items() if not _reopens(k, v)}, "bicycle": "no"}


def _denied(way_id: int, tags: Mapping[str, str], overrides: Overrides) -> bool:
    if tags.get("highway") == STEPS:
        return True  # 使用者決定禁走樓梯：allow 清單也不例外
    return (is_expressway(tags) or way_id in overrides.deny) and way_id not in overrides.allow


def _patched_way(way, targets: dict):
    """被禁行的 way 換成 bicycle=no 的 tag；自行車能走的人行道換成 bicycle=dismount；其他原樣。"""
    if way.id in targets:
        return way.replace(tags=_denied_tags(targets[way.id][0]))
    if way.tags.get("highway") in FOOTWAYS and rideable_footway(tags := _tags(way)):
        return way.replace(tags={**tags, "bicycle": "dismount"})
    return way


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
                writer.add_way(_patched_way(obj, targets))
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
            tags.get("highway", ""),
        )
        for way_id, (tags, ends) in sorted(targets.items())
    ]
