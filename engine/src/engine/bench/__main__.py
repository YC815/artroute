"""python -m engine.bench：在真實路網上跑基準測試（在引擎容器內執行）。"""

import argparse
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path

from engine.bench.cases import CASES
from engine.bench.runner import (
    CaseResult,
    case_set_changes,
    compare,
    format_header,
    format_row,
    load_baseline,
    run_cases,
    summarize,
)
from engine.data.layout import DataLayout
from engine.routing.router import Router

DEFAULT_BASELINE = Path(__file__).with_name("baseline.json")


def _parse(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="StravaArt 吸附基準測試")
    parser.add_argument("--data-dir", type=Path, default=Path(os.environ.get("DATA_DIR", "/data")))
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--update-baseline", action="store_true")
    parser.add_argument("--force", action="store_true", help="搭配 --update-baseline：有案例失敗時仍寫入")
    args = parser.parse_args(argv)
    if args.force and not args.update_baseline:
        parser.error("--force 只能搭配 --update-baseline")
    return args


def _update_baseline(args: argparse.Namespace, results: list[CaseResult]) -> None:
    failed = [r for r in results if r.error]
    if failed and not args.force:
        print("拒絕更新 baseline：以下案例失敗（確認是預期的再加 --force）：", file=sys.stderr)
        for r in failed:
            print(f"  {r.name}：{r.error}", file=sys.stderr)
        sys.exit(1)
    payload = {r.name: asdict(r) for r in results}
    args.baseline.write_text(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n")
    print(f"已更新 baseline：{args.baseline}")


def _check_baseline(args: argparse.Namespace, results: list[CaseResult]) -> None:
    if not args.baseline.is_file():
        sys.exit(f"找不到 baseline：{args.baseline}（先用 --update-baseline 建立）")
    try:
        baseline = load_baseline(args.baseline)
    except ValueError as error:
        sys.exit(str(error))
    for note in case_set_changes(results, baseline):
        print(f"提示：{note}")
    problems = compare(results, baseline)
    for p in problems:
        print(f"退步：{p}")
    if problems:
        sys.exit(1)


def main(argv: list[str] | None = None) -> None:
    args = _parse(argv)
    build = DataLayout(args.data_dir).current()
    if build is None:
        sys.exit("沒有可用的路網資料")
    router = Router.from_build(build)

    print("每個地點先預熱一次（不計時），之後的 ms 為單次取樣。", flush=True)
    print(format_header(), flush=True)
    results = run_cases(router, CASES, on_result=lambda r: print(format_row(r), flush=True))
    print(summarize(results), flush=True)

    if args.update_baseline:
        _update_baseline(args, results)
    else:
        _check_baseline(args, results)


if __name__ == "__main__":
    main()
