import json
from pathlib import Path

from engine.app import create_app

OPENAPI = Path(__file__).resolve().parents[1] / "openapi.json"


def test_committed_openapi_matches_app(tmp_path: Path) -> None:
    generated = create_app(tmp_path).openapi()
    assert json.loads(OPENAPI.read_text()) == json.loads(json.dumps(generated)), (
        "engine/openapi.json 過期：執行 uv run python -m engine.openapi openapi.json 重新產生"
    )
