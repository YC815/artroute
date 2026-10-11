"""POST /search：串流前的錯誤是一般 JSON；串流是 SSE（progress／best → result 或 error）；
同時只跑一個搜尋（其他排隊並收到 queued）；呼叫端離開時搜尋停下、名額交給下一個。"""

import http.client
import json
import socket
import threading
import time
from pathlib import Path

import pytest
import uvicorn
from fastapi.testclient import TestClient

from engine.app import create_app
from engine.data.layout import DataLayout
from engine.data.pipeline import run_build
from engine.matching.deadline import MatchTimeout
from engine.matching.score import Score
from engine.matching.service import MatchResult
from engine.matching.types import Segment
from engine.search.gate import SearchGate
from engine.search.roads import RoadDataMissing
from engine.search.types import Best, Candidate, Placement, Progress, SearchOutcome

REQUEST = {
    "strokes": [{"points": [[-0.5, -0.5], [0.5, -0.5], [0.5, 0.5]], "closed": True}],
    "center": [121.5, 25.05],
    "radius_m": 3000,
    "min_km": 5,
    "max_km": 15,
    "rotation_min_deg": -15,
    "rotation_max_deg": 15,
}
ROUTE = [(121.5, 25.05), (121.51, 25.05)]
RESULT = MatchResult(
    route_points=ROUTE, route_length_m=1000.0, segments=[Segment("stroke", 0, ROUTE, 1000.0)],
    score=Score(80, 70, 75), target_length_m=900.0, warnings=[],
)
CANDIDATE = Candidate(Placement((121.5, 25.05), 1200.0, -10.0), RESULT, 12.5, True)


def activated(tmp_path: Path) -> Path:
    run_build(DataLayout(tmp_path), tmp_path / "x.pbf", "abc", "20261010T000000Z", lambda pbf, d: None, lambda d: [])
    return tmp_path


def make_app(tmp_path: Path, search, *, roads=lambda build: "roads", data: bool = True, **kwargs):
    root = activated(tmp_path) if data else tmp_path
    return create_app(
        root,
        router_factory=lambda build: object(),
        road_loader=roads,
        search=search,
        search_gate=SearchGate(tmp_path / "search.lock", poll_s=0.01),
        **kwargs,
    )


def events(body: str) -> list[tuple[str, dict]]:
    out = []
    for block in body.strip().split("\n\n"):
        lines = [line for line in block.split("\n") if not line.startswith(":")]
        if not lines:
            continue
        name = next(line[7:] for line in lines if line.startswith("event: "))
        data = json.loads("".join(line[6:] for line in lines if line.startswith("data: ")))
        out.append((name, data))
    return out


def ok_search(params, routers, roads, deadline, emit):
    assert roads == "roads" and len(routers) == 2
    assert params.center == (121.5, 25.05) and params.strokes[0].closed
    emit(Progress("coarse", 0, 6))
    emit(Best(CANDIDATE))
    return SearchOutcome([CANDIDATE], coarse_candidates=1234, evaluated=10, notice=None)


def test_stream_has_progress_best_and_result(tmp_path: Path) -> None:
    with TestClient(make_app(tmp_path, ok_search)) as client:
        response = client.post("/search", json=REQUEST)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    got = events(response.text)
    assert [name for name, _ in got] == ["progress", "best", "result"]
    assert got[0][1] == {"type": "progress", "stage": "coarse", "done": 0, "total": 6}
    candidate = got[2][1]["candidates"][0]
    assert candidate["placement"] == {"center": [121.5, 25.05], "size_m": 1200.0, "rotation_deg": 350.0}
    assert candidate["match"]["score"] == {"coverage": 80, "cleanliness": 70, "direction": 0, "overall": 75}
    assert candidate["match"]["data_version"] == "20261010T000000Z"
    assert got[2][1]["coarse_candidates"] == 1234 and got[2][1]["evaluated"] == 10


@pytest.mark.parametrize(
    ("error", "code"), [(MatchTimeout("x"), "timeout"), (RuntimeError("boom"), "internal")]
)
def test_search_failures_become_error_events(tmp_path: Path, error: Exception, code: str) -> None:
    def failing(*args):
        raise error

    with TestClient(make_app(tmp_path, failing)) as client:
        got = events(client.post("/search", json=REQUEST).text)
    assert [name for name, _ in got] == ["error"]
    assert got[0][1]["code"] == code


