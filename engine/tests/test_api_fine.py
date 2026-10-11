"""/match 的 quality="fine"：每個 worker 快取 current build 的可騎路網圖，只有 fine 才讀並傳給 match_shape。"""

import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from engine.app import _CurrentGraph, create_app
from engine.data.layout import DataLayout
from engine.data.pipeline import run_build
from engine.matching.score import Score
from engine.matching.service import MatchResult
from engine.planner.graph import GRAPH_FILES, GraphDataMissing, RideGraph
from engine.routing.router import Router
from osm_fixture import DIAGONAL_TARGET, meter_point

EMPTY = MatchResult(
    route_points=[], route_length_m=0.0, segments=[], score=Score(0, 0, 0), target_length_m=0.0, warnings=[]
)
BODY = {"strokes": [{"points": [[121.5, 25.05], [121.6, 25.05]]}]}


def build(tmp_path: Path, build_id: str) -> Path:
    layout = DataLayout(tmp_path)
    run_build(layout, tmp_path / "x.pbf", "abc", build_id, lambda pbf, d: None, lambda d: [])
    return layout.current()


class Spy:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def __call__(self, router, strokes, **kwargs) -> MatchResult:
        self.calls.append(kwargs)
        return EMPTY


class Loader:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.loaded: list[Path] = []

    def __call__(self, build_dir: Path):
        self.loaded.append(build_dir)
        if self.fail:
            raise GraphDataMissing(f"{build_dir}：沒有圖")
        return ("graph", build_dir.name)


def make(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, loader: Loader) -> tuple[TestClient, Spy]:
    spy = Spy()
    monkeypatch.setattr("engine.app.match_shape", spy)
    return TestClient(create_app(tmp_path, router_factory=lambda b: object(), graph_loader=loader)), spy


def test_fine_passes_the_current_builds_graph(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    first = build(tmp_path, "20261010T000000Z")
    loader = Loader()
    api, spy = make(tmp_path, monkeypatch, loader)
    assert api.post("/match", json={**BODY, "quality": "fine"}).status_code == 200
    assert spy.calls[-1]["quality"] == "fine" and spy.calls[-1]["graph"] == ("graph", first.name)
    assert api.post("/match", json={**BODY, "quality": "fine"}).status_code == 200
    assert loader.loaded == [first]  # 同一個 build 只載入一次

    second = build(tmp_path, "20261011T000000Z")
    api.post("/match", json={**BODY, "quality": "fine"})
    assert spy.calls[-1]["graph"] == ("graph", second.name)
    assert loader.loaded == [first, second]  # 換版就重新載入


@pytest.mark.parametrize("quality", [None, "fast"])
def test_fast_never_reads_the_graph_and_calls_match_shape_as_before(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, quality: str | None
) -> None:
    build(tmp_path, "20261010T000000Z")
    loader = Loader()
    api, spy = make(tmp_path, monkeypatch, loader)
    payload = BODY if quality is None else {**BODY, "quality": quality}
    assert api.post("/match", json=payload).status_code == 200
    assert set(spy.calls[-1]) == {"search_radius_m", "deadline", "start"}
    assert loader.loaded == []


def test_build_without_a_graph_runs_fine_as_fast_without_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    build(tmp_path, "20261010T000000Z")
    loader = Loader(fail=True)
    api, spy = make(tmp_path, monkeypatch, loader)
    with caplog.at_level(logging.ERROR, logger="engine.app"):
        for _ in range(2):
            assert api.post("/match", json={**BODY, "quality": "fine"}).status_code == 200
    assert spy.calls[-1]["quality"] == "fine" and spy.calls[-1]["graph"] is None
    assert len(loader.loaded) == 1  # 沒有圖也記住，不每次都去讀
    assert caplog.records == []  # 舊 build 沒有圖是正常的


def test_broken_graph_files_are_logged_for_operators(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    current = build(tmp_path, "20261010T000000Z")
    for name in GRAPH_FILES:
        (current / name).write_text("x")
    api, spy = make(tmp_path, monkeypatch, Loader(fail=True))
    with caplog.at_level(logging.ERROR, logger="engine.app"):
        assert api.post("/match", json={**BODY, "quality": "fine"}).status_code == 200
    assert spy.calls[-1]["graph"] is None
    assert [r.levelno for r in caplog.records] == [logging.ERROR]


@pytest.mark.valhalla
def test_fine_match_over_http_uses_the_planner(tmp_path: Path, planner_build: Path) -> None:
    """真的 Valhalla 與路網圖：棋盤裡的斜線，fine 走斜路（方向分數高），fast 走鋸齒。"""
    build(tmp_path, "20261010T000000Z")
    api = TestClient(create_app(tmp_path, router_factory=lambda b: Router.from_build(planner_build),
                                graph_loader=lambda b: RideGraph.load(planner_build)))
    body = {"strokes": [{"points": [list(meter_point(x, y)) for x, y in DIAGONAL_TARGET], "closed": False}]}
    fast = api.post("/match", json=body)
    fine = api.post("/match", json={**body, "quality": "fine"})
    assert fast.status_code == fine.status_code == 200
    assert fine.json()["score"]["direction"] >= fast.json()["score"]["direction"] + 30
    assert fine.json()["route"]["length_m"] < fast.json()["route"]["length_m"]


def test_graph_of_another_build_is_never_handed_out(tmp_path: Path) -> None:
    """Router 是哪個 build 的（版本名稱）就只給那個 build 的圖；current 已經換成別的 build 時不給（這次走 fast）。"""
    current = build(tmp_path, "20261011T000000Z")
    loader = Loader()
    cache = _CurrentGraph(DataLayout(tmp_path), loader)
    assert cache.get("20261010T000000Z") is None
    assert loader.loaded == []
    assert cache.get(current.name) == ("graph", current.name)


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class FlakyLoader(Loader):
    """前 failures 次失敗（例如暫時開不了檔），之後成功。"""

    def __init__(self, failures: int) -> None:
        super().__init__()
        self.failures = failures

    def __call__(self, build_dir: Path):
        self.loaded.append(build_dir)
        if len(self.loaded) <= self.failures:
            raise GraphDataMissing(f"{build_dir}：暫時讀不了")
        return ("graph", build_dir.name)


def test_unreadable_graph_files_are_retried_after_a_while(tmp_path: Path) -> None:
    """檔案都在卻讀不了（可能是暫時的）：不記住到換 build，GRAPH_RETRY_S 之後再試；這段時間走 fast，不會每個請求都去讀。"""
    from engine.app import GRAPH_RETRY_S

    current = build(tmp_path, "20261010T000000Z")
    for name in GRAPH_FILES:
        (current / name).write_text("x")
    loader, clock = FlakyLoader(failures=1), Clock()
    cache = _CurrentGraph(DataLayout(tmp_path), loader, clock=clock)
    assert cache.get(current.name) is None
    clock.now += GRAPH_RETRY_S - 1
    assert cache.get(current.name) is None and len(loader.loaded) == 1
    clock.now += 2
    assert cache.get(current.name) == ("graph", current.name) and len(loader.loaded) == 2


def test_build_without_graph_files_is_never_retried(tmp_path: Path) -> None:
    """舊 build 本來就沒有圖：記住 None 到換 build 為止。"""
    current = build(tmp_path, "20261010T000000Z")
    loader, clock = Loader(fail=True), Clock()
    cache = _CurrentGraph(DataLayout(tmp_path), loader, clock=clock)
    assert cache.get(current.name) is None
    clock.now += 10_000
    assert cache.get(current.name) is None and len(loader.loaded) == 1
