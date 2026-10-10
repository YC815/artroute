from pathlib import Path

import pytest

from engine.data.layout import DataLayout
from engine.data.pipeline import ValidationFailed, read_meta, run_build


def fake_builder(pbf: Path, build_dir: Path) -> None:
    (build_dir / "valhalla.json").write_text("{}")


def test_successful_build_is_activated_with_meta(tmp_path: Path) -> None:
    layout = DataLayout(tmp_path)
    meta = run_build(layout, tmp_path / "x.pbf", "abc", "20261010T000000Z", fake_builder, lambda d: [])
    assert layout.current() == (layout.builds_dir / "20261010T000000Z").resolve()
    assert read_meta(layout.current()) == meta
    assert meta.osm_md5 == "abc"


def test_failed_validation_keeps_previous_current(tmp_path: Path) -> None:
    layout = DataLayout(tmp_path)
    run_build(layout, tmp_path / "x.pbf", "old", "20261003T000000Z", fake_builder, lambda d: [])
    with pytest.raises(ValidationFailed, match="逆向"):
        run_build(layout, tmp_path / "x.pbf", "new", "20261010T000000Z", fake_builder, lambda d: ["逆向騎上單行道"])
    assert read_meta(layout.current()).osm_md5 == "old"
    assert not (layout.builds_dir / "20261010T000000Z").exists()


def test_builder_crash_keeps_previous_current(tmp_path: Path) -> None:
    layout = DataLayout(tmp_path)
    run_build(layout, tmp_path / "x.pbf", "old", "20261003T000000Z", fake_builder, lambda d: [])

    def crashing_builder(pbf: Path, build_dir: Path) -> None:
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        run_build(layout, tmp_path / "x.pbf", "new", "20261010T000000Z", crashing_builder, lambda d: [])
    assert read_meta(layout.current()).osm_md5 == "old"
    assert not (layout.builds_dir / "20261010T000000Z").exists()


def test_read_meta_missing(tmp_path: Path) -> None:
    assert read_meta(tmp_path) is None
