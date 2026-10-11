"""POST /edit 與回應的 route_sig（route editing spec §3.1、§3.4、§3.5）。

版本與簽章的測試用 tests/edit_router.py 的假 router（直線的 route）：路線本身做得到，被擋下只可能是因為版本或簽章。
真的剪掉用 EDIT_MAPS（edit_config）＋真的 Valhalla。
"""

import asyncio
import json
import logging
import re
import stat
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from engine.app import create_app
from engine.data.layout import DataLayout
from engine.data.pipeline import run_build
from engine.edit.splice import DEFAULT_MESSAGES, EditRejected
from engine.edit.signature import load_or_create_key, sign_route, verify_route
from engine.matching.compose import route_points
from engine.matching.score import Score, score
from engine.matching.service import MatchResult
from engine.matching.turns import distance_to_polyline_m
from engine.matching.types import Segment, segment_from
from engine.routing.geo import haversine_m
from engine.routing.router import Router
from edit_router import FakeRouter
from grid_helpers import densify
from osm_fixture import EDIT_NEST_X, grid_point, meter_point
from turn_router import east, north

VERSION = "20261010T000000Z"
OLD_VERSION = "20261003T000000Z"
A = (121.5, 25.05)
LINE = [east(A, 50 * k) for k in range(13)]  # 往東 600 m，每 50 m 一點
ROUTE = [Segment("stroke", 0, LINE[:7], 300.0), Segment("stroke", 0, LINE[6:], 300.0)]
STROKES = [{"points": [list(A), list(east(A, 600))], "closed": False}]
REROUTE = {"type": "reroute", "from": {"segment": 0, "offset_m": 100.0}, "to": {"segment": 1, "offset_m": 100.0},
           "mode": "shortest"}


def activated(root: Path, version: str = VERSION) -> Path:
    run_build(DataLayout(root), root / "x.pbf", "abc", version, lambda pbf, d: None, lambda d: [])
    return root


def key_of(root: Path) -> bytes:
    return load_or_create_key(root / "edit-key")


def segment_json(segment: Segment) -> dict:
    return {"kind": segment.kind, "stroke_index": segment.stroke_index,
            "points": [list(p) for p in segment.points], "length_m": segment.length_m}


def body(segments: list[Segment], sig: str, op: dict, *, version: str = VERSION, strokes=None) -> dict:
    return {"segments": [segment_json(s) for s in segments], "data_version": version, "route_sig": sig,
            "strokes": STROKES if strokes is None else strokes, "op": op}


def signed(root: Path, segments: list[Segment], op: dict, *, version: str = VERSION) -> dict:
    return body(segments, sign_route(key_of(root), version, segments), op, version=version)


def segments_of(data: dict) -> list[Segment]:
    return [Segment(s["kind"], s["stroke_index"], [tuple(p) for p in s["points"]], s["length_m"]) for s in data["segments"]]


def fake_api(tmp_path: Path, router=None, **kwargs) -> TestClient:
    router = FakeRouter() if router is None else router
    return TestClient(create_app(activated(tmp_path), router_factory=lambda build: router, **kwargs))


def assert_no_route_in(response) -> None:
    """錯誤回應只有 detail：不回半成品。"""
    assert set(response.json()) == {"detail"}


# ---- 金鑰與 /match 的簽章 ----


def test_key_is_created_at_startup_with_mode_600(tmp_path: Path) -> None:
    with TestClient(create_app(tmp_path)):
        key = tmp_path / "edit-key"
        assert key.is_file() and stat.S_IMODE(key.stat().st_mode) == 0o600
    custom = tmp_path / "keys" / "k"
    custom.parent.mkdir()
    with TestClient(create_app(tmp_path, edit_key_path=custom)):
        assert custom.read_bytes() != key.read_bytes()


