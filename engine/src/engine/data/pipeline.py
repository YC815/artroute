"""建置 → 驗證 → 啟用。任何一步失敗都刪掉新版、保留舊版繼續服務。"""

import json
import shutil
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from engine.data.layout import DataLayout

META_FILE = "meta.json"


@dataclass(frozen=True)
class BuildMeta:
    build_id: str
    osm_md5: str
    created_at: str


class ValidationFailed(Exception):
    pass


def run_build(
    layout: DataLayout,
    pbf: Path,
    osm_md5: str,
    build_id: str,
    builder: Callable[[Path, Path], object],
    validator: Callable[[Path], list[str]],
) -> BuildMeta:
    build_dir = layout.new_build_dir(build_id)
    try:
        builder(pbf, build_dir)
        failures = validator(build_dir)
        if failures:
            raise ValidationFailed("；".join(failures))
    except BaseException:
        shutil.rmtree(build_dir, ignore_errors=True)
        raise
    meta = BuildMeta(build_id=build_id, osm_md5=osm_md5, created_at=datetime.now(UTC).isoformat())
    (build_dir / META_FILE).write_text(json.dumps(asdict(meta)))
    layout.activate(build_dir)
    layout.prune(keep=2)
    return meta


def read_meta(build_dir: Path) -> BuildMeta | None:
    path = build_dir / META_FILE
    if not path.is_file():
        return None
    return BuildMeta(**json.loads(path.read_text()))
