"""HTTP 介面。只在 hl 內網提供服務，由 web 轉發請求。"""

import os
from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI

from engine.data.layout import DataLayout
from engine.data.pipeline import read_meta


def create_app(data_root: Path) -> FastAPI:
    app = FastAPI(title="artroute-engine")
    layout = DataLayout(data_root)

    @app.get("/")
    def root() -> dict[str, str]:
        return {"service": "artroute-engine"}

    @app.get("/health")
    def health() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/status")
    def status() -> dict[str, dict[str, str] | None]:
        current = layout.current()
        meta = read_meta(current) if current else None
        return {"data": asdict(meta) if meta else None}

    return app


app = create_app(Path(os.environ.get("DATA_DIR", "/data")))
