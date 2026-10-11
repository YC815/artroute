"""不需要 Valhalla 的 Router 純函式測試：回應合併與吸附結果驗證。"""

import json
from pathlib import Path

import pytest

from engine.routing import router as router_module
from engine.routing.polyline import encode
from engine.routing.router import (
    FAST_TURN_PENALTY_FACTOR,
    EdgeInfo,
    RouteResult,
    Router,
    RoutingError,
    _ensure_matched,
    _route_result,
)
from engine.routing.tiles import config_path

A, B, C = (121.5, 25.05), (121.502, 25.05), (121.504, 25.05)
FAR = (121.52, 25.05)  # 離 C 約 1.8 km


def _response(*legs: list[tuple[float, float]]) -> dict:
    return {
        "trip": {
            "legs": [{"shape": encode(leg)} for leg in legs],
            "summary": {"length": 0.5},
        }
    }


def test_route_result_merges_continuous_legs_without_duplicating_joint() -> None:
    result = _route_result(_response([A, B], [B, C]))
    assert result.points == pytest.approx([A, B, C])


def test_route_result_rejects_gap_between_legs() -> None:
    with pytest.raises(RoutingError, match="不連續"):
        _route_result(_response([A, B], [FAR, C]))


def test_ensure_matched_accepts_good_match() -> None:
    _ensure_matched(RouteResult([A, B, C], 0.402), [A, B, C], search_radius_m=50.0)


def test_ensure_matched_rejects_single_distinct_point() -> None:
    with pytest.raises(RoutingError, match="不同的點"):
        _ensure_matched(RouteResult([C, C], 0.0), [C, B, A], search_radius_m=50.0)


def test_ensure_matched_rejects_truncated_match() -> None:
    # 輸入約 0.4 km，只匹配到一半
    with pytest.raises(RoutingError, match="覆蓋"):
        _ensure_matched(RouteResult([A, B], 0.2), [A, B, C], search_radius_m=50.0)


def test_ensure_matched_rejects_far_endpoints() -> None:
    # 長度夠，但起點離輸入起點太遠
    with pytest.raises(RoutingError, match="起點"):
        _ensure_matched(RouteResult([FAR, C], 0.402), [A, B, C], search_radius_m=50.0)
    with pytest.raises(RoutingError, match="終點"):
        _ensure_matched(RouteResult([A, FAR], 0.402), [A, B, C], search_radius_m=50.0)


class FakeActor:
    """取代 valhalla.Actor：記下載入的設定與每個 (方法, 請求)，回應固定是 A→B→C。"""

    def __init__(self, config: dict | str) -> None:
        self.config = config
        self.seen: list[tuple[str, dict]] = []

    def _answer(self, method: str, request: dict) -> dict:
        self.seen.append((method, request))
        return _response([A, B, C])

    def trace_route(self, request: dict) -> dict:
        return self._answer("trace_route", request)

    def route(self, request: dict) -> dict:
        return self._answer("route", request)


def _fake_router(monkeypatch: pytest.MonkeyPatch, config: dict | Path | None = None, **kwargs) -> tuple[Router, FakeActor]:
    monkeypatch.setattr(router_module, "Actor", FakeActor)
    router = Router({} if config is None else config, **kwargs)
    actor = router._actor
    assert isinstance(actor, FakeActor)
    return router, actor


def _captured_request(monkeypatch: pytest.MonkeyPatch, call, **router_kwargs) -> tuple[str, dict]:
    """不載入 Valhalla：攔下 Router 送給 Actor 的 (方法, 請求)。"""
    router, actor = _fake_router(monkeypatch, **router_kwargs)
    call(router)
    return actor.seen[0]


def test_route_request_disables_node_snap_on_every_location(monkeypatch: pytest.MonkeyPatch) -> None:
    # route 預設會把 5 m 內的端點拉到路口，接點就和 trace 吸附出來的端點對不上
    method, request = _captured_request(monkeypatch, lambda router: router.route(A, C))
    assert method == "route"
    assert [loc["node_snap_tolerance"] for loc in request["locations"]] == [0, 0]
    assert [(loc["lon"], loc["lat"]) for loc in request["locations"]] == [A, C]


def test_trace_request_has_no_node_snap_tolerance(monkeypatch: pytest.MonkeyPatch) -> None:
    method, request = _captured_request(monkeypatch, lambda router: router.trace([A, B, C], search_radius_m=50.0))
    assert method == "trace_route"
    assert "node_snap_tolerance" not in str(request)


def _edges_requests(monkeypatch: pytest.MonkeyPatch, points: list[tuple[float, float]]) -> tuple[list, list[dict]]:
    """不載入 Valhalla：回傳 edges() 的結果與它送出的所有 trace_attributes 請求。"""
    router = Router.__new__(Router)
    seen: list[dict] = []

    def fake_call(method: str, request: dict) -> dict:
        assert method == "trace_attributes"
        seen.append(request)
        return {"edges": [{"way_id": 7, "begin_heading": 90.0}]}

    monkeypatch.setattr(router, "_call", fake_call)
    return router.edges(points), seen


def test_edges_drops_consecutive_repeated_vertices(monkeypatch: pytest.MonkeyPatch) -> None:
    # route 的端點落在路口時，折線會重複路口那個點；edge_walk 遇到重複點就整條配不上
    _, seen = _edges_requests(monkeypatch, [A, A, B, B, C, C])
    assert [(p["lon"], p["lat"]) for p in seen[0]["shape"]] == [A, B, C]


def test_edges_keeps_non_consecutive_revisits(monkeypatch: pytest.MonkeyPatch) -> None:
    _, seen = _edges_requests(monkeypatch, [A, B, A])
    assert [(p["lon"], p["lat"]) for p in seen[0]["shape"]] == [A, B, A]


