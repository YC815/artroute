"""POST /verify（route editing spec §3.6）：舊路網的草稿在目前的路網上重新檢查。

路網版本不同才查：每段的禁行、逆向、判斷不了（matching.safety），加上每個接點的轉彎。
真的逆向用 verify_old_config／verify_new_config（同一張圖，200–400 m 的路在新路網改成 oneway=yes）；
禁行與判斷不了用假 router（真的 tiles 沒有修補，route 走得過禁行的 way，沒辦法造出來）。
"""

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from engine.app import VERIFY_TIMEOUT_MESSAGE, create_app
from engine.data.build import DENIED_FILE
from engine.data.layout import DataLayout
from engine.data.pipeline import run_build
from engine.edit.signature import load_or_create_key, sign_route, verify_route
from engine.matching.score import Score
from engine.matching.service import MatchResult
from engine.matching.types import Segment, segment_from
from engine.routing.router import EdgeInfo, Router, RoutingError
from edit_router import FakeRouter
from osm_fixture import EDIT_VERIFY_X, meter_point
from turn_router import east, north

NEW = "20261010T000000Z"
OLD = "20261003T000000Z"
A = (121.5, 25.05)
LINE = [east(A, 50 * k) for k in range(13)]  # 往東 600 m，每 50 m 一點
ROUTE = [Segment("stroke", 0, LINE[:7], 300.0), Segment("stroke", 0, LINE[6:], 300.0)]
STROKES = [{"points": [list(A), list(east(A, 600))], "closed": False}]
DENIED_WAY = 9


def activated(root: Path, *, denied: list[int] | None = (), version: str = NEW) -> Path:
    """version 的 build 設成 current；denied 是被禁行的 way id（None = 不寫禁行清單）。"""
    run_build(DataLayout(root), root / "x.pbf", "abc", version, lambda pbf, d: None, lambda d: [])
    if denied is not None:
        (root / "builds" / version / DENIED_FILE).write_text(json.dumps([{"id": i, "name": "", "ref": ""} for i in denied]))
    return root


def key_of(root: Path) -> bytes:
    return load_or_create_key(root / "edit-key")


def body(segments: list[Segment], sig: str, version: str) -> dict:
    return {
        "segments": [{"kind": s.kind, "stroke_index": s.stroke_index, "points": [list(p) for p in s.points], "length_m": s.length_m}
                     for s in segments],
        "data_version": version, "route_sig": sig, "strokes": STROKES,
    }


def signed(root: Path, segments: list[Segment], version: str = OLD) -> dict:
    return body(segments, sign_route(key_of(root), version, segments), version)


def segments_of(data: dict) -> list[Segment]:
    return [Segment(s["kind"], s["stroke_index"], [tuple(p) for p in s["points"]], s["length_m"]) for s in data["segments"]]


def fake_api(tmp_path: Path, router, *, denied: list[int] | None = (), **kwargs) -> TestClient:
    return TestClient(create_app(activated(tmp_path, denied=denied), router_factory=lambda build: router, **kwargs))


def assert_failed(response, code: str, status: int = 422):
    assert response.status_code == status
    assert response.json()["detail"]["code"] == code
    assert set(response.json()) == {"detail"}  # 不回半成品
    return response.json()["detail"]


# ---- 簽章與同版本 ----


def test_current_version_route_is_returned_without_rechecking(tmp_path: Path) -> None:
    """版本與 current 相同：不查路網（FakeRouter 沒有 edges，查了會 500），簽章不變，分數重算。"""
    api = fake_api(tmp_path, FakeRouter())
    request = signed(tmp_path, ROUTE, NEW)
    response = api.post("/verify", json=request)
    assert response.status_code == 200
    data = response.json()
    assert data["data_version"] == NEW and data["route_sig"] == request["route_sig"]
    assert data["segments"] == request["segments"] and data["score"]["coverage"] > 90
    assert data["target_length_m"] == pytest.approx(600, abs=1)