def test_pre_stream_errors_are_json(tmp_path: Path) -> None:
    def never(*args):
        raise AssertionError("不該開始搜尋")

    def missing(build):
        raise RoadDataMissing("x")

    with TestClient(make_app(tmp_path, never)) as client:
        outside = client.post("/search", json={**REQUEST, "center": [130.0, 30.0]})
        assert outside.status_code == 400 and outside.json()["detail"]["code"] == "outside_taiwan"
        flipped = client.post("/search", json={**REQUEST, "min_km": 20, "max_km": 10})
        assert flipped.status_code == 422 and isinstance(flipped.json()["detail"], list)
        dot = client.post("/search", json={**REQUEST, "strokes": [{"points": [[0.1, 0.1], [0.1, 0.1]]}]})
        assert dot.status_code == 422 and dot.json()["detail"]["code"] == "too_small"
        big = client.post("/search", json={**REQUEST, "radius_m": 10_001})
        assert big.status_code == 422
    with TestClient(make_app(tmp_path / "a", never, roads=missing)) as client:
        response = client.post("/search", json=REQUEST)
        assert response.status_code == 503 and response.json()["detail"]["code"] == "no_search_data"
    (tmp_path / "b").mkdir()
    with TestClient(make_app(tmp_path / "b", never, data=False)) as client:
        response = client.post("/search", json=REQUEST)
        assert response.status_code == 503 and response.json()["detail"]["code"] == "no_data"


def test_status_reports_search_ready(tmp_path: Path) -> None:
    from engine.search.roads import RIDEABLE_META, RIDEABLE_OFFSETS, RIDEABLE_SEGMENTS

    with TestClient(make_app(tmp_path, ok_search)) as client:
        assert client.get("/status").json()["search_ready"] is False
        build = DataLayout(tmp_path).current()
        for name in (RIDEABLE_META, RIDEABLE_SEGMENTS, RIDEABLE_OFFSETS):
            (build / name).write_text("x")
        assert client.get("/status").json()["search_ready"] is True


def test_status_reports_planner_ready(tmp_path: Path) -> None:
    from engine.planner.graph import GRAPH_FILES

    with TestClient(make_app(tmp_path, ok_search)) as client:
        assert client.get("/status").json()["planner_ready"] is False
        build = DataLayout(tmp_path).current()
        for name in GRAPH_FILES[:-1]:
            (build / name).write_text("x")
        assert client.get("/status").json()["planner_ready"] is False  # 少一個檔案就不算
        (build / GRAPH_FILES[-1]).write_text("x")
        assert client.get("/status").json()["planner_ready"] is True


# ---- 經過真的 uvicorn：排隊與斷線 ----


class Blocking:
    """第一次呼叫卡住直到 release 或被取消（每 10 ms 檢查期限，跟真的搜尋一樣）；之後的呼叫立刻成功。"""

    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()
        self.cancelled = threading.Event()
        self.calls = 0
        self.running = 0
        self.overlap = False

    def __call__(self, params, routers, roads, deadline, emit):
        self.calls += 1
        self.running += 1
        self.overlap = self.overlap or self.running > 1
        try:
            if self.calls == 1:
                self.started.set()
                while not self.release.is_set():
                    try:
                        deadline.check()
                    except BaseException:
                        self.cancelled.set()
                        raise
                    time.sleep(0.01)
            return SearchOutcome([CANDIDATE], 1, 1, None)
        finally:
            self.running -= 1


class Server:
    def __init__(self, app) -> None:
        self.server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=0, lifespan="off", ws="none", log_level="warning")
        )
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def __enter__(self) -> int:
        self.thread.start()
        end = time.monotonic() + 5
        while not self.server.started:
            assert time.monotonic() < end, "uvicorn 沒有啟動"
            time.sleep(0.01)
        return self.server.servers[0].sockets[0].getsockname()[1]

    def __exit__(self, *exc) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=5)


def stream(port: int, out: list[str]) -> None:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request("POST", "/search", body=json.dumps(REQUEST), headers={"Content-Type": "application/json"})
    response = conn.getresponse()
    while line := response.readline():
        out.append(line.decode())
    conn.close()


def wait_for(condition, timeout_s: float = 5.0) -> None:
    end = time.monotonic() + timeout_s
    while not condition():
        assert time.monotonic() < end, "等待逾時"
        time.sleep(0.01)