def test_edges_of_zero_length_polyline_is_empty_without_calling_valhalla(monkeypatch: pytest.MonkeyPatch) -> None:
    # 起訖吸附到同一點的零長度路線沒有經過任何路段；edge_walk 對這種折線會直接失敗
    edges, seen = _edges_requests(monkeypatch, [A, A])
    assert edges == [] and seen == []


def test_edges_request_use_and_length_and_carry_them(monkeypatch: pytest.MonkeyPatch) -> None:
    router = Router.__new__(Router)
    seen: list[dict] = []

    def fake_call(method: str, request: dict) -> dict:
        seen.append(request)
        return {"edges": [{"way_id": 7, "begin_heading": 90.0, "use": "footway", "length": 0.012}]}

    monkeypatch.setattr(router, "_call", fake_call)
    edges = router.edges([A, B, C])
    assert {"edge.use", "edge.length"} <= set(seen[0]["filters"]["attributes"])
    assert edges == [EdgeInfo(way_id=7, begin_heading=90.0, use="footway", length_km=0.012)]


def test_edges_without_use_or_length_fall_back_to_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    # 建圖驗證也呼叫 edges()：量測用的欄位缺了不能讓驗證失敗
    edges, _ = _edges_requests(monkeypatch, [A, B, C])
    assert edges == [EdgeInfo(way_id=7, begin_heading=90.0, use="", length_km=0.0)]


def test_route_asks_for_the_shortest_path_only_when_told(monkeypatch: pytest.MonkeyPatch) -> None:
    _, plain = _captured_request(monkeypatch, lambda router: router.route(A, C))
    assert "costing_options" not in plain
    _, shortest = _captured_request(monkeypatch, lambda router: router.route(A, C, shortest=True))
    assert shortest["costing"] == "bicycle"
    assert shortest["costing_options"] == {"bicycle": {"shortest": True}}
    assert [loc["node_snap_tolerance"] for loc in shortest["locations"]] == [0, 0]


def test_trace_request_without_turn_penalty_factor_is_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    # 明確傳 None：trace_options 只有搜尋半徑，由 Valhalla 的 meili 設定決定轉彎懲罰
    _, request = _captured_request(
        monkeypatch, lambda router: router.trace([A, B, C], search_radius_m=50.0), turn_penalty_factor=None
    )
    assert request == {
        "shape": [{"lon": lon, "lat": lat} for lon, lat in (A, B, C)],
        "costing": "bicycle",
        "shape_match": "map_snap",
        "trace_options": {"search_radius": 50.0},
    }


def test_trace_request_carries_turn_penalty_factor_when_set(monkeypatch: pytest.MonkeyPatch) -> None:
    _, request = _captured_request(
        monkeypatch, lambda router: router.trace([A, B, C], search_radius_m=60.0), turn_penalty_factor=500.0
    )
    assert request["trace_options"] == {"search_radius": 60.0, "turn_penalty_factor": 500.0}
    assert request["shape_match"] == "map_snap"


def test_turn_penalty_factor_leaves_route_request_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    # 只影響吸附；route 是連接線與轉彎檢查用的，請求要和沒設時完全相同
    _, plain = _captured_request(monkeypatch, lambda router: router.route(A, C), turn_penalty_factor=None)
    _, penalized = _captured_request(monkeypatch, lambda router: router.route(A, C), turn_penalty_factor=500.0)
    assert penalized == plain


def _write_config(build_dir: Path) -> None:
    config_path(build_dir).write_text(
        json.dumps({"mjolnir": {"tile_dir": "/old"}, "meili": {"default": {"sigma_z": 4.07, "search_radius": 50}}})
    )


def test_from_build_passes_sigma_z_and_turn_penalty_factor(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _write_config(tmp_path)
    monkeypatch.setattr(router_module, "Actor", FakeActor)
    router = Router.from_build(tmp_path, sigma_z=15.0, turn_penalty_factor=140.0)  # 都不是預設值
    actor = router._actor
    assert isinstance(actor, FakeActor) and isinstance(actor.config, dict)
    assert actor.config["meili"]["default"]["sigma_z"] == 15.0
    router.trace([A, B, C])
    assert actor.seen[0][1]["trace_options"]["turn_penalty_factor"] == 140.0


def test_from_build_can_leave_turn_penalty_factor_out(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _write_config(tmp_path)
    monkeypatch.setattr(router_module, "Actor", FakeActor)
    router = Router.from_build(tmp_path, turn_penalty_factor=None)
    router.trace([A, B, C])
    actor = router._actor
    assert isinstance(actor, FakeActor)
    assert actor.seen[0][1]["trace_options"] == {"search_radius": 50.0}


def test_fast_turn_penalty_factor_is_500() -> None:
    # 使用者盲測：V2（turn_penalty_factor 500）對現行 2 勝 0 負 10 平（P2.5 spec §5.1）
    assert FAST_TURN_PENALTY_FACTOR == 500.0


def test_trace_uses_fast_turn_penalty_factor_by_default(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _, request = _captured_request(monkeypatch, lambda router: router.trace([A, B, C], search_radius_m=50.0))
    assert request["trace_options"] == {"search_radius": 50.0, "turn_penalty_factor": FAST_TURN_PENALTY_FACTOR}

    _write_config(tmp_path)
    router = Router.from_build(tmp_path)
    actor = router._actor
    assert isinstance(actor, FakeActor) and isinstance(actor.config, dict)
    assert actor.config["meili"]["default"]["sigma_z"] == 4.07  # sigma_z 維持建圖時的設定
    router.trace([A, B, C])
    assert actor.seen[0][1]["trace_options"]["turn_penalty_factor"] == FAST_TURN_PENALTY_FACTOR
