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