def _moved_point(segments: list[Segment]) -> list[Segment]:
    first = segments[0]
    points = [*first.points[:3], (first.points[3][0] + 1e-6, first.points[3][1]), *first.points[4:]]
    return [Segment(first.kind, first.stroke_index, points, first.length_m), *segments[1:]]


@pytest.mark.parametrize("version", [NEW, OLD])
def test_tampered_route_is_invalid_route(tmp_path: Path, version: str) -> None:
    """改了一個點：簽章驗不過 → 422 invalid_route（舊版本與目前版本都一樣）。
    拿掉 _verify 的 verify_route 檢查會紅（同版本時直接回 200；舊版本時改過的點照樣通過檢查）。"""
    api = fake_api(tmp_path, FakeRouter())
    sig = sign_route(key_of(tmp_path), version, ROUTE)
    assert_failed(api.post("/verify", json=body(_moved_point(ROUTE), sig, version)), "invalid_route")


def test_signature_of_another_version_is_invalid(tmp_path: Path) -> None:
    api = fake_api(tmp_path, FakeRouter())
    sig = sign_route(key_of(tmp_path), OLD, ROUTE)
    assert_failed(api.post("/verify", json=body(ROUTE, sig, NEW)), "invalid_route")


# ---- 舊版本：真的 Valhalla ----


def _route_on(router: Router, *xs: float) -> list[Segment]:
    stops = [meter_point(EDIT_VERIFY_X + x, 0.0) for x in xs]
    return [segment_from("stroke", 0, router.route(a, b, shortest=True)) for a, b in zip(stops, stops[1:])]


@pytest.mark.valhalla
def test_unchanged_network_is_resigned_and_can_be_edited(tmp_path: Path, verify_old_config: Path) -> None:
    router = Router(verify_old_config)
    route = _route_on(router, 0, 200, 400, 600)
    api = fake_api(tmp_path, router)
    response = api.post("/verify", json=signed(tmp_path, route))
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["data_version"] == NEW and data["segments"] == signed(tmp_path, route)["segments"]
    assert verify_route(key_of(tmp_path), NEW, segments_of(data), data["route_sig"])
    assert not verify_route(key_of(tmp_path), OLD, segments_of(data), data["route_sig"])
    edit = api.post("/edit", json={**data, "strokes": STROKES, "op": {"type": "reroute", "from": {"segment": 0, "offset_m": 50.0},
                                                                    "to": {"segment": 2, "offset_m": 50.0}, "mode": "shortest"}})
    assert edit.status_code == 200, edit.text


@pytest.mark.valhalla
def test_wrong_way_in_the_new_network_is_listed(tmp_path: Path, verify_old_config: Path, verify_new_config: Path) -> None:
    """舊路網往東騎的路線，在新路網（200–400 m 改成 oneway=yes、只能往西）上逆向：422 verify_failed，
    segments 是逆向的那一段（第 1 段）。這一段的 edge_walk 直接配不出路段（判斷不了，同樣算有問題），
    所以拿掉 inspect_segments 記錄 undetermined 的那一處會紅；逆向的方向判斷由下一個測試守住。"""
    route = _route_on(Router(verify_old_config), 0, 200, 400, 600)
    api = fake_api(tmp_path, Router(verify_new_config))
    detail = assert_failed(api.post("/verify", json=signed(tmp_path, route)), "verify_failed")
    assert detail["segments"] == [1] and detail["message"]


@pytest.mark.valhalla
def test_wrong_way_is_caught_by_direction(tmp_path: Path, verify_old_config: Path, verify_new_config: Path) -> None:
    """整條路線只有一段（0–600 m），逆向只佔其中 200 m：edge_walk 配得出路段、給出逆向的方向，整段被列出。
    拿掉 inspect_segments 的逆向判斷（backwards 恆為 False）會紅。"""
    route = _route_on(Router(verify_old_config), 0, 600)
    api = fake_api(tmp_path, Router(verify_new_config))
    detail = assert_failed(api.post("/verify", json=signed(tmp_path, route)), "verify_failed")
    assert detail["segments"] == [0]


