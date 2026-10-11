"""python -m engine.bench.likeness_samples：下一輪盲測標註的樣本與配對（P2.5 計畫 A6）。

兩個子命令：
- `samples`（在引擎容器內跑，用 current build）：每個搜尋 bench 案例跑一次正式搜尋（同 bench/search.py），取前 `--top`
  名擺放；每個擺放、每個變體（VARIANTS）各吸附一次。寫出 `routes.json.gz`（格式同 tests/data/likeness/，見該目錄的
  README）、`pairs.json` 與 `label.html`（盲測標註頁，用 Artifact 發佈並宣告 db capability）。
- `labels`：把標註頁存在 `votes` 集合的結果（ArtifactData 讀回來的 JSON）轉成 labels.json 的格式，印到 stdout。

使用者決定（計畫「使用者在本 session 的決定」第 6 點）：只產生差異明顯的配對，第一輪 15 組有表態分不出評分好壞，
下一輪目標至少 40 組有表態。所以配對只留 v3 rank 差距 ≥ `--min-gap` 的，和一律保留的變體配對（ALWAYS_PAIRS：現行 vs 規劃器）。
安全斷言沒過的路線照樣記在 routes.json.gz（unsafe／bad_turns／steps_m），但不進配對。
"""

import argparse
import gzip
import json
import math
import os
import random
import re
import statistics
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import date
from pathlib import Path
from typing import Any

from engine.bench.runner import bad_turns, safety_problem, stroke_targets, use_meters
from engine.bench.search import HARD_TIMEOUT_S
from engine.bench.search_cases import SEARCH_CASES, SearchCase
from engine.data.build import DENIED_FILE, load_denied
from engine.data.layout import DataLayout
from engine.matching.deadline import Deadline
from engine.matching.score import rank, score_v2
from engine.matching.service import MatchResult, match_shape
from engine.matching.types import MatchRouter, StartHint
from engine.routing.router import Router
from engine.search.geometry import place
from engine.search.roads import RoadDataMissing, RoadIndex
from engine.search.service import run_search
from engine.search.types import Placement, SearchParams

TEMPLATE_FILE = Path(__file__).with_name("label.tmpl.html")
MATCH_TIMEOUT_S = 10.0  # 每次重新吸附的上限（同上一輪 source/samples.py；bench 不計正式環境的 5 s）
BOX_PX, REF_PX = 240.0, 110.0  # 標註頁的路線縮圖與原圖大小（同 source/pairs.py）
ROUTE_PAD_PX, REF_PAD_PX = 10.0, 6.0
CHOICES = ("L", "R", "same")

type RouteKey = tuple[str, int]  # (variant, 名次)


@dataclass(frozen=True)
class Variant:
    """一種吸附設定。router：Router.from_build 的關鍵字參數；match：match_shape 的額外關鍵字參數。空的 = 現行預設。"""

    router: dict[str, Any]
    match: dict[str, Any]
    description: str


# PR B 加 "F": Variant({}, {"quality": "fine"}, "規劃器") 與 ALWAYS_PAIRS 的 ("F", "Q2")
VARIANTS: dict[str, Variant] = {
    "Q2": Variant({}, {}, "現行預設（turn_penalty_factor 用 Router 預設、剪小刺）"),
    "P2": Variant({"turn_penalty_factor": None}, {"prune": False}, "改前（不帶 turn_penalty_factor、不剪小刺）"),
}
DEFAULT_VARIANTS = "Q2,P2"
# 同一個擺放、兩個變體的配對（每個名次一組）：一律保留（不看差距），與只留差距 ≥ 門檻的
ALWAYS_PAIRS: list[tuple[str, str]] = []
GAP_PAIRS: list[tuple[str, str]] = [("Q2", "P2")]
# 不同擺放的配對：只留差距 ≥ 門檻的
CROSS_PAIRS: list[tuple[RouteKey, RouteKey]] = [(("Q2", 0), ("Q2", 2))]


