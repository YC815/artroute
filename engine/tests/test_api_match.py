from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from engine.app import create_app
from engine.data.layout import DataLayout
from engine.data.pipeline import run_build
from engine.routing.router import Router, RoutingError
from grid_helpers import densify
from osm_fixture import grid_point


def activated(tmp_path: Path) -> Path:
    layout = DataLayout(tmp_path)
    run_build(layout, tmp_path / "x.pbf", "abc", "20261010T000000Z", lambda pbf, d: None, lambda d: [])
    return tmp_path


def client(tmp_path: Path, grid_config: Path, **kwargs) -> TestClient:
    return TestClient(create_app(activated(tmp_path), router_factory=lambda build: Router(grid_config), **kwargs))


def body(*lines, closed=False) -> dict:
    return {"strokes": [{"points": [list(p) for p in line], "closed": closed} for line in lines]}


@pytest.mark.valhalla
def test_match_returns_contract(tmp_path: Path, grid_config: Path) -> None:
    response = client(tmp_path, grid_config).post("/match", json=body(densify(grid_point(0, 1), grid_point(6, 1), n=24)))
    assert response.status_code == 200
    data = response.json()
    assert set(data) == {"route", "segments", "score", "target_length_m", "warnings", "data_version"}
    assert data["data_version"] == "20261010T000000Z"
    assert data["route"]["length_m"] == pytest.approx(1206, abs=60)
    assert {s["kind"] for s in data["segments"]} <= {"stroke", "detour", "connector"}
    assert set(data["score"]) == {"coverage", "cleanliness", "overall"}


def test_no_data_is_503(tmp_path: Path) -> None:
    response = TestClient(create_app(tmp_path)).post("/match", json=body([(121.5, 25.05), (121.51, 25.05)]))
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "no_data"


def test_broken_current_build_is_503_without_leaking_path(tmp_path: Path) -> None:
    """current 指向的 build 載入失敗（RoutingError）也是 no_data；訊息不能帶出內部路徑。"""

    def broken(build: Path) -> Router:
        raise RoutingError(f"讀不到路網設定 {build}/valhalla.json")

    api = TestClient(create_app(activated(tmp_path), router_factory=broken))
    response = api.post("/match", json=body([(121.5, 25.05), (121.51, 25.05)]))
    assert response.status_code == 503
    detail = response.json()["detail"]
    assert detail["code"] == "no_data"
    assert detail["message"]
    assert str(tmp_path) not in response.text
    assert "valhalla.json" not in response.text


@pytest.mark.valhalla
def test_outside_taiwan_is_400(tmp_path: Path, grid_config: Path) -> None:
    response = client(tmp_path, grid_config).post("/match", json=body([(130.0, 30.0), (130.1, 30.0)]))
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "outside_taiwan"


def test_too_small_is_422(tmp_path: Path) -> None:
    """總長不到 50 m：在碰 Router 之前就擋下，所以不需要 Valhalla。"""
    api = TestClient(create_app(activated(tmp_path), router_factory=lambda build: object()))
    response = api.post("/match", json=body([(121.5, 25.05), (121.50001, 25.05)]))
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "too_small"
    assert detail["message"]


@pytest.mark.valhalla
def test_no_route_is_422_with_warnings(tmp_path: Path, grid_config: Path) -> None:
    response = client(tmp_path, grid_config).post("/match", json=body(densify((121.0, 24.0), (121.003, 24.0), n=8)))
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "no_route"
    assert detail["warnings"][0]["code"] == "no_route"


@pytest.mark.valhalla
def test_timeout_is_504(tmp_path: Path, grid_config: Path) -> None:
    api = client(tmp_path, grid_config, match_timeout_s=0.0)
    response = api.post("/match", json=body(densify(grid_point(0, 1), grid_point(6, 1), n=24)))
    assert response.status_code == 504
    assert response.json()["detail"]["code"] == "timeout"


POINT = [121.5, 25.05]
LINE = {"points": [POINT, [121.6, 25.05]]}


@pytest.mark.parametrize(
    "payload",
    [
        {"strokes": []},
        {"strokes": [{"points": [[121.5, 25.05]], "closed": False}]},
        {"strokes": [{"points": [[121.5, 25.05], [121.6, 25.05]]}], "search_radius_m": 10},
        {"strokes": [{"points": [[121.5, 25.05], [121.6, 25.05]]}], "search_radius_m": 151},
        {"strokes": [LINE] * 41},
        {"strokes": [{"points": [POINT] * 2001}]},
        {"strokes": [{"points": [[121.5], [121.6, 25.05]]}]},
        {"strokes": [{"points": [[121.5, 25.05, 3.0], [121.6, 25.05]]}]},
    ],
)
def test_invalid_input_is_422(tmp_path: Path, payload: dict) -> None:
    """沒有路網資料也回 422（不是 503）：驗證在碰服務之前；格式維持 FastAPI 預設的 detail 清單。"""
    response = TestClient(create_app(tmp_path)).post("/match", json=payload)
    assert response.status_code == 422
    assert isinstance(response.json()["detail"], list)


def test_validation_errors_do_not_echo_input(tmp_path: Path) -> None:
    """422 只留 type / loc / msg：不回聲請求內容（超大請求會被放大）。"""
    payload = {"strokes": [{"points": [[121.5, 25.05], [121.6, 25.05]]}], "search_radius_m": 9999}
    detail = TestClient(create_app(tmp_path)).post("/match", json=payload).json()["detail"]
    assert detail
    assert all(set(item) == {"type", "loc", "msg"} for item in detail)
    assert "9999" not in str(detail)


@pytest.mark.parametrize(
    "raw",
    [
        b'{"strokes": [{"points": [[NaN, 25.05], [121.6, 25.05]]}]}',
        b'{"strokes": [{"points": [[121.5, Infinity], [121.6, 25.05]]}]}',
        b'{"strokes": [{"points": [[121.5, 25.05], [-Infinity, 25.05]]}]}',
        b'{"strokes": [{"points": [[121.5, 25.05], [121.6, 25.05]]}], "search_radius_m": NaN}',
        b'{"strokes": [{"points": [[121.5, 25.05], [121.6, 25.05]]}], "search_radius_m": Infinity}',
    ],
)
def test_non_finite_numbers_are_422(tmp_path: Path, raw: bytes) -> None:
    response = TestClient(create_app(tmp_path)).post("/match", content=raw, headers={"Content-Type": "application/json"})
    assert response.status_code == 422
    assert isinstance(response.json()["detail"], list)
