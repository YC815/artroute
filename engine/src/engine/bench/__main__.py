"""python -m engine.bench：在真實路網上跑基準測試（在引擎容器內執行）。"""

import argparse
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path

from engine.bench.cases import CASES
from engine.bench.runner import (
    SAFETY_ERRORS,
    CaseResult,
    case_set_changes,
    compare,
    format_header,
    format_row,
    is_legacy,
    load_baseline,
    run_cases,
    summarize,
)
from engine.data.build import DENIED_FILE, load_denied
from engine.data.layout import DataLayout
from engine.routing.router import Router

DEFAULT_BASELINE = Path(__file__).with_name("baseline.json")


def _parse(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="StravaArt 吸附基準測試")
    parser.add_argument("--data-dir", type=Path, default=Path(os.environ.get("DATA_DIR", "/data")))
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--update-baseline", action="store_true")
    parser.add_argument(
        "--force", action="store_true", help="搭配 --update-baseline：有案例失敗時仍寫入（安全斷言沒過的案例除外）"
    )
    args = parser.parse_args(argv)
    if args.force and not args.update_baseline:
        parser.error("--force 只能搭配 --update-baseline")
    return args


def _update_baseline(args: argparse.Namespace, results: list[CaseResult]) -> None:
    unsafe = [r for r in results if r.error in SAFETY_ERRORS]
    if unsafe:
        print("拒絕更新 baseline：安全斷言沒過的案例不能寫進 baseline（加 --force 也一樣）：", file=sys.stderr)
        for r in unsafe:
            print(f"  {r.name}：{r.error}（{r.safety}）", file=sys.stderr)
        sys.exit(1)
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
    legacy = sum(is_legacy(baseline[r.name]) for r in results if r.name in baseline)
    if legacy:
        print(f"提示：baseline 有 {legacy} 個案例是 v1 評分，這些案例以本次路線的 v1 分數（表格的 v1 欄）比較；確認分數沒有下降後用 --update-baseline 換成 v2")
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
    try:
        denied_ids = frozenset(d.id for d in load_denied(build))
    except (OSError, ValueError, KeyError, TypeError) as error:  # 沒有禁行清單就無法檢查安全：不跑，不能空洞通過
        sys.exit(f"讀不到 {build / DENIED_FILE}，無法檢查路線是否經過禁行路段：{error}")
    router = Router.from_build(build)

    print("每個地點先預熱一次（不計時），之後的 ms 為單次取樣。", flush=True)
    print(f"安全斷言：相鄰路段 ≤ 1 m、不經過 {len(denied_ids)} 條被禁行的 way、接點轉彎能直接騎；沒過的案例算失敗。", flush=True)
    print(format_header(), flush=True)
    results = run_cases(router, CASES, denied_ids=denied_ids, on_result=lambda r: print(format_row(r), flush=True))
    print(summarize(results), flush=True)

    if args.update_baseline:
        _update_baseline(args, results)
    else:
        _check_baseline(args, results)


if __name__ == "__main__":
    main()