def test_second_search_queues_until_first_finishes(tmp_path: Path) -> None:
    fake = Blocking()
    with Server(make_app(tmp_path, fake)) as port:
        first: list[str] = []
        second: list[str] = []
        a = threading.Thread(target=stream, args=(port, first))
        a.start()
        assert fake.started.wait(5)
        b = threading.Thread(target=stream, args=(port, second))
        b.start()
        wait_for(lambda: any("queued" in line for line in second))
        assert fake.calls == 1  # 第二個還在排隊，沒有開始
        fake.release.set()
        a.join(5)
        b.join(5)
    assert any(line.startswith("event: result") for line in first)
    assert any(line.startswith("event: result") for line in second)
    assert '"position":1' in "".join(second)
    assert fake.calls == 2 and not fake.overlap


def test_disconnect_cancels_running_search_and_frees_the_slot(tmp_path: Path) -> None:
    fake = Blocking()
    with Server(make_app(tmp_path, fake)) as port:
        body = json.dumps(REQUEST).encode()
        with socket.create_connection(("127.0.0.1", port)) as conn:
            head = f"POST /search HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\nContent-Length: {len(body)}\r\n\r\n"
            conn.sendall(head.encode() + body)
            assert fake.started.wait(5)
        assert fake.cancelled.wait(2), "呼叫端離開後搜尋沒有停"
        after: list[str] = []
        stream(port, after)  # 名額已經釋放：下一個搜尋直接跑完
    assert any(line.startswith("event: result") for line in after)
    assert not fake.overlap


def test_tiny_negative_rotation_does_not_break_the_stream(tmp_path: Path) -> None:
    # 審查：-1.4e-14 % 360 == 360.0，PlacementOut（< 360）驗證失敗，串流沒有最後一個事件
    tiny = Candidate(Placement((121.5, 25.05), 1200.0, -1.42e-14), RESULT, 12.5, True)

    def search(params, routers, roads, deadline, emit):
        emit(Best(tiny))
        return SearchOutcome([tiny], 1, 1, None)

    with TestClient(make_app(tmp_path, search)) as client:
        got = events(client.post("/search", json=REQUEST).text)
    assert [name for name, _ in got] == ["best", "result"]
    assert got[1][1]["candidates"][0]["placement"]["rotation_deg"] == 0.0


def test_strokes_of_repeated_points_are_too_small(tmp_path: Path) -> None:
    # 審查：每筆都是重複的同一點、但筆與筆分開：外框不小，總長卻是 0，原本變成 internal
    def never(*args):
        raise AssertionError("不該開始搜尋")

    body = {**REQUEST, "strokes": [{"points": [[0, 0], [0, 0]]}, {"points": [[0.5, 0.5], [0.5, 0.5]]}]}
    with TestClient(make_app(tmp_path, never)) as client:
        response = client.post("/search", json=body)
    assert response.status_code == 422 and response.json()["detail"]["code"] == "too_small"


def test_gate_failure_ends_with_an_error_event(tmp_path: Path) -> None:
    class Broken(SearchGate):
        async def acquire(self, *args):
            raise OSError("lock file")

    app = create_app(
        activated(tmp_path), router_factory=lambda b: object(), road_loader=lambda b: "roads", search=ok_search,
        search_gate=Broken(tmp_path / "x"),
    )
    with TestClient(app) as client:
        got = events(client.post("/search", json=REQUEST).text)
    assert [(name, data["code"]) for name, data in got] == [("error", "internal")]


def test_search_routers_come_from_router_factory(tmp_path: Path) -> None:
    # 部署時的吸附參數（環境變數）經 router_factory 傳入：搜尋的 Router 也要用它建
    built: list[Path] = []

    def factory(build: Path) -> object:
        built.append(build)
        return ("router", len(built))

    def search(params, routers, roads, deadline, emit):
        assert routers == [("router", 1), ("router", 2)]
        return SearchOutcome([], coarse_candidates=0, evaluated=0, notice=None)

    app = create_app(
        activated(tmp_path), router_factory=factory, road_loader=lambda b: "roads", search=search,
        search_gate=SearchGate(tmp_path / "search.lock", poll_s=0.01),
    )
    with TestClient(app) as client:
        got = events(client.post("/search", json=REQUEST).text)
    assert [name for name, _ in got] == ["result"]
    assert built == [DataLayout(tmp_path).current()] * 2
