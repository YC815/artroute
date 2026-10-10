"""/data 的目錄結構：osm/ 放原始 pbf，builds/<id>/ 放各版 tiles，current 指向使用中的那版。"""

import os
import shutil
from pathlib import Path


class DataLayout:
    def __init__(self, root: Path) -> None:
        self.root = root

    @property
    def osm_dir(self) -> Path:
        return self.root / "osm"

    @property
    def builds_dir(self) -> Path:
        return self.root / "builds"

    @property
    def current_link(self) -> Path:
        return self.root / "current"

    def new_build_dir(self, build_id: str) -> Path:
        path = self.builds_dir / build_id
        path.mkdir(parents=True, exist_ok=False)
        return path

    def current(self) -> Path | None:
        if not self.current_link.is_symlink():
            return None
        return self.current_link.resolve()

    def activate(self, build_dir: Path) -> None:
        """先建暫存連結再 os.replace：rename 是原子操作，讀取端不會看到半套狀態。"""
        tmp = self.root / f".current-{build_dir.name}.tmp"
        tmp.unlink(missing_ok=True)
        tmp.symlink_to(build_dir.resolve().relative_to(self.root.resolve()))
        os.replace(tmp, self.current_link)

    def prune(self, keep: int = 2) -> list[Path]:
        builds = sorted(p for p in self.builds_dir.iterdir() if p.is_dir())
        current = self.current()
        keep_set = {p.resolve() for p in builds[-keep:]} | ({current} if current else set())
        removed = [p for p in builds if p.resolve() not in keep_set]
        for path in removed:
            shutil.rmtree(path)
        return removed