@pytest.mark.valhalla
def test_match_response_carries_a_valid_signature(tmp_path: Path, grid_config: Path) -> None:
    api = TestClient(create_app(activated(tmp_path), router_factory=lambda build: Router(grid_config)))
    data = api.post("/match", json={"strokes": [{"points": [list(p) for p in densify(grid_point(0, 1), grid_point(6, 1), n=24)]}]}).json()
    assert verify_route(key_of(tmp_path), data["data_version"], segments_of(data), data["route_sig"])
    assert not verify_route(key_of(tmp_path), OLD_VERSION, segments_of(data), data["route_sig"])


# ---- 版本與簽章 ----


def test_successful_edit_returns_a_route_signed_for_the_current_version(tmp_path: Path) -> None:
    api = fake_api(tmp_path)
    response = api.post("/edit", json=signed(tmp_path, ROUTE, REROUTE))
    assert response.status_code == 200
    data = response.json()
    assert data["data_version"] == VERSION and data["warnings"] == []
    assert "edit" in {s["kind"] for s in data["segments"]}
    assert verify_route(key_of(tmp_path), VERSION, segments_of(data), data["route_sig"])
    assert data["route"]["points"][0] == list(A) and data["route"]["points"][-1] == list(east(A, 600))
    assert data["target_length_m"] == pytest.approx(600, abs=1)
    assert data["score"]["coverage"] > 90
    expected = score([[A, east(A, 600)]], route_points(segments_of(data)))
    assert data["score"] == {"coverage": expected.coverage, "cleanliness": expected.cleanliness,
                             "direction": expected.direction, "overall": expected.overall}


def test_stale_version_is_409(tmp_path: Path) -> None:
    """簽章對（舊版本簽的）但版本不是 current：409 stale_data。
    拿掉 _edit 的「data_version != version」檢查會紅（簽章用請求的版本驗得過，操作照做、回 200）。"""
    api = fake_api(tmp_path)
    response = api.post("/edit", json=signed(tmp_path, ROUTE, REROUTE, version=OLD_VERSION))
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "stale_data"
    assert_no_route_in(response)


def _moved_point(segments: list[Segment]) -> list[Segment]:
    first = segments[0]
    points = [*first.points[:3], (first.points[3][0] + 1e-6, first.points[3][1]), *first.points[4:]]
    return [Segment(first.kind, first.stroke_index, points, first.length_m), *segments[1:]]


def _relabelled(segments: list[Segment]) -> list[Segment]:
    return [Segment("detour", s.stroke_index, s.points, s.length_m) for s in segments]


@pytest.mark.parametrize("tamper", [_moved_point, _relabelled])
def test_tampered_route_is_invalid_route(tmp_path: Path, tamper) -> None:
    """改了一個點（1e-6 度）或段落種類：簽章驗不過 → 422 invalid_route。
    拿掉 _edit 的 verify_route 檢查會紅（改過的路線仍連續，假 router 照樣做得到這個操作、回 200）。"""
    api = fake_api(tmp_path)
    sig = sign_route(key_of(tmp_path), VERSION, ROUTE)
    response = api.post("/edit", json=body(tamper(ROUTE), sig, REROUTE))
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_route"
    assert_no_route_in(response)


def test_signature_from_another_key_is_invalid(tmp_path: Path) -> None:
    api = fake_api(tmp_path)
    response = api.post("/edit", json=body(ROUTE, sign_route(b"x" * 32, VERSION, ROUTE), REROUTE))
    assert response.status_code == 422 and response.json()["detail"]["code"] == "invalid_route"


# ---- 操作做不到 → 422 帶 code ----

STRAIGHT = [Segment("stroke", 0, LINE, 600.0)]


