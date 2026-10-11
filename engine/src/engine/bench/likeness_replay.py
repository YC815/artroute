"""python -m engine.bench.likeness_replay：用現在的程式與吸附參數，把校正樣本裡某個變體的擺放重新吸附，比對路線是否相同。

用途（P2.5 spec §5.1）：樣本的 V2 是 `gps_accuracy` = 半徑 / 2 加 `turn_penalty_factor` 500；實測 gps_accuracy 沒有作用，
所以 V2 應該等於只設 500（推論）。在引擎容器內（current build 要和產生樣本時同一個 build，否則路網不同、比對沒有意義）：

    python -m engine.bench.likeness_replay --samples /tmp/likeness --variant V2 --turn-penalty-factor 500 --no-prune

`--no-prune`：樣本產生時還沒有剪小刺與小圈，比對時要關掉。輸出每條路線是否逐點相同（座標取到小數 6 位，同樣本），
不同時列出兩條路線之間的最大偏離（雙向，頂點到另一條折線頂點的最近距離，公尺）。
"""

import argparse
import gzip
import json
import math
import os
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from engine.bench.likeness import _route
from engine.bench.options import (
    add_match_options,
    add_router_options,
    describe_match_options,
    describe_router_options,
    match_options,
    router_options,
)
from engine.data.layout import DataLayout
from engine.matching.deadline import Deadline
from engine.matching.service import match_shape
from engine.matching.types import MatchRouter, StartHint
from engine.routing.router import LonLat, Router
from engine.search.geometry import place
from engine.search.types import NormStroke, Placement

TIMEOUT_S = 10.0  # 同搜尋精算的單一候選上限
DIGITS = 6  # 樣本的座標精度


@dataclass(frozen=True)
class Replay:
    case: str
    rank: int
    same: bool
    max_gap_m: float | None  # 相同時為 0；重新吸附失敗時為 None
    error: str | None = None


def _rounded(points: list[LonLat]) -> list[tuple[float, float]]:
    return [(round(lon, DIGITS), round(lat, DIGITS)) for lon, lat in points]


def max_gap_m(a: list[LonLat], b: list[LonLat]) -> float:
    """兩條路線頂點之間的雙向最大最近距離（局部平面，公尺）：粗略的 Hausdorff，只用來看差多少。"""
    lat0 = math.radians((a[0][1] + b[0][1]) / 2)
    scale = np.array([111_320.0 * math.cos(lat0), 110_574.0])
    pa, pb = np.asarray(a) * scale, np.asarray(b) * scale
    d = np.sqrt(((pa[:, None, :] - pb[None, :, :]) ** 2).sum(axis=2))
    return float(max(d.min(axis=1).max(), d.min(axis=0).max()))


def replay(
    cases: list[dict], variant: str, router: MatchRouter, options: Mapping[str, Any]
) -> list[Replay]:
    out: list[Replay] = []
    for c in cases:
        strokes = [NormStroke([(x, y) for x, y in s["points"]], s["closed"]) for s in c["shape"]]
        start = StartHint((c["center"][0], c["center"][1]), c["radius_m"])
        for r in c["routes"]:
            if r["variant"] != variant or "error" in r:
                continue
            p = r["placement"]
            placement = Placement((p["center"][0], p["center"][1]), p["size_m"], p["rotation_deg"])
            try:
                result = match_shape(
                    router, place(strokes, placement), search_radius_m=None, deadline=Deadline(TIMEOUT_S), start=start,
                    **options,
                )
            except Exception as error:  # noqa: BLE001  一條失敗不擋住其他條
                out.append(Replay(c["case"], r["rank"], False, None, type(error).__name__))
                continue
            stored, fresh = _route(r["segments"]), _rounded(result.route_points)
            same = stored == fresh
            out.append(Replay(c["case"], r["rank"], same, 0.0 if same else round(max_gap_m(stored, fresh), 1)))
    return out


def main(argv: list[str] | None = None, make_router: Callable[..., MatchRouter] = Router.from_build) -> None:
    parser = argparse.ArgumentParser(description="重新吸附校正樣本的某個變體，比對路線是否相同")
    parser.add_argument("--samples", type=Path, required=True, help="routes.json.gz 所在的目錄")
    parser.add_argument("--variant", required=True, help="例如 V2")
    parser.add_argument("--data-dir", type=Path, default=Path(os.environ.get("DATA_DIR", "/data")))
    add_router_options(parser)
    add_match_options(parser)
    args = parser.parse_args(argv)
    build = DataLayout(args.data_dir).current()
    if build is None:
        sys.exit("沒有可用的路網資料")
    with gzip.open(args.samples / "routes.json.gz", "rt") as f:
        cases = json.load(f)
    options, matching = router_options(args), match_options(args)
    print(describe_router_options(options), flush=True)
    print(describe_match_options(matching), flush=True)
    print(f"build {build.name}；樣本的變體 {args.variant}", flush=True)
    results = replay(cases, args.variant, make_router(build, **options), matching)
    for r in results:
        status = "相同" if r.same else (f"失敗（{r.error}）" if r.error else f"不同，最大偏離 {r.max_gap_m:g} m")
        print(f"{r.case:34} 第 {r.rank + 1} 名  {status}", flush=True)
    print(f"共 {len(results)} 條：逐點相同 {sum(r.same for r in results)} 條", flush=True)


if __name__ == "__main__":
    main()
