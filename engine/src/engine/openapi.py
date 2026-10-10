"""輸出 OpenAPI：uv run python -m engine.openapi openapi.json（web 依它產生型別）。"""

import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from engine.app import create_app


def main() -> None:
    target = Path(sys.argv[1])
    with TemporaryDirectory() as tmp:
        spec = create_app(Path(tmp)).openapi()
    target.write_text(json.dumps(spec, indent=2, ensure_ascii=False, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