@pytest.mark.parametrize(
    ("router", "op", "code"),
    [
        (FakeRouter(), {"type": "cut", "at": {"segment": 5, "offset_m": 0.0}}, "not_on_route"),
        (FakeRouter(), {"type": "cut", "at": {"segment": 0, "offset_m": 900.0}}, "not_on_route"),
        (FakeRouter(), {"type": "cut", "at": {"segment": 0, "offset_m": 300.0}}, "nothing_to_cut"),
        (FakeRouter(), {**REROUTE, "from": {"segment": 0, "offset_m": 100.0}, "to": {"segment": 0, "offset_m": 101.0}},
         "no_route"),
        (FakeRouter(fail=True), {"type": "drag", "grab": {"segment": 0, "offset_m": 300.0}, "to": list(north(east(A, 300), 30))},
         "no_road"),
    ],
)
def test_rejected_edits_are_422_with_their_code(tmp_path: Path, router, op: dict, code: str) -> None:
    api = fake_api(tmp_path, router)
    response = api.post("/edit", json=signed(tmp_path, STRAIGHT, op))
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == code and detail["message"]
    assert_no_route_in(response)


@pytest.mark.parametrize("code", sorted(DEFAULT_MESSAGES))
def test_every_rejection_code_passes_through(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, code: str) -> None:
    def rejecting(router, deadline, segments, at):
        raise EditRejected(code)

    monkeypatch.setattr("engine.app.cut_at", rejecting)
    response = fake_api(tmp_path).post("/edit", json=signed(tmp_path, ROUTE, {"type": "cut", "at": {"segment": 0, "offset_m": 1.0}}))
    assert response.status_code == 422
    assert response.json()["detail"] == {"code": code, "message": DEFAULT_MESSAGES[code]}


class SlowFakeRouter(FakeRouter):
    """每次 route 要 0.3 s，端點又被吸到 5 m 外（route_between 會一直換頂點重試，直到期限）。"""

    def __init__(self) -> None:
        super().__init__(snap=lambda p: north(p, 5))

    def route(self, start, end, *, shortest=False):
        time.sleep(0.3)
        return super().route(start, end, shortest=shortest)


def test_slow_edit_is_504_without_a_partial_route(tmp_path: Path) -> None:
    """超過期限：504 timeout、只有 detail。拿掉 _edit 接住 MatchTimeout 轉 504 的那一段會紅（變成未處理的例外）。"""
    api = fake_api(tmp_path, SlowFakeRouter(), match_timeout_s=0.2)
    response = api.post("/edit", json=signed(tmp_path, ROUTE, REROUTE))
    assert response.status_code == 504
    assert response.json()["detail"]["code"] == "timeout"
    assert_no_route_in(response)


# ---- 請求格式 ----


@pytest.mark.parametrize(
    "change",
    [
        lambda b: {**b, "op": {"type": "delete", "at": {"segment": 0, "offset_m": 1.0}}},
        lambda b: {**b, "op": {**{k: v for k, v in REROUTE.items() if k != "from"}, "from_": REROUTE["from"]}},
        lambda b: {**b, "op": {"type": "cut", "at": {"segment": -1, "offset_m": 1.0}}},
        lambda b: {**b, "op": {"type": "cut", "at": {"segment": 0, "offset_m": -1.0}}},
        lambda b: {**b, "op": {**REROUTE, "mode": "scenic"}},
        lambda b: {**b, "segments": []},
        lambda b: {**b, "segments": [{**b["segments"][0], "stroke_index": -1}]},
        lambda b: {**b, "segments": [{**b["segments"][0], "kind": "straight"}]},
        lambda b: {**b, "segments": [{**b["segments"][0], "points": [b["segments"][0]["points"][0]]}]},
        lambda b: {**b, "data_version": "x" * 65},
        lambda b: {**b, "route_sig": "0" * 129},
        lambda b: {**b, "strokes": []},
    ],
)
def test_malformed_requests_are_422_lists(tmp_path: Path, change) -> None:
    response = TestClient(create_app(tmp_path)).post("/edit", json=change(signed(tmp_path, ROUTE, REROUTE)))
    assert response.status_code == 422
    assert isinstance(response.json()["detail"], list)


def test_non_finite_offsets_are_422(tmp_path: Path) -> None:
    raw = json.dumps(signed(tmp_path, ROUTE, REROUTE)).replace('"offset_m": 100.0', '"offset_m": NaN', 1)
    response = TestClient(create_app(tmp_path)).post("/edit", content=raw, headers={"Content-Type": "application/json"})
    assert response.status_code == 422 and isinstance(response.json()["detail"], list)


