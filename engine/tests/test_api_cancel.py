"""呼叫端離開（web 拖曳時每 300 ms 取消上一筆）：排隊的不再排、跑到一半的停下，名額立刻讓給下一筆（最終審查 I-2）。

直接呼叫 ASGI app，receive 模仿 uvicorn：body 送完之後，先同步檢查連線是否已斷，斷了立刻回 http.disconnect，
沒斷才等。（Starlette 的 is_disconnected 在已取消的 scope 裡呼叫 receive；要是先 await 才檢查，永遠拿不到 disconnect。）
吸附用假的 match_shape 取代，它在每次「Valhalla 呼叫」前檢查期限，跟真的吸附一樣。
"""

import asyncio
import json
import socket
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import anyio.to_thread
import pytest
import uvicorn
from fastapi.testclient import TestClient

from engine.app import DISCONNECT_POLL_S, create_app
from engine.data.layout import DataLayout
from engine.data.pipeline import run_build
from engine.matching.deadline import Deadline, MatchCancelled
from engine.matching.score import Score
from engine.matching.service import MatchResult

REQUEST = {"strokes": [{"points": [[121.5, 25.05], [121.6, 25.05]]}]}
BODY = json.dumps(REQUEST).encode()
EMPTY = MatchResult(
    route_points=[], route_length_m=0.0, segments=[], score=Score(0, 0, 0), target_length_m=0.0, warnings=[]
)
STEP_S = 0.05  # 假的一次 Valhalla 呼叫
NOTICE_S = 2 * DISCONNECT_POLL_S + STEP_S + 0.15  # 發現斷線（輪詢）＋跑完進行中的那一步＋排程餘裕


def activated(tmp_path: Path) -> Path:
    layout = DataLayout(tmp_path)
    run_build(layout, tmp_path / "x.pbf", "abc", "20261010T000000Z", lambda pbf, d: None, lambda d: [])
    return tmp_path


def make_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake: Callable, timeout_s: float = 5.0):
    monkeypatch.setattr("engine.app.match_shape", fake)
    return create_app(activated(tmp_path), router_factory=lambda build: object(), match_timeout_s=timeout_s)


def http_scope() -> dict:
    return {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.4"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/match",
        "raw_path": b"/match",
        "root_path": "",
        "query_string": b"",
        "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(BODY)).encode())],
        "client": ("127.0.0.1", 50000),
        "server": ("testserver", 80),
    }


class Caller:
    """一個 /match 請求；gone() 模擬呼叫端關掉連線。"""

    def __init__(self, app) -> None:
        self.app = app
        self._body_sent = False
        self._gone = asyncio.Event()
        self.status: int | None = None
        self.sent_at = self.gone_at = self.done_at = 0.0

    def gone(self) -> None:
        self.gone_at = time.monotonic()
        self._gone.set()

    async def receive(self) -> dict:
        if not self._body_sent:
            self._body_sent = True
            return {"type": "http.request", "body": BODY, "more_body": False}
        if not self._gone.is_set():  # 跟 uvicorn 一樣：已經斷線就同步回 disconnect
            await self._gone.wait()
        return {"type": "http.disconnect"}

    async def send(self, message: dict) -> None:
        if message["type"] == "http.response.start":
            self.status = message["status"]

    async def run(self) -> int | None:
        self.sent_at = time.monotonic()
        await self.app(http_scope(), self.receive, self.send)
        self.done_at = time.monotonic()
        return self.status


async def until(condition: Callable[[], bool], timeout_s: float = 3.0) -> None:
    end = time.monotonic() + timeout_s
    while not condition():
        assert time.monotonic() < end, "等待逾時"
        await asyncio.sleep(0.005)


class SteppingMatch:
    """每一步先檢查期限（像真的吸附在每次 Valhalla 呼叫前檢查），再「呼叫 Valhalla」STEP_S 秒。"""

    def __init__(self, steps: int = 40) -> None:
        self.steps = steps
        self.started: list[float] = []
        self.cancelled = 0

    def __call__(self, router, strokes, *, search_radius_m, deadline: Deadline) -> MatchResult:
        self.started.append(time.monotonic())
        try:
            for _ in range(self.steps):
                deadline.check()
                time.sleep(STEP_S)
        except MatchCancelled:
            self.cancelled += 1
            raise
        return EMPTY


class HoldingMatch:
    """第一筆一直佔著名額直到 release；之後的每一筆立刻完成。"""

    def __init__(self) -> None:
        self.release = threading.Event()
        self.calls = 0
        self.before_return: Callable[[], None] | None = None

    def __call__(self, router, strokes, *, search_radius_m, deadline: Deadline) -> MatchResult:
        self.calls += 1
        if self.calls == 1:
            assert self.release.wait(timeout=5)
            if self.before_return is not None:
                self.before_return()
        return EMPTY


def test_caller_leaving_while_queued_never_runs_and_frees_the_queue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = HoldingMatch()
    app = make_app(tmp_path, monkeypatch, fake)

    async def scenario() -> None:
        first, queued = Caller(app), Caller(app)
        first_task = asyncio.create_task(first.run())
        await until(lambda: fake.calls == 1)
        queued_task = asyncio.create_task(queued.run())
        await asyncio.sleep(0.15)  # 排在 first 後面等名額
        queued.gone()
        assert await asyncio.wait_for(queued_task, 2) == 499
        assert queued.done_at - queued.gone_at < NOTICE_S  # 不用等 first 跑完，也不用等到期限
        fake.release.set()
        assert await first_task == 200
        third = Caller(app)
        assert await third.run() == 200
        assert third.done_at - third.sent_at < 0.3  # 名額沒有被離開的請求佔住
        assert fake.calls == 2  # 離開的那一筆從來沒有進到 match_shape

    asyncio.run(scenario())