def parse_variants(raw: str) -> list[str]:
    """`--variants`：逗號分隔的變體名稱；未知、空的、重複的都是 argparse 的錯誤。"""
    names = [name.strip() for name in raw.split(",")]
    if not all(names):
        raise argparse.ArgumentTypeError(f"變體名稱不能是空的，收到 {raw!r}")
    unknown = [name for name in names if name not in VARIANTS]
    if unknown:
        raise argparse.ArgumentTypeError(f"不認得的變體 {', '.join(unknown)}（可用：{', '.join(VARIANTS)}）")
    if len(set(names)) != len(names):
        raise argparse.ArgumentTypeError(f"變體重複，收到 {raw!r}")
    return names


def variant_routers(build: Path, names: Sequence[str]) -> dict[str, Router]:
    return {name: Router.from_build(build, **VARIANTS[name].router) for name in names}


def match_variant(router: MatchRouter, case: SearchCase, placement: Placement, name: str) -> MatchResult:
    """同 bench/likeness 的做法：擺放 → place → match_shape，帶與搜尋相同的起點提示。"""
    return match_shape(
        router,
        place(case.strokes, placement),
        search_radius_m=None,
        deadline=Deadline(MATCH_TIMEOUT_S),
        start=StartHint(case.center, case.radius_m),
        **VARIANTS[name].match,
    )


def _r6(point: tuple[float, float]) -> list[float]:
    return [round(point[0], 6), round(point[1], 6)]


def route_entry(
    name: str,
    rank_index: int,
    placement: Placement,
    targets: list[list[tuple[float, float]]],
    result: MatchResult,
    ms: int,
    checker: MatchRouter,
    denied_ids: frozenset[int],
) -> dict:
    """routes.json.gz 的一條路線（欄位見 tests/data/likeness/README.md）。"""
    unsafe = safety_problem(checker, result.segments, denied_ids)
    v3, v2 = result.score, score_v2(targets, result.route_points)
    return {
        "variant": name,
        "rank": rank_index,
        "placement": {
            "center": list(placement.center),
            "size_m": round(placement.size_m, 1),
            "rotation_deg": round(placement.rotation_deg, 1),
        },
        "score_v3": {
            "overall": v3.overall, "coverage": v3.coverage, "cleanliness": v3.cleanliness,
            "direction": v3.direction, "exact": round(v3.exact, 3),
        },
        "rank_score": round(rank(v3, placement.rotation_deg), 3),
        "score_v2": {"overall": v2.overall, "coverage": v2.coverage, "cleanliness": v2.cleanliness},
        "route_m": round(result.route_length_m),
        "target_m": round(result.target_length_m),
        "ms": ms,
        "warnings": [w.code for w in result.warnings],
        "unsafe": None if unsafe is None else f"{unsafe[0]}：{unsafe[1]}",
        "bad_turns": bad_turns(checker, result.segments),
        "steps_m": use_meters(checker, result.segments).get("steps", 0.0),
        "segments": [
            {"kind": s.kind, "stroke": s.stroke_index, "points": [_r6(p) for p in s.points]} for s in result.segments
        ],
    }


def is_safe(route: dict) -> bool:
    """吸附成功而且通過安全斷言（與 bench 同一套：沒有缺口與禁行、接點轉彎能直接騎、不走樓梯）。"""
    return "error" not in route and route["unsafe"] is None and route["bad_turns"] == 0 and route["steps_m"] == 0


def key_text(key: RouteKey) -> str:
    """labels.json 的 left／right：<variant><rank>。"""
    return f"{key[0]}{key[1]}"