def test_no_data_is_503(tmp_path: Path) -> None:
    response = TestClient(create_app(tmp_path)).post("/edit", json=signed(tmp_path, ROUTE, REROUTE))
    assert response.status_code == 503 and response.json()["detail"]["code"] == "no_data"


# ---- 與 /match 共用名額、斷線取消、紀錄 ----


def test_edit_waits_for_the_match_holding_the_slot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """同一個 Router 不能被兩個請求同時用：/match 佔著名額時 /edit 不碰 router，等它結束才開始。"""
    release, calls = threading.Event(), []

    def holding(router, strokes, *, search_radius_m, deadline, start=None) -> MatchResult:
        calls.append(time.monotonic())
        assert release.wait(timeout=5)
        return MatchResult([], 0.0, [], Score(0, 0, 0), 0.0, [])

    monkeypatch.setattr("engine.app.match_shape", holding)
    router = FakeRouter()
    with fake_api(tmp_path, router) as api, ThreadPoolExecutor(max_workers=2) as pool:
        match = pool.submit(api.post, "/match", json={"strokes": STROKES})
        until = time.monotonic() + 3
        while not calls:
            assert time.monotonic() < until
            time.sleep(0.005)
        edit = pool.submit(api.post, "/edit", json=signed(tmp_path, ROUTE, REROUTE))
        time.sleep(0.3)
        assert router.calls == []
        release.set()
        assert match.result().status_code == 200
        assert edit.result().status_code == 200
    assert router.calls


EDIT_LOG = re.compile(
    r"^edit op=(?P<op>\w+) status=(?P<status>\d+) wait_ms=\d+ edit_ms=\d+ strokes=1 segments=(?P<segments>\d+|-) "
    r"cancelled=(?P<cancelled>True|False)$"
)


def edit_logs(caplog: pytest.LogCaptureFixture) -> list[dict[str, str]]:
    lines = [r.getMessage() for r in caplog.records if r.name == "engine.app" and r.getMessage().startswith("edit ")]
    parsed = [EDIT_LOG.match(line) for line in lines]
    assert all(parsed), lines
    return [m.groupdict() for m in parsed if m]


def test_each_edit_logs_one_line_with_its_op(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    api = fake_api(tmp_path)
    with caplog.at_level(logging.INFO, logger="engine.app"):
        assert api.post("/edit", json=signed(tmp_path, ROUTE, REROUTE)).status_code == 200
        assert api.post("/edit", json=signed(tmp_path, ROUTE, REROUTE, version=OLD_VERSION)).status_code == 409
    assert [(log["op"], log["status"], log["cancelled"]) for log in edit_logs(caplog)] == [
        ("reroute", "200", "False"), ("reroute", "409", "False")]
    assert edit_logs(caplog)[0]["segments"] != "-"


class Caller:
    """直接呼叫 ASGI app 的一個請求；gone() 模擬呼叫端關掉連線（同 test_api_cancel 的做法）。"""

    def __init__(self, app, path: str, payload: dict) -> None:
        self.app, self.path, self.body = app, path, json.dumps(payload).encode()
        self._sent, self._gone, self.status = False, asyncio.Event(), None

    def gone(self) -> None:
        self._gone.set()

    async def receive(self) -> dict:
        if not self._sent:
            self._sent = True
            return {"type": "http.request", "body": self.body, "more_body": False}
        if not self._gone.is_set():
            await self._gone.wait()
        return {"type": "http.disconnect"}

    async def send(self, message: dict) -> None:
        if message["type"] == "http.response.start":
            self.status = message["status"]

    async def run(self) -> int | None:
        scope = {
            "type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"}, "http_version": "1.1", "method": "POST",
            "scheme": "http", "path": self.path, "raw_path": self.path.encode(), "root_path": "", "query_string": b"",
            "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(self.body)).encode())],
            "client": ("127.0.0.1", 50000), "server": ("testserver", 80),
        }
        await self.app(scope, self.receive, self.send)
        return self.status


