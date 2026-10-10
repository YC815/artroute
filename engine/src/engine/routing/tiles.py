"""產生 Valhalla 設定檔並從 pbf 建 tiles。"""

import json
import os
import subprocess
import sys
from pathlib import Path

from valhalla import get_config


MAX_SEARCH_RADIUS_M = 150  # spec §4.3 的 trace search_radius 上限（Valhalla 預設只有 100）


class TileBuildError(Exception):
    pass


def write_config(tile_dir: Path, config_path: Path) -> dict:
    """只用 tile_dir（不打包 tar），tile_extract 給空字串讓 Valhalla 直接讀資料夾。"""
    tile_dir = tile_dir.resolve()  # 設定檔存絕對路徑，Actor 從任何 cwd 載入都讀得到
    tile_dir.mkdir(parents=True, exist_ok=True)
    config = get_config(tile_extract="", tile_dir=tile_dir)
    config["service_limits"]["trace"]["max_search_radius"] = MAX_SEARCH_RADIUS_M
    config_path.write_text(json.dumps(config))
    return config


def build_tiles(pbf: Path, out_dir: Path) -> Path:
    # 子行程的 cwd 是 out_dir，傳進去的路徑一律先轉絕對路徑
    pbf, out_dir = pbf.resolve(), out_dir.resolve()
    if not pbf.is_file():
        raise TileBuildError(f"找不到 pbf：{pbf}")
    out_dir.mkdir(parents=True, exist_ok=True)
    config_path = out_dir / "valhalla.json"
    write_config(out_dir / "tiles", config_path)
    # `python -m valhalla` 用 PATH 找執行檔：把目前直譯器所在的 bin/ 補進去，不依賴呼叫端有沒有 activate venv
    path = os.pathsep.join(filter(None, [str(Path(sys.executable).parent), os.environ.get("PATH")]))
    env = {**os.environ, "PATH": path}
    # cwd 設成 out_dir：valhalla_build_tiles 會在工作目錄寫中間檔
    result = subprocess.run(
        [sys.executable, "-m", "valhalla", "valhalla_build_tiles", "-c", str(config_path), str(pbf)],
        cwd=out_dir,
        env=env,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise TileBuildError(f"valhalla_build_tiles 失敗（{result.returncode}）：{result.stderr[-2000:]}")
    return config_path
