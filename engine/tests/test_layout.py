from pathlib import Path

from engine.data.layout import META_FILE, DataLayout


def complete_build(layout: DataLayout, build_id: str) -> Path:
    build = layout.new_build_dir(build_id)
    (build / META_FILE).write_text("{}")
    return build


def test_no_current_initially(tmp_path: Path) -> None:
    assert DataLayout(tmp_path).current() is None


def test_activate_points_current_to_build(tmp_path: Path) -> None:
    layout = DataLayout(tmp_path)
    build = layout.new_build_dir("20261010T000000Z")
    layout.activate(build)
    assert layout.current() == build.resolve()
    assert layout.current_link.is_symlink()
    assert not layout.current_link.readlink().is_absolute()  # 相對連結，換掛載路徑也有效


def test_activate_replaces_previous(tmp_path: Path) -> None:
    layout = DataLayout(tmp_path)
    first = layout.new_build_dir("20261010T000000Z")
    second = layout.new_build_dir("20261017T000000Z")
    layout.activate(first)
    layout.activate(second)
    assert layout.current() == second.resolve()
    assert not list(tmp_path.glob(".current-*"))  # 暫存連結沒有殘留


def test_prune_keeps_newest_and_current(tmp_path: Path) -> None:
    layout = DataLayout(tmp_path)
    ids = ["20261001T000000Z", "20261008T000000Z", "20261015T000000Z", "20261022T000000Z"]
    builds = [complete_build(layout, i) for i in ids]
    layout.activate(builds[0])  # current 是最舊的（例如回滾後）
    removed = layout.prune(keep=2)
    assert sorted(p.name for p in removed) == ["20261008T000000Z"]
    remaining = sorted(p.name for p in layout.builds_dir.iterdir())
    assert remaining == ["20261001T000000Z", "20261015T000000Z", "20261022T000000Z"]


def test_prune_ignores_incomplete_builds_for_quota(tmp_path: Path) -> None:
    layout = DataLayout(tmp_path)
    complete_build(layout, "20261001T000000Z")  # a
    b = complete_build(layout, "20261008T000000Z")
    layout.activate(b)  # 回滾版
    layout.new_build_dir("20261009T000000Z")  # 建置途中被中斷的殘骸，沒有 meta.json
    c = complete_build(layout, "20261015T000000Z")
    layout.activate(c)
    removed = layout.prune(keep=2)
    assert sorted(p.name for p in removed) == ["20261001T000000Z", "20261009T000000Z"]
    assert sorted(p.name for p in layout.builds_dir.iterdir()) == ["20261008T000000Z", "20261015T000000Z"]


def test_prune_keep_zero_keeps_only_current(tmp_path: Path) -> None:
    layout = DataLayout(tmp_path)
    complete_build(layout, "20261001T000000Z")
    b = complete_build(layout, "20261008T000000Z")
    complete_build(layout, "20261015T000000Z")
    layout.activate(b)
    removed = layout.prune(keep=0)
    assert sorted(p.name for p in removed) == ["20261001T000000Z", "20261015T000000Z"]
    assert [p.name for p in layout.builds_dir.iterdir()] == ["20261008T000000Z"]


def test_prune_without_builds_dir_returns_empty(tmp_path: Path) -> None:
    assert DataLayout(tmp_path).prune() == []