class SteppingRouter(FakeRouter):
    """每次「呼叫 Valhalla」0.1 s（route_between 在每次呼叫前 deadline.check）。"""

    def route(self, start, end, *, shortest=False):
        time.sleep(0.1)
        return super().route(start, end, shortest=shortest)


def test_caller_leaving_mid_edit_stops_it(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """呼叫端離開：編輯在下一次期限檢查停下、回 499，紀錄 cancelled=True。端點吸偏讓 route_between 一直重試。"""
    router = SteppingRouter(snap=lambda p: north(p, 5))
    app = create_app(activated(tmp_path), router_factory=lambda build: router)

    async def scenario() -> int | None:
        off_vertices = {**REROUTE, "from": {"segment": 0, "offset_m": 125.0}, "to": {"segment": 1, "offset_m": 125.0}}
        caller = Caller(app, "/edit", signed(tmp_path, ROUTE, off_vertices))
        task = asyncio.create_task(caller.run())
        until = time.monotonic() + 3
        while not router.calls:
            assert time.monotonic() < until and not task.done(), "編輯沒有開始"
            await asyncio.sleep(0.005)
        caller.gone()
        return await asyncio.wait_for(task, 2)

    with caplog.at_level(logging.INFO, logger="engine.app"):
        assert asyncio.run(scenario()) == 499
    assert len(router.calls) < 9  # 沒有把九組端點都試完
    assert [(log["status"], log["cancelled"]) for log in edit_logs(caplog)] == [("499", "True")]


# ---- 真的 Valhalla：剪掉，新簽章能再拿來編輯 ----


def nm(x: float, y: float):
    return meter_point(EDIT_NEST_X + x, y)


def pos_of(segments: list[Segment], point) -> dict:
    """point（落在路線上）的 {segment, offset_m}：第一個經過它的段落。"""
    for index, segment in enumerate(segments):
        walked = 0.0
        for a, b in zip(segment.points, segment.points[1:]):
            if distance_to_polyline_m(point, [a, b]) <= 0.5:
                return {"segment": index, "offset_m": walked + haversine_m(a, point)}
            walked += haversine_m(a, b)
    raise AssertionError(f"{point} 不在路線上")


@pytest.mark.valhalla
def test_cut_then_cut_again_with_the_returned_signature(tmp_path: Path, edit_config: Path) -> None:
    router = Router(edit_config)
    stops = [nm(0, 0), nm(200, 120), nm(250, 180), nm(300, 120), nm(300, 0), nm(200, 0), nm(500, 0)]
    route = [segment_from("stroke", 0, router.route(a, b, shortest=True)) for a, b in zip(stops, stops[1:])]
    strokes = [{"points": [list(nm(0, 0)), list(nm(500, 0))], "closed": False}]
    api = TestClient(create_app(activated(tmp_path), router_factory=lambda build: router))

    first = api.post("/edit", json={**signed(tmp_path, route, {"type": "cut", "at": pos_of(route, nm(250, 150))}),
                                    "strokes": strokes})
    assert first.status_code == 200, first.text
    inner = first.json()
    cut_once = segments_of(inner)
    assert all(haversine_m(p, nm(250, 180)) > 30 for s in cut_once for p in s.points)  # 死巷剪掉了

    second = api.post("/edit", json=body(cut_once, inner["route_sig"], {"type": "cut", "at": pos_of(cut_once, nm(200, 60))},
                                         strokes=strokes))
    assert second.status_code == 200, second.text
    outer = second.json()
    assert all(haversine_m(p, nm(200, 120)) > 30 for s in segments_of(outer) for p in s.points)  # 整個街廓剪掉
    assert outer["score"]["overall"] >= inner["score"]["overall"]
    assert verify_route(key_of(tmp_path), VERSION, segments_of(outer), outer["route_sig"])