def sample_case(
    case: SearchCase,
    placements: Sequence[Placement],
    routers: dict[str, MatchRouter],
    checker: MatchRouter,
    denied_ids: frozenset[int],
) -> dict:
    """一個案例：每個擺放（名次）× 每個變體吸附一次。吸附失敗記 {variant, rank, error}；安全斷言沒過的在 stderr 警告。"""
    entry: dict = {
        "case": case.name,
        "center": list(case.center),
        "radius_m": case.radius_m,
        "shape": [{"points": [list(p) for p in s.points], "closed": s.closed} for s in case.strokes],
        "routes": [],
    }
    for rank_index, placement in enumerate(placements):
        targets = stroke_targets(place(case.strokes, placement))
        for name, router in routers.items():
            started = time.monotonic()
            try:
                result = match_variant(router, case, placement, name)
            except Exception as error:  # noqa: BLE001  一條路線失敗不擋住其他路線
                entry["routes"].append({"variant": name, "rank": rank_index, "error": type(error).__name__})
                continue
            ms = round((time.monotonic() - started) * 1000)
            route = route_entry(name, rank_index, placement, targets, result, ms, checker, denied_ids)
            entry["routes"].append(route)
            if not is_safe(route):
                problem = route["unsafe"] or f"bad_turns {route['bad_turns']}、樓梯 {route['steps_m']} m"
                print(f"警告：{case.name} {key_text((name, rank_index))} 安全斷言沒過，不進配對（{problem}）", file=sys.stderr)
    return entry


# ---- 配對 ----


@dataclass(frozen=True)
class PairCandidate:
    case: str  # 完整案例名稱（含 @R5）
    left: RouteKey
    right: RouteKey
    gap: float  # |rank_score 差|
    always: bool  # 一律保留的變體配對（不看差距）


def case_pairs(entry: dict) -> list[PairCandidate]:
    """一個案例的配對候選（還沒依差距篩）。兩邊都要有吸附成功、通過安全斷言的路線；沒跑的變體就沒有配對。"""
    routes = {(r["variant"], r["rank"]): r for r in entry["routes"] if is_safe(r)}
    ranks = sorted({k[1] for k in routes})
    wanted: list[tuple[RouteKey, RouteKey, bool]] = []
    for a, b in ALWAYS_PAIRS:
        wanted += [((a, r), (b, r), True) for r in ranks]
    for a, b in GAP_PAIRS:
        wanted += [((a, r), (b, r), False) for r in ranks]
    wanted += [(a, b, False) for a, b in CROSS_PAIRS]
    out: list[PairCandidate] = []
    seen: set[frozenset[RouteKey]] = set()
    for a, b, always in wanted:
        pair = frozenset((a, b))
        if a in routes and b in routes and pair not in seen:  # 同一組出現兩次時留先列的（一律保留的在前）
            seen.add(pair)
            gap = abs(routes[a]["rank_score"] - routes[b]["rank_score"])
            out.append(PairCandidate(entry["case"], a, b, gap, always))
    return out


def _priority(c: PairCandidate) -> tuple[bool, float]:
    return (not c.always, -c.gap)


def select_pairs(candidates: Sequence[PairCandidate], *, min_gap: float, max_pairs: int) -> list[PairCandidate]:
    """留下一律保留的與差距 ≥ min_gap 的；同案例平均分配（round-robin）挑到 max_pairs 組。

    每個案例的佇列：一律保留的在前，其餘依差距由大到小。每一輪每個案例拿佇列的第一組，輪內同樣排序（名額在一輪中間
    用完時差距大的先拿）；同分照案例出現的順序。結果是確定的。
    """
    queues: dict[str, list[PairCandidate]] = {}
    for c in candidates:
        if c.always or c.gap >= min_gap:
            queues.setdefault(c.case, []).append(c)
    for queue in queues.values():
        queue.sort(key=_priority)
    picked: list[PairCandidate] = []
    depth = 0
    while len(picked) < max_pairs:
        round_ = sorted((q[depth] for q in queues.values() if depth < len(q)), key=_priority)
        if not round_:
            break
        picked += round_[: max_pairs - len(picked)]
        depth += 1
    return picked


