"""每個 worker 一份：依 /data/current 提供 Router，換版時自動換新（也就是熱重載）。"""

import threading
from collections.abc import Callable
from pathlib import Path

from engine.data.layout import DataLayout
from engine.routing.router import Router


class NoDataError(Exception):
    pass


class CurrentRouter[R]:
    def __init__(self, layout: DataLayout, factory: Callable[[Path], R] = Router.from_build) -> None:
        self._layout = layout
        self._factory = factory
        self._lock = threading.Lock()
        self._build: Path | None = None
        self._router: R | None = None

    def get(self) -> tuple[R, str]:
        # 讀 current 與載入必須在同一把鎖內：鎖外讀到的版本可能已過期，晚到的舊版會覆寫新版
        with self._lock:
            build = self._layout.current()
            if build is None or not build.is_dir():
                raise NoDataError("路網資料尚未就緒")
            if build != self._build or self._router is None:
                # 先建好新的再替換：正在用舊 Router 的請求會在舊實例上跑完；
                # 換版載入期間新進來的請求會排在鎖後面，等新的 Router 建好才拿到
                self._router = self._factory(build)
                self._build = build
            return self._router, build.name
