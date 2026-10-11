"""建置一個 build：修補自行車 tag（快速道路、樓梯禁行，人行道 dismount）→ 萃取可騎道路索引與可騎路網圖 → 建 tiles → 留下被禁行的 way 清單供驗證。"""

import json
import logging
import resource
import time
from dataclasses import asdict
from pathlib import Path

from engine.data.bike_patch import DeniedWay, load_overrides, patch_pbf
from engine.data.rideable import extract_rideable
from engine.data.ridegraph import extract_graph
from engine.routing.tiles import build_tiles

DENIED_FILE = "bicycle_denied_ways.json"
OVERRIDES_FILE = Path(__file__).with_name("bicycle_overrides.toml")

logger = logging.getLogger(__name__)


def build_with_patch(pbf: Path, build_dir: Path) -> Path:
    patched = build_dir / "patched.osm.pbf"
    try:
        denied = patch_pbf(pbf, patched, load_overrides(OVERRIDES_FILE))
        (build_dir / DENIED_FILE).write_text(json.dumps([asdict(d) for d in denied], ensure_ascii=False))
        extract_rideable(patched, build_dir)  # 與 tiles 讀同一份修補後的 pbf：禁行規則只有一處
        started = time.monotonic()
        graph = extract_graph(patched, build_dir)
        # ru_maxrss 是整個程序到目前的峰值（Linux 單位 KB），不只建圖這一步；本機量全台建圖的記憶體量級用
        logger.info("可騎路網圖建好：%d 個節點、%d 條邊、%d 個形狀點，耗時 %.1f 秒，程序記憶體峰值 %d MB",
                    graph.nodes, graph.edges, graph.coords, time.monotonic() - started,
                    resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024)
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
