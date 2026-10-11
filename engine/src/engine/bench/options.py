"""吸附 bench 與搜尋 bench 共用的吸附參數旗標（P2.5 spec §5.1）：給本機比對 V2 是否等於只設 turn_penalty_factor 500。

- `--turn-penalty-factor`：數字（≥ 0）或 `none`（不帶，由 meili 設定決定）；不給 = Router 預設 FAST_TURN_PENALTY_FACTOR。
- `--sigma-z`：數字（> 0）；不給 = 設定檔預設（建圖時的設定，Valhalla 預設 4.07 m）。
規則與 app 讀環境變數（TRACE_TURN_PENALTY_FACTOR、MEILI_SIGMA_Z）相同；壞值是 argparse 的錯誤（結束碼 2）。
"""

import argparse
import math

from engine.routing.router import FAST_TURN_PENALTY_FACTOR


def _number(raw: str) -> float:
    try:
        value = float(raw)
    except ValueError:
        raise argparse.ArgumentTypeError(f"必須是數字，收到 {raw!r}") from None
    if not math.isfinite(value):
        raise argparse.ArgumentTypeError(f"必須是有限的數字，收到 {raw!r}")
    return value


def turn_penalty_factor_arg(raw: str) -> float | None:
    if raw.strip().lower() == "none":
        return None
    value = _number(raw)
    if value < 0:
        raise argparse.ArgumentTypeError(f"不能是負數，收到 {raw!r}")
    return value


def sigma_z_arg(raw: str) -> float:
    value = _number(raw)
    if value <= 0:
        raise argparse.ArgumentTypeError(f"必須大於 0，收到 {raw!r}")
    return value


def add_router_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--turn-penalty-factor",
        type=turn_penalty_factor_arg,
        default=FAST_TURN_PENALTY_FACTOR,
        help=f"吸附的轉彎懲罰：數字或 none（不帶，由 meili 設定決定）；預設 {FAST_TURN_PENALTY_FACTOR:g}",
    )
    parser.add_argument("--sigma-z", type=sigma_z_arg, default=None, help="meili 的 sigma_z（m）；預設用設定檔的值")


def router_options(args: argparse.Namespace) -> dict[str, float | None]:
    """Router.from_build 的關鍵字參數。"""
    return {"turn_penalty_factor": args.turn_penalty_factor, "sigma_z": args.sigma_z}


def describe_router_options(options: dict[str, float | None]) -> str:
    """bench 輸出的第一行：實際用的吸附參數。"""
    factor = options["turn_penalty_factor"]
    sigma_z = options["sigma_z"]
    factor_text = "none（不帶，由 meili 設定決定）" if factor is None else f"{factor:g}"
    sigma_text = "設定檔預設" if sigma_z is None else f"{sigma_z:g}"
    return f"吸附參數：turn_penalty_factor {factor_text}、sigma_z {sigma_text}"
