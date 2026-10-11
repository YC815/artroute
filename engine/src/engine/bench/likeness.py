"""python -m engine.bench.likeness：評分和使用者盲測標註的一致率（P2.5 spec §4.4）。

資料是 tests/data/likeness/ 的 routes.json.gz 與 labels.json（格式見該目錄的 README）。每一組配對是同一個案例的兩條路線
與使用者的選擇（左／右／差不多）。報告：
- 有表態（不算「差不多」）的配對中，評分較高的那條是使用者選的那條的組數；同分算不一致；
- 每種配對的一致數：現行 vs V2、現行 vs V3、不同擺放（兩邊都是 V0：搜尋結果的不同名次）；
- 「差不多」與有表態配對的分數差距中位數（前者應該明顯比較小）；
- 留一個案例交叉驗證：用其他案例的配對挑參數（fit），量被留下那個案例的配對。

比較一律用搜尋的排名分數 rank（旋轉扣分）而且不取整數（使用者決定：標註頁顯示的是沒旋轉的原圖；同一個擺放的配對
旋轉相同，扣分抵銷）。v3 用 spec 的初始參數、沒有擬合任何東西，所以交叉驗證等於樣本內。v2 的數字是對照。
"""

import argparse
import gzip
import json
import statistics
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from engine.matching.score import V3_DEFAULT, Score, rank, score, score_v2
from engine.routing.router import LonLat
from engine.search.geometry import place
from engine.search.types import NormStroke, Placement

DATA_DIR = Path(__file__).resolve().parents[3] / "tests" / "data" / "likeness"
CURRENT = "V0"  # routes.json 的現行吸附（正式搜尋的結果）
CURRENT_VARIANTS = frozenset({CURRENT, "Q2"})  # 下一輪樣本（likeness_samples）的現行預設是 Q2
KIND_ORDER = ("現行 vs V2", "現行 vs V3", "不同擺放")

type Choice = Literal["L", "R", "same"]


@dataclass(frozen=True)
class Sample:
    """routes.json 的一條路線，還原成評分的輸入。"""

    case: str  # 案例名稱去掉 @R5（同 labels.json）
    key: str  # <variant><rank>，例如 V02 = V0 第 3 名
    targets: list[list[LonLat]]  # 擺放後的原圖（沒有變形），封閉筆畫補回起點
    route: list[LonLat]
    rotation_deg: float


@dataclass(frozen=True)
class Pair:
    n: int
    case: str
    left: str
    right: str
    choice: Choice


type Scorer = Callable[[Sample], float]
type Fit = Callable[[Sequence[Pair]], Scorer]  # 用這些配對挑參數，回傳挑好的評分


@dataclass(frozen=True)
class PairResult:
    pair: Pair
    left: float
    right: float

    @property
    def gap(self) -> float:
        return abs(self.left - self.right)

    @property
    def agrees(self) -> bool:
        """評分較高的是使用者選的那條；同分算不一致。「差不多」的配對不算。"""
        return (self.pair.choice == "L" and self.left > self.right) or (
            self.pair.choice == "R" and self.right > self.left
        )


@dataclass(frozen=True)
class Report:
    results: list[PairResult]
    loco_agreed: int  # 留一個案例交叉驗證的一致數（分母同 decided）
    by_kind: dict[str, tuple[int, int]] = field(init=False)

    def __post_init__(self) -> None:
        by_kind: dict[str, list[int]] = {kind: [0, 0] for kind in KIND_ORDER}
        for r in self.decided_results:
            counts = by_kind.setdefault(kind(r.pair), [0, 0])
            counts[0] += r.agrees
            counts[1] += 1
        object.__setattr__(self, "by_kind", {k: (a, n) for k, (a, n) in by_kind.items()})

    @property
    def decided_results(self) -> list[PairResult]:
        return [r for r in self.results if r.pair.choice != "same"]

    @property
    def agreed(self) -> int:
        return sum(r.agrees for r in self.decided_results)

    @property
    def decided(self) -> int:
        return len(self.decided_results)

    @property
    def same_gap_median(self) -> float:
        return _median([r.gap for r in self.results if r.pair.choice == "same"])

    @property
    def decided_gap_median(self) -> float:
        return _median([r.gap for r in self.decided_results])


def _median(values: list[float]) -> float:
    return statistics.median(values) if values else float("nan")


def split_key(key: str) -> tuple[str, int]:
    """`<variant><rank>` → (variant, rank)。名次只有一位數（樣本最多取前 10 名），變體名稱本身可以含數字（V0、Q2）或只有一個字（F）。"""
    return key[:-1], int(key[-1])


def kind(pair: Pair) -> str:
    """配對的種類：同一個變體是「不同擺放」；含現行（V0：第一輪的現行吸附；Q2：P2.5 Q2 的現行預設）的寫成「現行 vs 另一個」。"""
    variants = sorted((split_key(pair.left)[0], split_key(pair.right)[0]))
    if variants[0] == variants[1]:
        return "不同擺放"
    current = [v for v in variants if v in CURRENT_VARIANTS]
    if len(current) == 1:
        other = variants[1] if variants[0] == current[0] else variants[0]
        return f"現行 vs {other}"
    return f"{variants[0]} vs {variants[1]}"