def arrange(selected: Sequence[PairCandidate], seed: int) -> list[PairCandidate]:
    """左右隨機、整體打亂（random.Random(seed)，確定的）。回傳的順序就是標註頁的順序，n = 索引 + 1。"""
    rng = random.Random(seed)
    sided = [replace(c, left=c.right, right=c.left) if rng.random() < 0.5 else c for c in selected]
    rng.shuffle(sided)
    return sided


def plan_pairs(entries: Sequence[dict], *, min_gap: float, max_pairs: int, seed: int) -> list[PairCandidate]:
    candidates = [c for e in entries for c in case_pairs(e)]
    return arrange(select_pairs(candidates, min_gap=min_gap, max_pairs=max_pairs), seed)


def short_case(case: str) -> str:
    """labels.json 的 case：案例名稱去掉 @R5（同上一輪）。"""
    return case.split("@R5")[0]


def pair_id(c: PairCandidate) -> str:
    return f"{short_case(c.case)}|{key_text(c.left)}|{key_text(c.right)}"


def fit(paths: Sequence[Sequence[tuple[float, float]]], box: float, pad: float = ROUTE_PAD_PX) -> str:
    """SVG path：全部的線一起縮放到 box 方框內（長邊 = box − 2 × pad、置中），y 向上 → 北朝上；取到 0.1 px，連續重複的點只留一個。
    同 tests/data/likeness/source/pairs.py 的 fit。"""
    xs = [x for p in paths for x, _ in p]
    ys = [y for p in paths for _, y in p]
    if not xs:
        return ""
    w, h = max(xs) - min(xs) or 1e-9, max(ys) - min(ys) or 1e-9
    s = (box - 2 * pad) / max(w, h)
    ox, oy = (box - w * s) / 2 - min(xs) * s, (box - h * s) / 2 + max(ys) * s
    out = []
    for p in paths:
        pts: list[tuple[float, float]] = []
        for x, y in p:
            q = (round(x * s + ox, 1), round(oy - y * s, 1))
            if not pts or q != pts[-1]:
                pts.append(q)
        out.append("M" + "L".join(f"{a:g},{b:g}" for a, b in pts))
    return "".join(out)


def route_svg(route: dict) -> str:
    """路線縮圖：經度乘 cos(擺放中心的緯度)，近似等距。"""
    k = math.cos(math.radians(route["placement"]["center"][1]))
    return fit([[(lon * k, lat) for lon, lat in s["points"]] for s in route["segments"]], BOX_PX)


def shape_svg(shape: list[dict]) -> str:
    """原圖（正規化座標、不旋轉），封閉筆畫補回起點。"""
    lines = [[tuple(p) for p in s["points"]] + ([tuple(s["points"][0])] if s["closed"] else []) for s in shape]
    return fit(lines, REF_PX, REF_PAD_PX)


def build_pairs(entries: Sequence[dict], arranged: Sequence[PairCandidate]) -> list[dict]:
    """pairs.json：[{id, ref, l, r, n}]，順序同 arranged。"""
    by_case = {e["case"]: e for e in entries}
    out = []
    for n, c in enumerate(arranged, 1):
        entry = by_case[c.case]
        routes = {(r["variant"], r["rank"]): r for r in entry["routes"] if is_safe(r)}
        out.append({
            "id": pair_id(c), "ref": shape_svg(entry["shape"]),
            "l": route_svg(routes[c.left]), "r": route_svg(routes[c.right]), "n": n,
        })
    return out