def test_caller_leaving_mid_match_stops_it_and_hands_over_the_slot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = SteppingMatch(steps=40)  # 不取消要跑 2 s
    app = make_app(tmp_path, monkeypatch, fake)

    async def scenario() -> None:
        running, waiting = Caller(app), Caller(app)
        running_task = asyncio.create_task(running.run())
        await until(lambda: len(fake.started) == 1)
        waiting_task = asyncio.create_task(waiting.run())
        await asyncio.sleep(0.2)
        running.gone()
        assert await asyncio.wait_for(running_task, 2) == 499
        assert running.done_at - running.gone_at < NOTICE_S  # 在下一次「Valhalla 呼叫」前停下
        await until(lambda: len(fake.started) == 2)
        assert fake.started[1] - running.gone_at < NOTICE_S  # 名額立刻交給排隊的下一筆
        waiting.gone()
        assert await asyncio.wait_for(waiting_task, 2) == 499
        assert fake.cancelled == 2

    asyncio.run(scenario())


def test_caller_leaving_as_the_slot_is_granted_releases_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    deadlines: list[Deadline] = []

    class RecordingDeadline(Deadline):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            deadlines.append(self)

    monkeypatch.setattr("engine.app.Deadline", RecordingDeadline)
    fake = HoldingMatch()
    app = make_app(tmp_path, monkeypatch, fake)

    async def scenario() -> None:
        loop = asyncio.get_running_loop()
        first, second = Caller(app), Caller(app)
        first_task = asyncio.create_task(first.run())
        await until(lambda: fake.calls == 1)
        second_task = asyncio.create_task(second.run())
        await until(lambda: len(deadlines) == 2)
        await asyncio.sleep(0.1)
        # first 的 thread 結束前排定取消 second：事件迴圈先跑取消，才處理 first 結束、交出名額，
        # 所以 second 拿到名額的同一刻，它的呼叫端已經離開
        fake.before_return = lambda: loop.call_soon_threadsafe(deadlines[1].cancel)
        fake.release.set()
        assert await first_task == 200
        assert await asyncio.wait_for(second_task, 2) == 499
        assert fake.calls == 1  # second 沒有進到 match_shape
        third = Caller(app)
        assert await third.run() == 200  # 名額有還回去
        assert third.done_at - third.sent_at < 0.3

    asyncio.run(scenario())


def test_deadline_starts_when_the_request_arrives(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """ledger L99：期限在請求抵達時起算，不是等到有空的 thread。

    threadpool 只留 1 條、被正在跑的吸附佔住：期限若由同步依賴（要排 thread）起算，
    後到的請求要等前一筆跑完才開始計時，就會拿到 200 或晚很多才 504。
    """
    timeout_s, late_s = 0.5, 0.3
    fake = HoldingMatch()
    app = make_app(tmp_path, monkeypatch, fake, timeout_s=timeout_s)

    async def scenario() -> None:
        anyio.to_thread.current_default_thread_limiter().total_tokens = 1
        first = Caller(app)
        first_task = asyncio.create_task(first.run())
        await until(lambda: fake.calls == 1)
        await asyncio.sleep(late_s)
        late = Caller(app)
        assert await asyncio.wait_for(late.run(), 3) == 504
        fake.release.set()
        assert await first_task == 200
        assert late.done_at - late.sent_at == pytest.approx(timeout_s, abs=DISCONNECT_POLL_S + 0.1)
        assert late.done_at - first.sent_at > late_s + timeout_s - 0.05  # 起點是後到請求自己的抵達時刻

    asyncio.run(scenario())


def test_status_is_not_queued_behind_requests_waiting_for_the_slot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ledger L99：舊版等名額的請求各佔一條 thread（預設 40 條），/status 得排在它們後面。"""
    fake = HoldingMatch()
    app = make_app(tmp_path, monkeypatch, fake, timeout_s=2.0)
    with TestClient(app) as api, ThreadPoolExecutor(max_workers=61) as pool:
        first = pool.submit(api.post, "/match", json=REQUEST)
        deadline = time.monotonic() + 3
        while fake.calls == 0:
            assert time.monotonic() < deadline
            time.sleep(0.005)
        queued = [pool.submit(api.post, "/match", json=REQUEST) for _ in range(60)]
        time.sleep(0.3)  # 讓 60 筆都在等名額
        begin = time.monotonic()
        status = api.get("/status")
        waited = time.monotonic() - begin
        fake.release.set()
        assert first.result().status_code == 200
        assert all(f.result().status_code in (200, 504) for f in queued)
    assert status.status_code == 200
    assert waited < 0.5


def test_real_uvicorn_disconnect_cancels_the_running_match(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """唯一經過真的 uvicorn 與 socket 的測試：釘住「對端關閉連線 → receive 回 http.disconnect」這個前提。"""
    fake = SteppingMatch(steps=100)  # 不取消要跑 5 s
    app = make_app(tmp_path, monkeypatch, fake)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, lifespan="off", ws="none", log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 5
        while not server.started:
            assert time.monotonic() < deadline, "uvicorn 沒有啟動"
            time.sleep(0.01)
        port = server.servers[0].sockets[0].getsockname()[1]
        with socket.create_connection(("127.0.0.1", port)) as conn:
            head = f"POST /match HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\nContent-Length: {len(BODY)}\r\n\r\n"
            conn.sendall(head.encode() + BODY)
            while not fake.started:
                assert time.monotonic() < deadline, "吸附沒有開始"
                time.sleep(0.01)
        closed_at = time.monotonic()
        while fake.cancelled == 0:
            assert time.monotonic() - closed_at < 1.0, "關掉連線後吸附沒有停"
            time.sleep(0.01)
    finally:
        server.should_exit = True
        thread.join(timeout=5)
