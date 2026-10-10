from pathlib import Path

from fastapi.testclient import TestClient

from engine.app import create_app


def client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(tmp_path))


def test_root_identifies_service(tmp_path: Path) -> None:
    assert client(tmp_path).get("/").json() == {"service": "artroute-engine"}


def test_health_ok(tmp_path: Path) -> None:
    response = client(tmp_path).get("/health")
    assert response.status_code == 200
    assert response.json() == {"ok": True}
