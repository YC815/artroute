"""HTTP 介面。只在 hl 內網提供服務，由 web 轉發請求。"""

import os
from pathlib import Path

from fastapi import FastAPI


def create_app(data_root: Path) -> FastAPI:
    app = FastAPI(title="artroute-engine")
    app.state.data_root = data_root

    @app.get("/")
    def root() -> dict[str, str]:
        return {"service": "artroute-engine"}

    @app.get("/health")
    def health() -> dict[str, bool]:
        return {"ok": True}

    return app


app = create_app(Path(os.environ.get("DATA_DIR", "/data")))
