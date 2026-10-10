"""產生 Valhalla 設定檔並從 pbf 建 tiles。"""

import json
import subprocess
import sys
from pathlib import Path

from valhalla import get_config


class TileBuildError(Exception):
    pass


def write_config(tile_dir: Path, config_path: Path) -> dict:
    """只用 tile_dir（不打包 tar），tile_extract 給空字串讓 Valhalla 直接讀資料夾。"""
    tile_dir.mkdir(parents=True, exist_ok=True)
    config = get_config(tile_extract="", tile_dir=tile_dir)
    config_path.write_text(json.dumps(config))
    return config


def build_tiles(pbf: Path, out_dir: Path) -> Path:
    if not pbf.is_file():
        raise TileBuildError(f"找不到 pbf：{pbf}")
    out_dir.mkdir(parents=True, exist_ok=True)
    config_path = out_dir / "valhalla.json"
    write_config(out_dir / "tiles", config_path)
    # cwd 設成 out_dir：valhalla_build_tiles 會在工作目錄寫中間檔
    result = subprocess.run(
        [sys.executable, "-m", "valhalla", "valhalla_build_tiles", "-c", str(config_path), str(pbf)],
        cwd=out_dir,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise TileBuildError(f"valhalla_build_tiles 失敗（{result.returncode}）：{result.stderr[-2000:]}")
    return config_path