def label_html(pairs: Sequence[dict]) -> str:
    """標註頁：樣板的 __PAIRS__ 換成 pairs（JSON；`</` 跳脫，免得提早結束 <script>）、__TOTAL__ 換成組數。"""
    data = json.dumps(list(pairs), ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    template = TEMPLATE_FILE.read_text(encoding="utf-8")
    return template.replace("__PAIRS__", data).replace("__TOTAL__", str(len(pairs)))


def write_routes(path: Path, entries: Sequence[dict]) -> None:
    with gzip.open(path, "wt", encoding="utf-8") as f:
        json.dump(list(entries), f, ensure_ascii=False, separators=(",", ":"))


@dataclass
class _CaseStats:
    candidates: int = 0
    over_gap: int = 0
    selected: list[float] = field(default_factory=list)


def _gap_buckets(gaps: Sequence[float], min_gap: float) -> str:
    """選入配對的差距分布：[門檻, 5)、[5, 10)、[10, 20)、≥ 20（門檻以下的區間省略）。"""
    edges = [min_gap] + [e for e in (5.0, 10.0, 20.0) if e > min_gap]
    labels = [f"[{a:g}, {b:g})" for a, b in zip(edges, edges[1:])] + [f"≥ {edges[-1]:g}"]
    counts = [0] * len(labels)
    for g in gaps:
        if g < edges[0]:
            continue  # 一律保留而差距不到門檻的，另外算
        counts[sum(g >= e for e in edges[1:])] += 1
    below = sum(g < edges[0] for g in gaps)
    parts = [f"{label} {n}" for label, n in zip(labels, counts)]
    if below:
        parts.append(f"不到門檻（一律保留）{below}")
    return "、".join(parts)


def summary(entries: Sequence[dict], arranged: Sequence[PairCandidate], *, min_gap: float, variants: Sequence[str]) -> str:
    """每個案例各變體的 v3（各名次的 rank_score；x = 吸附失敗、! = 安全斷言沒過）、候選數、留下的配對數與差距分布。"""
    stats = {e["case"]: _CaseStats() for e in entries}
    for e in entries:
        for c in case_pairs(e):
            stats[e["case"]].candidates += 1
            stats[e["case"]].over_gap += c.always or c.gap >= min_gap
    for c in arranged:
        stats[c.case].selected.append(c.gap)
    lines = [f"v3 是各名次的 rank（overall − 5 × (θ/15°)²）；x = 吸附失敗、! = 安全斷言沒過（不進配對）；門檻 {min_gap:g}"]
    for e in entries:
        parts = []
        for name in variants:
            cells = []
            for r in sorted((r for r in e["routes"] if r["variant"] == name), key=lambda r: r["rank"]):
                cells.append("x" if "error" in r else f"{r['rank_score']:.1f}{'' if is_safe(r) else '!'}")
            parts.append(f"{name} {' '.join(cells) or '-'}")
        s = stats[e["case"]]
        gaps = f"、差距 {min(s.selected):.1f}–{max(s.selected):.1f}" if s.selected else ""
        notice = f"（搜尋失敗：{e['error']}）" if "error" in e else ""
        lines.append(
            f"{e['case']:34} {'｜'.join(parts)}｜候選 {s.candidates}、達門檻 {s.over_gap}、配對 {len(s.selected)}{gaps}{notice}"
        )
    all_gaps = [c.gap for c in arranged]
    total = sum(s.candidates for s in stats.values())
    over = sum(s.over_gap for s in stats.values())
    lines.append(f"共 {len(arranged)} 組配對（候選 {total}、達門檻或一律保留 {over}）")
    if all_gaps:
        lines.append(f"差距中位數 {statistics.median(all_gaps):.1f}；分布：{_gap_buckets(all_gaps, min_gap)}")
    return "\n".join(lines)


# ---- votes → labels ----


class VotesError(ValueError):
    """votes.json 的內容對不上 pairs.json 或格式不對：不悄悄略過。"""


def _vote_rows(votes: object) -> list[tuple[str, object]]:
    """兩種形狀（ArtifactData 讀回 votes 集合的內容）：
    - 列表：[{id: "p01", data: {pair, choice, n, at}}, …]（list／query 的 documents）；
    - 物件：{"p01": {pair, choice, n, at}, …}（以 doc id 為鍵）。
    回傳 [(doc id, 欄位)]。"""
    if isinstance(votes, list):
        rows = []
        for k, doc in enumerate(votes):
            if not isinstance(doc, dict) or "data" not in doc:
                raise VotesError(f"第 {k + 1} 筆沒有 data 欄位：{doc!r}")
            rows.append((str(doc.get("id", f"#{k + 1}")), doc["data"]))
        return rows
    if isinstance(votes, dict):
        return [(str(doc_id), data) for doc_id, data in votes.items()]
    raise VotesError(f"votes 的形狀不對：要列表 [{{id, data}}] 或以 doc id 為鍵的物件，收到 {type(votes).__name__}")


def votes_to_labels(votes: object, pairs: Sequence[dict], collected_at: str) -> tuple[dict, int]:
    """回傳 (labels.json 的內容, 沒標的組數)。pair 對不上 pairs.json、編號不符、choice 不合法、同一組標兩次都丟 VotesError。"""
    by_id = {p["id"]: p for p in pairs}
    out: dict[str, dict] = {}
    for doc_id, data in _vote_rows(votes):
        if not isinstance(data, dict) or not isinstance(data.get("pair"), str):
            raise VotesError(f"{doc_id}：沒有 pair 欄位")
        pair_text = data["pair"]
        if pair_text not in by_id:
            raise VotesError(f"{doc_id}：pair {pair_text!r} 對不上 pairs.json")
        if pair_text in out:
            raise VotesError(f"{doc_id}：pair {pair_text!r} 重複標了兩次")
        if data.get("choice") not in CHOICES:
            raise VotesError(f"{doc_id}：choice 必須是 L、R 或 same，收到 {data.get('choice')!r}")
        expected = by_id[pair_text]["n"]
        if data.get("n") != expected:
            raise VotesError(f"{doc_id}：編號 n = {data.get('n')!r}，pairs.json 裡 {pair_text!r} 是第 {expected} 組")
        case, left, right = pair_text.split("|")
        out[pair_text] = {"n": expected, "case": case, "left": left, "right": right, "choice": data["choice"]}
    labels = {"collected_at": collected_at, "pairs": sorted(out.values(), key=lambda p: p["n"])}
    return labels, len(pairs) - len(out)


def _date_arg(raw: str) -> str:
    try:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
            raise ValueError
        date.fromisoformat(raw)
    except ValueError:
        raise argparse.ArgumentTypeError(f"日期要是 YYYY-MM-DD，收到 {raw!r}") from None
    return raw


# ---- 命令列 ----


def _positive_int(raw: str) -> int:
    value = int(raw)
    if value < 1:
        raise argparse.ArgumentTypeError(f"必須 ≥ 1，收到 {raw!r}")
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="下一輪盲測標註的樣本與配對（P2.5 計畫 A6）")
    sub = parser.add_subparsers(dest="command", required=True)
    s = sub.add_parser("samples", help="跑搜尋與各變體吸附，寫出 routes.json.gz、pairs.json、label.html（在引擎容器內）")
    s.add_argument("--out", type=Path, required=True, help="輸出目錄，不存在就建立")
    s.add_argument("--data-dir", type=Path, default=Path(os.environ.get("DATA_DIR", "/data")))
    s.add_argument("--case", action="append", default=[], help="只跑名稱含這個字串的案例（可重複）")
    s.add_argument("--top", type=_positive_int, default=3, help="每個案例取搜尋的前幾名擺放（預設 3）")
    s.add_argument("--min-gap", type=float, default=3.0, help="一般配對的 v3 rank 差距門檻（預設 3）")
    s.add_argument("--max-pairs", type=_positive_int, default=60, help="最多幾組配對（預設 60）")
    s.add_argument("--seed", type=int, default=7, help="左右與順序的亂數種子（預設 7）")
    s.add_argument(
        "--variants", type=parse_variants, default=parse_variants(DEFAULT_VARIANTS),
        help=f"要跑的變體，逗號分隔（預設 {DEFAULT_VARIANTS}；可用：{'、'.join(f'{k} {v.description}' for k, v in VARIANTS.items())}）",
    )
    lab = sub.add_parser("labels", help="把標註頁的 votes 轉成 labels.json（印到 stdout）")
    lab.add_argument("--votes", type=Path, required=True, help="ArtifactData 讀回 votes 集合的 JSON")
    lab.add_argument("--pairs", type=Path, required=True, help="samples 寫的 pairs.json")
    lab.add_argument("--date", type=_date_arg, required=True, help="collected_at（YYYY-MM-DD）")
    return parser


