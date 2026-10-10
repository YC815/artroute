"""python -m engine.bench：在真實路網上跑基準測試（在引擎容器內執行）。"""

import argparse
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path

from engine.bench.cases import CASES
from engine.bench.runner import compare, run_cases
from engine.data.layout import DataLayout
from engine.routing.router import Router

DEFAULT_BASELINE = Path(__file__).with_name("baseline.json")


def main() -> None:
    parser = argparse.ArgumentParser(description="StravaArt 吸附基準測試")
    parser.add_argument("--data-dir", type=Path, default=Path(os.environ.get("DATA_DIR", "/data")))
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--update-baseline", action="store_true")
    args = parser.parse_args()

    build = DataLayout(args.data_dir).current()
    if build is None:
        sys.exit("沒有可用的路網資料")
    results = run_cases(Router.from_build(build), CASES)
    for r in results:
        print(f"{r.name:32} overall={r.overall:3} cov={r.coverage:3} clean={r.cleanliness:3} "
              f"len={r.length_m:8.0f} target={r.target_length_m:8.0f} {r.ms:6}ms {r.error or ''}")

    payload = {r.name: asdict(r) for r in results}
    if args.update_baseline:
        args.baseline.write_text(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n")
        print(f"已更新 baseline：{args.baseline}")
        return
    if not args.baseline.is_file():
        sys.exit(f"找不到 baseline：{args.baseline}（先用 --update-baseline 建立）")
    problems = compare(results, json.loads(args.baseline.read_text()))
    for p in problems:
        print(f"退步：{p}")
    if problems:
        sys.exit(1)


if __name__ == "__main__":
    main()