@pytest.mark.valhalla
def test_gap_lists_both_sides(tmp_path: Path, verify_old_config: Path) -> None:
    """兩段之間差 200 m（簽過的路線不該有缺口，但驗證要自己再查）：兩側都列。"""
    router = Router(verify_old_config)
    route = [*_route_on(router, 0, 200), *_route_on(router, 400, 600)]
    detail = assert_failed(fake_api(tmp_path, router).post("/verify", json=signed(tmp_path, route)), "verify_failed")
    assert detail["segments"] == [0, 1]


# ---- 舊版本：假 router（禁行、判斷不了、期限）----


class EdgeRouter(FakeRouter):
    """edges() 依折線的起點回報經過的路段：第一段是 way 9（被禁行），其餘是 way 1；
    route 失敗，所以合法重現一定重現不了（被判禁行就是真的禁行）。"""

    def __init__(
        self, *, wrong_way: bool | None = False, error: Exception | None = None, delay_s: float = 0.0, fail: bool = True, **kwargs
    ) -> None:
        super().__init__(fail=fail, **kwargs)
        self.wrong_way, self.error, self.delay_s = wrong_way, error, delay_s

    def edges(self, points):
        time.sleep(self.delay_s)
        if self.error is not None and points[0] == ROUTE[1].points[0]:
            raise self.error
        way = DENIED_WAY if points[0] == ROUTE[0].points[0] else 1
        return [EdgeInfo(way, 0.0, wrong_way=self.wrong_way, begin_shape_index=0, end_shape_index=len(points) - 1)]


def test_denied_way_is_listed(tmp_path: Path) -> None:
    """經過被禁行的 way（新路網的禁行清單）：列出那一段。拿掉 inspect_segments 的 `e.way_id in denied_ids` 會紅。"""
    api = fake_api(tmp_path, EdgeRouter(), denied=[DENIED_WAY])
    detail = assert_failed(api.post("/verify", json=signed(tmp_path, ROUTE)), "verify_failed")
    assert detail["segments"] == [0]


def test_unrideable_turn_lists_both_sides(tmp_path: Path) -> None:
    """兩段各自都沒問題，但接點的轉彎 Valhalla 不會直接騎（往東轉往北，route 要繞一大圈）：兩側都列。
    拿掉 _problem_segments 的 junction_ok 迴圈會紅（變成 200）。"""
    corner = east(A, 200)
    route = [Segment("stroke", 0, LINE[:5], 200.0), Segment("stroke", 0, [north(corner, 50 * k) for k in range(5)], 200.0)]
    router = EdgeRouter(fail=False, blocked=[(east(corner, -8), north(corner, 8))])
    detail = assert_failed(fake_api(tmp_path, router, denied=[]).post("/verify", json=signed(tmp_path, route)), "verify_failed")
    assert detail["segments"] == [0, 1]
    straight = fake_api(tmp_path / "straight", EdgeRouter(fail=False), denied=[])
    assert straight.post("/verify", json=signed(tmp_path / "straight", route)).status_code == 200


def test_same_edges_pass_when_nothing_is_denied(tmp_path: Path) -> None:
    api = fake_api(tmp_path, EdgeRouter(), denied=[])
    response = api.post("/verify", json=signed(tmp_path, ROUTE))
    assert response.status_code == 200 and response.json()["data_version"] == NEW


