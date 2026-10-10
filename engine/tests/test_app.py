from pathlib import Path

from fastapi.testclient import TestClient

from engine.app import create_app
from engine.data.layout import DataLayout
from engine.data.pipeline import run_build


def client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(tmp_path))


def test_root_identifies_service(tmp_path: Path) -> None:
    assert client(tmp_path).get("/").json() == {"service": "artroute-engine"}


def test_health_ok(tmp_path: Path) -> None:
    response = client(tmp_path).get("/health")
    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_status_without_data(tmp_path: Path) -> None:
    assert client(tmp_path).get("/status").json() == {"data": None}


def test_status_reports_active_build(tmp_path: Path) -> None:
    layout = DataLayout(tmp_path)
    meta = run_build(
        layout,
        tmp_path / "x.pbf",
        "abc",
        "20261010T000000Z",
        lambda pbf, d: (d / "valhalla.json").write_text("{}"),
        lambda d: [],
    )
    data = client(tmp_path).get("/status").json()["data"]
    assert data == {"build_id": meta.build_id, "osm_md5": "abc", "created_at": meta.created_at}
