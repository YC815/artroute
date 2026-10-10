import threading
import time
from pathlib import Path

import pytest

from engine.data.layout import DataLayout
from engine.service.current import CurrentRouter, NoDataError


class CountingFactory:
    def __init__(self) -> None:
        self.built: list[Path] = []

    def __call__(self, build_dir: Path) -> str:
        self.built.append(build_dir)
        return f"router-for-{build_dir.name}"


class FlakyFactory(CountingFactory):
    """載入 fail_for 這版會丟例外，用來驗證失敗不被快取。"""

    def __init__(self, fail_for: Path) -> None:
        super().__init__()
        self.fail_for = fail_for

    def __call__(self, build_dir: Path) -> str:
        if build_dir == self.fail_for:
            self.built.append(build_dir)
            raise RuntimeError("載入失敗")
        return super().__call__(build_dir)


class SlowFactory(CountingFactory):
    """載入要花時間，讓並發請求有機會同時撞上。"""

    def __call__(self, build_dir: Path) -> str:
        time.sleep(0.05)
        return super().__call__(build_dir)


class BlockingLayout(DataLayout):
    """thread S 第一次讀 current 時先讀下舊值再停住，等測試放行，用來重現 TOCTOU 的交錯。"""

    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self.entered = threading.Event()
        self.release = threading.Event()
        self._blocked = False

    def current(self) -> Path | None:
        stale = super().current()
        if not self._blocked and threading.current_thread().name == "S":
            self._blocked = True
            self.entered.set()
            self.release.wait(timeout=5)
        return stale


def test_no_current_raises_no_data(tmp_path: Path) -> None:
    with pytest.raises(NoDataError):
        CurrentRouter(DataLayout(tmp_path), CountingFactory()).get()


def test_reuses_router_while_current_unchanged(tmp_path: Path) -> None:
    layout = DataLayout(tmp_path)
    layout.activate(layout.new_build_dir("20261010T000000Z"))
    factory = CountingFactory()
    current = CurrentRouter(layout, factory)
    assert current.get() == ("router-for-20261010T000000Z", "20261010T000000Z")
    assert current.get() == ("router-for-20261010T000000Z", "20261010T000000Z")
    assert len(factory.built) == 1


def test_switches_router_when_current_changes(tmp_path: Path) -> None:
    layout = DataLayout(tmp_path)
    layout.activate(layout.new_build_dir("20261010T000000Z"))
    factory = CountingFactory()
    current = CurrentRouter(layout, factory)
    current.get()
    layout.activate(layout.new_build_dir("20261017T000000Z"))
    assert current.get() == ("router-for-20261017T000000Z", "20261017T000000Z")
    assert len(factory.built) == 2


def test_dangling_current_raises_no_data(tmp_path: Path) -> None:
    layout = DataLayout(tmp_path)
    build = layout.new_build_dir("20261010T000000Z")
    layout.activate(build)
    build.rmdir()
    with pytest.raises(NoDataError):
        CurrentRouter(layout, CountingFactory()).get()


def test_stale_current_read_cannot_overwrite_newer_build(tmp_path: Path) -> None:
    layout = BlockingLayout(tmp_path)
    old = layout.new_build_dir("20261010T000000Z")
    new = layout.new_build_dir("20261017T000000Z")
    layout.activate(old)
    factory = CountingFactory()
    current = CurrentRouter(layout, factory)
    results: dict[str, tuple[str, str]] = {}

    def call(name: str) -> None:
        results[name] = current.get()

    s = threading.Thread(target=call, args=("S",), name="S", daemon=True)
    s.start()
    assert layout.entered.wait(timeout=5)  # S 已讀到舊的 current 並停住
    layout.activate(new)
    b = threading.Thread(target=call, args=("B",), name="B", daemon=True)
    b.start()
    b.join(timeout=0.2)
    layout.release.set()
    s.join(timeout=5)
    b.join(timeout=5)
    assert not s.is_alive() and not b.is_alive()
    assert results["B"] == ("router-for-20261017T000000Z", "20261017T000000Z")
    # 最後一次載入必須是新版；舊版若晚到就會覆寫新版
    assert factory.built[-1] == new, f"最後載入的是 {factory.built[-1].name}，應為 {new.name}"
    loads = len(factory.built)
    assert current.get() == ("router-for-20261017T000000Z", "20261017T000000Z")
    assert len(factory.built) == loads


def test_factory_failure_propagates_keeps_cache_and_retries(tmp_path: Path) -> None:
    layout = DataLayout(tmp_path)
    old = layout.new_build_dir("20261010T000000Z")
    new = layout.new_build_dir("20261017T000000Z")
    layout.activate(old)
    factory = FlakyFactory(fail_for=new)
    current = CurrentRouter(layout, factory)
    assert current.get() == ("router-for-20261010T000000Z", "20261010T000000Z")
    layout.activate(new)
    with pytest.raises(RuntimeError, match="載入失敗"):
        current.get()  # 不退回舊版
    with pytest.raises(RuntimeError, match="載入失敗"):
        current.get()  # 失敗沒被快取，下一次會重新呼叫 factory
    assert factory.built == [old, new, new]
    layout.activate(old)
    assert current.get() == ("router-for-20261010T000000Z", "20261010T000000Z")
    assert factory.built == [old, new, new]  # 舊版仍在快取，沒有重載


def test_dangling_after_successful_get_raises_no_data(tmp_path: Path) -> None:
    layout = DataLayout(tmp_path)
    build = layout.new_build_dir("20261010T000000Z")
    layout.activate(build)
    current = CurrentRouter(layout, CountingFactory())
    assert current.get() == ("router-for-20261010T000000Z", "20261010T000000Z")
    build.rmdir()
    with pytest.raises(NoDataError):
        current.get()


def test_cold_start_concurrent_get_loads_once(tmp_path: Path) -> None:
    layout = DataLayout(tmp_path)
    build = layout.new_build_dir("20261010T000000Z")
    layout.activate(build)
    factory = SlowFactory()
    current = CurrentRouter(layout, factory)
    n = 16
    barrier = threading.Barrier(n)
    results: list[tuple[str, str]] = []

    def worker() -> None:
        barrier.wait(timeout=5)
        results.append(current.get())

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)
    assert not any(t.is_alive() for t in threads)
    assert results == [("router-for-20261010T000000Z", "20261010T000000Z")] * n
    assert len(factory.built) == 1