def _samples(args: argparse.Namespace) -> None:
    build = DataLayout(args.data_dir).current()
    if build is None:
        sys.exit("沒有可用的路網資料")
    try:
        roads = RoadIndex.load(build)
    except RoadDataMissing as error:
        sys.exit(f"目前的 build 沒有可騎道路索引（要重建路網）：{error}")
    try:
        denied_ids = frozenset(d.id for d in load_denied(build))
    except (OSError, ValueError, KeyError, TypeError) as error:
        sys.exit(f"讀不到 {build / DENIED_FILE}，無法檢查安全：{error}")
    cases = [c for c in SEARCH_CASES if not args.case or any(s in c.name for s in args.case)]
    routers = variant_routers(build, args.variants)
    checker = Router.from_build(build)  # 安全斷言用的 route、edges 不受吸附參數影響
    search_routers = int(os.environ.get("SEARCH_ROUTERS", "2"))
    print(f"build {build.name}；變體 {'、'.join(args.variants)}；每個案例前 {args.top} 名擺放", flush=True)
    entries = []
    for case in cases:
        params = SearchParams(
            case.strokes, case.center, case.radius_m, case.min_km, case.max_km, case.rotation[0], case.rotation[1]
        )
        try:
            outcome = run_search(
                params, [Router.from_build(build) for _ in range(search_routers)], roads, Deadline(HARD_TIMEOUT_S), lambda e: None
            )
            placements = [c.placement for c in outcome.candidates[: args.top]]
            error = None
        except Exception as e:  # noqa: BLE001  一個案例失敗不擋住其他案例
            placements, error = [], type(e).__name__
        entry = sample_case(case, placements, routers, checker, denied_ids)
        if error:
            entry["error"] = error
        entries.append(entry)
        print(f"{case.name}：{len(entry['routes'])} 條路線{f'（搜尋失敗：{error}）' if error else ''}", file=sys.stderr, flush=True)
    args.out.mkdir(parents=True, exist_ok=True)
    write_routes(args.out / "routes.json.gz", entries)
    arranged = plan_pairs(entries, min_gap=args.min_gap, max_pairs=args.max_pairs, seed=args.seed)
    pairs = build_pairs(entries, arranged)
    (args.out / "pairs.json").write_text(json.dumps(pairs, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    if pairs:
        (args.out / "label.html").write_text(label_html(pairs), encoding="utf-8")
    else:
        print("沒有任何配對：不寫 label.html（調低 --min-gap 或多跑幾個案例）", file=sys.stderr)
    print(summary(entries, arranged, min_gap=args.min_gap, variants=args.variants), flush=True)


def _labels(args: argparse.Namespace) -> None:
    pairs = json.loads(args.pairs.read_text(encoding="utf-8"))
    votes = json.loads(args.votes.read_text(encoding="utf-8"))
    try:
        labels, missing = votes_to_labels(votes, pairs, args.date)
    except VotesError as error:
        sys.exit(f"votes 轉不了：{error}")
    print(json.dumps(labels, ensure_ascii=False, indent=1))
    decided = sum(p["choice"] != "same" for p in labels["pairs"])
    note = f"，還有 {missing} 組沒標" if missing else ""
    print(f"共 {len(pairs)} 組：已標 {len(labels['pairs'])} 組（有表態 {decided} 組）{note}", file=sys.stderr)


def main(argv: list[str] | None = None) -> None:
    args = _parser().parse_args(argv)
    if args.command == "samples":
        _samples(args)
    else:
        _labels(args)


if __name__ == "__main__":
    main()