@pytest.mark.parametrize(
    ("router", "expected"),
    [
        (EdgeRouter(error=RoutingError("配不出路段")), [1]),  # 配不出經過的路段
        (EdgeRouter(wrong_way=None), [0, 1]),  # Valhalla 沒給通行方向
    ],
)
def test_undetermined_counts_as_a_problem(tmp_path: Path, router, expected: list[int]) -> None:
    """判斷不了算有問題。拿掉 inspect_segments 記錄 undetermined／unknown 的那兩處會紅（變成 200）。"""
    api = fake_api(tmp_path, router, denied=[])
    detail = assert_failed(api.post("/verify", json=signed(tmp_path, ROUTE)), "verify_failed")
    assert detail["segments"] == expected


def test_missing_denied_list_is_503(tmp_path: Path) -> None:
    """目前的 build 沒有禁行清單：判斷不了，不放行。"""
    api = fake_api(tmp_path, EdgeRouter(), denied=None)
    assert_failed(api.post("/verify", json=signed(tmp_path, ROUTE)), "no_data", 503)


def test_slow_verify_is_504_without_a_partial_route(tmp_path: Path) -> None:
    """超過 verify_timeout_s：504 timeout、只有 detail。同時拿掉 inspect_segments 每段前與 _problem_segments 每個接點前
    的 deadline.check() 會紅（跑完回 200）；只拿掉其中一處時另一處仍會擋下，所以 test_safety 另外守住前者。"""
    api = fake_api(tmp_path, EdgeRouter(delay_s=0.3), denied=[], verify_timeout_s=0.2)
    assert_failed(api.post("/verify", json=signed(tmp_path, ROUTE)), "timeout", 504)


def test_verify_queue_timeout_uses_the_verify_message(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """/match 佔著名額、/verify 排到期限：504 的說明是重新檢查的，不是 /match 的「吸附超過時間上限」。
    拿掉 _admit 的 timeout_message 參數（寫回 TIMEOUT_MESSAGE）會紅。"""
    release, started = threading.Event(), threading.Event()

    def holding(router, strokes, *, search_radius_m, deadline, start=None) -> MatchResult:
        started.set()
        assert release.wait(timeout=5)
        return MatchResult([], 0.0, [], Score(0, 0, 0), 0.0, [])

    monkeypatch.setattr("engine.app.match_shape", holding)
    with fake_api(tmp_path, EdgeRouter(), denied=[], verify_timeout_s=0.3) as api, ThreadPoolExecutor(max_workers=2) as pool:
        match = pool.submit(api.post, "/match", json={"strokes": STROKES})
        assert started.wait(timeout=3)
        response = api.post("/verify", json=signed(tmp_path, ROUTE))
        release.set()
        match.result()
    assert response.status_code == 504
    assert response.json()["detail"] == {"code": "timeout", "message": VERIFY_TIMEOUT_MESSAGE}


def test_verify_deadline_is_independent_of_match_timeout(tmp_path: Path) -> None:
    api = fake_api(tmp_path, EdgeRouter(delay_s=0.3), denied=[], match_timeout_s=0.2, verify_timeout_s=5.0)
    assert api.post("/verify", json=signed(tmp_path, ROUTE)).status_code == 200


# ---- 請求格式、沒有資料 ----


@pytest.mark.parametrize(
    "change",
    [
        lambda b: {**b, "segments": []},
        lambda b: {**b, "data_version": "x" * 65},
        lambda b: {**b, "route_sig": "0" * 129},
        lambda b: {**b, "strokes": []},
        lambda b: {**b, "segments": [{**b["segments"][0], "kind": "straight"}]},
    ],
)
def test_malformed_requests_are_422_lists(tmp_path: Path, change) -> None:
    response = TestClient(create_app(tmp_path)).post("/verify", json=change(signed(tmp_path, ROUTE)))
    assert response.status_code == 422 and isinstance(response.json()["detail"], list)


def test_no_data_is_503(tmp_path: Path) -> None:
    response = TestClient(create_app(tmp_path)).post("/verify", json=signed(tmp_path, ROUTE))
    assert response.status_code == 503 and response.json()["detail"]["code"] == "no_data"
