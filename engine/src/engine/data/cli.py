"""完整建置一次台灣路網：python -m engine.data.cli（在引擎容器裡執行）。"""

import json
import logging
import os
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

import httpx

from engine.data.build import build_with_patch
from engine.data.checks import validate_build
from engine.data.download import GEOFABRIK_TAIWAN, download_with_md5
from engine.data.layout import DataLayout
from engine.data.pipeline import run_build


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")  # 建圖紀錄（例如可騎道路的路段數）
    layout = DataLayout(Path(os.environ.get("DATA_DIR", "/data")))
    layout.osm_dir.mkdir(parents=True, exist_ok=True)
    pbf = layout.osm_dir / "taiwan-latest.osm.pbf"
    timeout = httpx.Timeout(30.0, read=300.0)
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        md5 = download_with_md5(GEOFABRIK_TAIWAN, pbf, client)
    build_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    meta = run_build(layout, pbf, md5, build_id, builder=build_with_patch, validator=validate_build)
    print(json.dumps(asdict(meta), ensure_ascii=False))


if __name__ == "__main__":
    main()