def _route(segments: list[dict]) -> list[LonLat]:
    """各段的點串起來，同 matching.service._route_points：後一段的第一點（與前一段共用）丟掉，不留零長度的一步。"""
    points: list[LonLat] = []
    for segment in segments:
        pts = [(float(lon), float(lat)) for lon, lat in segment["points"]]
        points.extend(pts if not points else pts[1:])
    return [p for i, p in enumerate(points) if i == 0 or p != points[i - 1]]


def load(data_dir: Path = DATA_DIR) -> tuple[dict[tuple[str, str], Sample], list[Pair]]:
    """讀 routes.json.gz 與 labels.json。樣本以 (案例, <variant><rank>) 為鍵；吸附失敗的路線（有 error）略過。"""
    with gzip.open(data_dir / "routes.json.gz", "rt") as f:
        cases = json.load(f)
    samples: dict[tuple[str, str], Sample] = {}
    for c in cases:
        name = c["case"].removesuffix("@R5")
        strokes = [NormStroke([(x, y) for x, y in s["points"]], s["closed"]) for s in c["shape"]]
        for r in c["routes"]:
            if "error" in r:
                continue
            p = r["placement"]
            placement = Placement((p["center"][0], p["center"][1]), p["size_m"], p["rotation_deg"])
            targets = [s.points + [s.points[0]] if s.closed else s.points for s in place(strokes, placement)]
            key = f"{r['variant']}{r['rank']}"
            samples[(name, key)] = Sample(name, key, targets, _route(r["segments"]), placement.rotation_deg)
    labels = json.loads((data_dir / "labels.json").read_text())
    pairs = [Pair(p["n"], p["case"], p["left"], p["right"], p["choice"]) for p in labels["pairs"]]
    return samples, pairs


def _evaluate(scorer: Scorer, samples: dict[tuple[str, str], Sample], pairs: Sequence[Pair]) -> list[PairResult]:
    cache: dict[tuple[str, str], float] = {}

    def value(case: str, key: str) -> float:
        if (case, key) not in cache:
            cache[(case, key)] = scorer(samples[(case, key)])
        return cache[(case, key)]

    return [PairResult(p, value(p.case, p.left), value(p.case, p.right)) for p in pairs]


def agreement(samples: dict[tuple[str, str], Sample], pairs: Sequence[Pair], fit: Fit) -> Report:
    """一致率報告。樣本內：fit(全部配對) 的評分；交叉驗證：每個案例用 fit(其他案例的配對) 的評分量該案例的配對。"""
    results = _evaluate(fit(pairs), samples, pairs)
    loco = 0
    for case in sorted({p.case for p in pairs}):
        held = [p for p in pairs if p.case == case and p.choice != "same"]
        if held:
            scorer = fit([p for p in pairs if p.case != case])
            loco += sum(r.agrees for r in _evaluate(scorer, samples, held))
    return Report(results, loco)


def v3_rank(sample: Sample) -> float:
    return rank(score(sample.targets, sample.route, V3_DEFAULT), sample.rotation_deg)


def v2_rank(sample: Sample) -> float:
    """對照：v2 的 overall（整數，v2 沒有更細的值）一樣套旋轉扣分。"""
    v2 = score_v2(sample.targets, sample.route)
    return rank(Score(v2.coverage, v2.cleanliness, v2.overall), sample.rotation_deg)


def initial_v3(_training: Sequence[Pair]) -> Scorer:
    """使用者決定用 spec 的初始參數、不做網格搜尋：不看訓練資料，所以交叉驗證等於樣本內。"""
    return v3_rank


def fixed_v2(_training: Sequence[Pair]) -> Scorer:
    return v2_rank


def format_report(title: str, report: Report, *, rows: bool = True) -> str:
    lines = [f"== {title} =="]
    if rows:
        lines.append(f"{'#':>3} {'案例':32} {'左':4} {'右':4} {'選':4} {'左分':>7} {'右分':>7} {'差距':>6}  一致")
        for r in report.results:
            p = r.pair
            mark = "-" if p.choice == "same" else ("○" if r.agrees else "×")
            lines.append(
                f"{p.n:3} {p.case:32} {p.left:4} {p.right:4} {p.choice:4} {r.left:7.2f} {r.right:7.2f} {r.gap:6.2f}  {mark}"
            )
    lines.append(f"有表態的配對一致：{report.agreed}/{report.decided}")
    for name, (agreed, total) in report.by_kind.items():
        lines.append(f"  {name}：{agreed}/{total}")
    lines.append(f"分數差距中位數：差不多 {report.same_gap_median:.2f}、有表態 {report.decided_gap_median:.2f}")
    lines.append(f"留一個案例交叉驗證：{report.loco_agreed}/{report.decided}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="評分和使用者盲測標註的一致率（P2.5 spec §4.4）")
    parser.add_argument("--data", type=Path, default=DATA_DIR, help="routes.json.gz 與 labels.json 所在的目錄")
    args = parser.parse_args(argv)
    samples, pairs = load(args.data)
    print("比較用 rank（overall − 5 × (θ / 15°)²）、不取整數；同分算不一致。")
    print(format_report("v3（spec 初始參數：權重 0.4／0.35／0.25、容差 3／15 px、簡化 1.5 px）", agreement(samples, pairs, initial_v3)))
    print()
    print(format_report("v2（對照，overall 是整數）", agreement(samples, pairs, fixed_v2), rows=False))


if __name__ == "__main__":
    main()
