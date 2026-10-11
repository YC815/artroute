import json
from pathlib import Path

from engine.app import create_app

OPENAPI = Path(__file__).resolve().parents[1] / "openapi.json"


def test_committed_openapi_matches_app(tmp_path: Path) -> None:
    generated = create_app(tmp_path).openapi()
    assert json.loads(OPENAPI.read_text()) == json.loads(json.dumps(generated)), (
        "engine/openapi.json 過期：執行 uv run python -m engine.openapi openapi.json 重新產生"
    )


def _schema_refs(schema: dict) -> set[str]:
    refs = [schema] if "$ref" in schema else schema["anyOf"]
    return {ref["$ref"].rsplit("/", 1)[-1] for ref in refs}


def test_match_declares_its_error_bodies(tmp_path: Path) -> None:
    spec = create_app(tmp_path).openapi()
    responses = spec["paths"]["/match"]["post"]["responses"]
    for status in ("400", "503", "504"):
        assert _schema_refs(responses[status]["content"]["application/json"]["schema"]) == {"ErrorResponse"}
    # 422 兩種形狀都要列：領域錯誤（物件）與請求格式錯誤（陣列）
    assert _schema_refs(responses["422"]["content"]["application/json"]["schema"]) == {
        "ErrorResponse",
        "ValidationErrorResponse",
    }
    detail = spec["components"]["schemas"]["ErrorDetail"]
    assert set(detail["properties"]) == {"code", "message", "warnings", "segments"}
    assert detail["required"] == ["code", "message"]


def test_edit_and_verify_declare_their_error_bodies(tmp_path: Path) -> None:
    paths = create_app(tmp_path).openapi()["paths"]
    for path, statuses in {"/edit": ("409", "503", "504"), "/verify": ("503", "504")}.items():
        responses = paths[path]["post"]["responses"]
        for status in statuses:
            assert _schema_refs(responses[status]["content"]["application/json"]["schema"]) == {"ErrorResponse"}
        assert _schema_refs(responses["422"]["content"]["application/json"]["schema"]) == {
            "ErrorResponse",
            "ValidationErrorResponse",
        }
