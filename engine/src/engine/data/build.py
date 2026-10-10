"""建置一個 build：修補自行車 tag（快速道路、樓梯禁行，人行道 dismount）→ 建 tiles → 留下被禁行的 way 清單供驗證。"""

import json
from dataclasses import asdict
from pathlib import Path

from engine.data.bike_patch import DeniedWay, load_overrides, patch_pbf
from engine.routing.tiles import build_tiles

DENIED_FILE = "bicycle_denied_ways.json"
OVERRIDES_FILE = Path(__file__).with_name("bicycle_overrides.toml")


def build_with_patch(pbf: Path, build_dir: Path) -> Path:
    patched = build_dir / "patched.osm.pbf"
    try:
        denied = patch_pbf(pbf, patched, load_overrides(OVERRIDES_FILE))
        (build_dir / DENIED_FILE).write_text(json.dumps([asdict(d) for d in denied], ensure_ascii=False))
        return build_tiles(patched, build_dir)
    finally:
        patched.unlink(missing_ok=True)


def load_denied(build_dir: Path) -> list[DeniedWay]:
    raw = json.loads((build_dir / DENIED_FILE).read_text())
    return [
        DeniedWay(d["id"], d["name"], d["ref"], _coord(d["first"]), _coord(d["last"]), d.get("highway", ""))
        for d in raw
    ]


def _coord(raw: list[float] | None) -> tuple[float, float] | None:
    return None if raw is None else (raw[0], raw[1])
