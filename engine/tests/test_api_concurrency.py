"""並發下的逾時：期限從請求進入 app 起算，且每個 worker 同時只進行一個吸附。

吸附本身用假的 match_shape 取代：它模擬真的 Router 的鎖語意（每次呼叫前檢查期限，之後排隊獨占 Valhalla）。
"""

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from engine.app import create_app
from engine.data.layout import DataLayout
from engine.data.pipeline import run_build
from engine.matching.score import Score
from engine.matching.service import MatchResult

BODY = {"strokes": [{"points": [[121.5, 25.05], [121.6, 25.05]]}]}
EMPTY = MatchResult(
    route_points=[], route_length_m=0.0, segments=[], score=Score(0, 0, 0), target_length_m=0.0, warnings=[]
)
DELAY_S = 0.05
TIMEOUT_S = 0.5
CALLS_PER_MATCH = 2
SLACK_S = 0.6  # 一次 Valhalla 呼叫 + 排程與連線的餘裕


def activated(tmp_path: Path) -> Path:
    layout = DataLayout(tmp_path)
    run_build(layout, tmp_path / "x.pbf", "abc", "20261010T000000Z", lambda pbf, d: None, lambda d: [])
    return tmp_path


class FakeValhalla:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.started: list[float] = []

    def __call__(self, router, strokes, *, search_radius_m, deadline, start=None) -> MatchResult:
        self.started.append(time.monotonic())
        for _ in range(CALLS_PER_MATCH):
            deadline.check()
            with self.lock:
                time.sleep(DELAY_S)
        return EMPTY


def timed_post(api: TestClient) -> tuple[int, float]:
    begin = time.monotonic()
    response = api.post("/match", json=BODY)
    return response.status_code, time.monotonic() - begin


@pytest.mark.parametrize("concurrent", [30, 100])  # 100 > anyio 預設 40 條 thread：多的要在 threadpool 前排隊
def test_concurrent_requests_all_finish_near_the_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, concurrent: int
) -> None:
    monkeypatch.setattr("engine.app.match_shape", FakeValhalla())
    app = create_app(activated(tmp_path), router_factory=lambda build: object(), match_timeout_s=TIMEOUT_S)
    with TestClient(app) as api, ThreadPoolExecutor(max_workers=concurrent) as pool:
        results = list(pool.map(lambda _: timed_post(api), range(concurrent)))
    assert {status for status, _ in results} <= {200, 504}
    assert max(elapsed for _, elapsed in results) < TIMEOUT_S + SLACK_S


def test_request_queued_behind_a_long_one_times_out_without_matching(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    started, hold = threading.Event(), threading.Event()
    calls: list[int] = []

    def long_match(router, strokes, *, search_radius_m, deadline, start=None) -> MatchResult:
        calls.append(1)
        started.set()
        hold.wait(timeout=3)
        return EMPTY

    monkeypatch.setattr("engine.app.match_shape", long_match)
    app = create_app(activated(tmp_path), router_factory=lambda build: object(), match_timeout_s=0.2)
    with TestClient(app) as api, ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(api.post, "/match", json=BODY)
        assert started.wait(timeout=3)
        begin = time.monotonic()
        second = api.post("/match", json=BODY)
        waited = time.monotonic() - begin
        hold.set()
        assert first.result().status_code == 200
    assert second.status_code == 504
    assert second.json()["detail"]["code"] == "timeout"
    assert len(calls) == 1  # 排在後面的請求沒有進到 match_shape
    assert waited < 1.0


def test_admission_is_released_after_each_request(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """失敗的請求也要放掉名額，否則一次逾時之後這個 worker 就永遠回 504。"""
    calls = {"n": 0}

    def flaky(router, strokes, *, search_radius_m, deadline, start=None) -> MatchResult:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("非預期的錯誤")
        return EMPTY

    monkeypatch.setattr("engine.app.match_shape", flaky)
    app = create_app(activated(tmp_path), router_factory=lambda build: object())
    with TestClient(app, raise_server_exceptions=False) as api:
        assert api.post("/match", json=BODY).status_code == 500
        assert api.post("/match", json=BODY).status_code == 200
