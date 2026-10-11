"""下一輪標註樣本工具（P2.5 計畫 A6）：配對篩選、變體表、pairs.json／label.html 的格式、votes 轉 labels。"""

import gzip
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from engine.bench import likeness
from engine.bench import likeness_samples as ls
from engine.bench.likeness_samples import PairCandidate
from engine.bench.runner import stroke_targets
from engine.bench.search_cases import SearchCase
from engine.matching.deadline import MatchTimeout
from engine.matching.score import Score
from engine.matching.service import MatchResult
from engine.matching.types import MatchWarning, Segment
from engine.routing.geo import polyline_length_m
from engine.routing.router import EdgeInfo
from engine.search.geometry import place
from engine.search.types import NormStroke, Placement

SQUARE = [NormStroke([(-0.5, -0.5), (0.5, -0.5), (0.5, 0.5), (-0.5, 0.5)], closed=True)]
SHAPE = [{"points": [[-0.5, -0.5], [0.5, -0.5], [0.5, 0.5], [-0.5, 0.5]], "closed": True}]


def placement_of(rank: int) -> Placement:
    return Placement((121.5 + 0.01 * rank, 25.05), 400.0, 5.0 * rank)


def square_segments(rank: int, offset: float = 0.0) -> list[dict]:
    (target,) = stroke_targets(place(SQUARE, placement_of(rank)))
    points = [[round(lon + offset, 6), round(lat, 6)] for lon, lat in target]
    return [{"kind": "stroke", "stroke": 0, "points": points}]


def route(variant: str, rank: int, rank_score: float, **overrides) -> dict:
    p = placement_of(rank)
    base = {
        "variant": variant, "rank": rank,
        "placement": {"center": list(p.center), "size_m": p.size_m, "rotation_deg": p.rotation_deg},
        "score_v3": {"overall": round(rank_score), "coverage": 90, "cleanliness": 80, "direction": 70, "exact": rank_score},
        "rank_score": rank_score,
        "score_v2": {"overall": 70, "coverage": 80, "cleanliness": 60},
        "route_m": 1700, "target_m": 1600, "ms": 120, "warnings": [], "unsafe": None, "bad_turns": 0, "steps_m": 0.0,
        "segments": square_segments(rank, 0.0001 if variant == "P2" else 0.0),
    }
    return {**base, **overrides}


def entry(case: str, routes: list[dict]) -> dict:
    return {"case": case, "center": [121.5, 25.05], "radius_m": 5000.0, "shape": SHAPE, "routes": routes}


def keys(candidates: list[PairCandidate]) -> set[tuple[str, tuple[str, int], tuple[str, int]]]:
    return {(c.case, c.left, c.right) for c in candidates}


# ---- 配對候選與篩選 ----


def test_gap_threshold_keeps_only_clear_differences() -> None:
    e = entry("heart@x@R5", [
        route("Q2", 0, 80.0), route("P2", 0, 78.0),  # 差 2：不留
        route("Q2", 1, 80.0), route("P2", 1, 70.0),  # 差 10：留
        route("Q2", 2, 77.0), route("P2", 2, 74.0),  # 差 3：剛好達門檻，留
    ])
    candidates = ls.case_pairs(e)
    assert keys(candidates) == {
        ("heart@x@R5", ("Q2", r), ("P2", r)) for r in range(3)
    } | {("heart@x@R5", ("Q2", 0), ("Q2", 2))}
    gaps = {(c.left, c.right): c.gap for c in candidates}
    assert gaps[(("Q2", 0), ("Q2", 2))] == pytest.approx(3.0)
    assert not any(c.always for c in candidates)
    selected = ls.select_pairs(candidates, min_gap=3.0, max_pairs=60)
    assert keys(selected) == {
        ("heart@x@R5", ("Q2", 1), ("P2", 1)),
        ("heart@x@R5", ("Q2", 2), ("P2", 2)),
        ("heart@x@R5", ("Q2", 0), ("Q2", 2)),
    }
    assert [c.gap for c in selected] == sorted((c.gap for c in selected), reverse=True)  # 同一個案例內依差距由大到小
    assert ls.select_pairs(candidates, min_gap=3.5, max_pairs=60)[0].left == ("Q2", 1)
    assert len(ls.select_pairs(candidates, min_gap=3.5, max_pairs=60)) == 1


def test_always_pairs_ignore_the_gap(monkeypatch: pytest.MonkeyPatch) -> None:
    # PR B 的「現行 vs 規劃器」：差距 0 也留，而且排在同一個案例有差距的配對前面
    monkeypatch.setattr(ls, "ALWAYS_PAIRS", [("F", "Q2")])
    e = entry("hi@x@R5", [route("Q2", 0, 80.0), route("P2", 0, 60.0), route("F", 0, 80.0)])
    candidates = ls.case_pairs(e)
    always = [c for c in candidates if c.always]
    assert keys(always) == {("hi@x@R5", ("F", 0), ("Q2", 0))}
    selected = ls.select_pairs(candidates, min_gap=3.0, max_pairs=60)
    assert [(c.left, c.right) for c in selected] == [(("F", 0), ("Q2", 0)), (("Q2", 0), ("P2", 0))]
    assert [(c.left, c.right) for c in ls.select_pairs(candidates, min_gap=3.0, max_pairs=1)] == [(("F", 0), ("Q2", 0))]


def test_variants_not_run_produce_no_pairs() -> None:
    e = entry("hi@x@R5", [route("Q2", 0, 80.0), route("Q2", 1, 60.0), route("Q2", 2, 50.0)])
    assert keys(ls.case_pairs(e)) == {("hi@x@R5", ("Q2", 0), ("Q2", 2))}  # 只跑 Q2：沒有 Q2 vs P2


@pytest.mark.parametrize(
    "bad",
    [
        {"unsafe": "UnsafeRoute：第 0、1 段之間缺口 3.0 m"},
        {"bad_turns": 1},
        {"steps_m": 4.5},
    ],
)
def test_routes_failing_safety_never_enter_pairs(bad: dict) -> None:
    e = entry("hi@x@R5", [
        route("Q2", 0, 90.0), route("P2", 0, 50.0, **bad),
        route("Q2", 1, 90.0), route("P2", 1, 50.0),
        route("Q2", 2, 40.0, **bad),
    ])
    assert keys(ls.case_pairs(e)) == {("hi@x@R5", ("Q2", 1), ("P2", 1))}


def test_failed_matches_never_enter_pairs() -> None:
    e = entry("hi@x@R5", [route("Q2", 0, 90.0), {"variant": "P2", "rank": 0, "error": "MatchTimeout"}])
    assert ls.case_pairs(e) == []


def test_round_robin_spreads_pairs_over_cases_and_respects_max_pairs() -> None:
    def cand(case: str, rank: int, gap: float) -> PairCandidate:
        return PairCandidate(case, ("Q2", rank), ("P2", rank), gap, False)

    a = [cand("a", r, g) for r, g in enumerate([30.0, 25.0, 20.0, 15.0])]
    b = [cand("b", 0, 4.0)]
    c = [cand("c", 0, 10.0), cand("c", 1, 5.0)]
    selected = ls.select_pairs(a + b + c, min_gap=3.0, max_pairs=5)
    # 第一輪每個案例各拿差距最大的一組（輪內依差距排）；第二輪 b 沒了，a、c 各一組
    assert [(s.case, s.gap) for s in selected] == [("a", 30.0), ("c", 10.0), ("b", 4.0), ("a", 25.0), ("c", 5.0)]
    # 名額在一輪中間用完：輪內差距大的先拿
    assert [(s.case, s.gap) for s in ls.select_pairs(a + b + c, min_gap=3.0, max_pairs=2)] == [("a", 30.0), ("c", 10.0)]
    assert len(ls.select_pairs(a + b + c, min_gap=3.0, max_pairs=100)) == 7


def test_arrange_is_deterministic_random_sided_and_numbered() -> None:
    selected = [PairCandidate(f"case{k}@R5", ("Q2", 0), ("P2", 0), 10.0 - k, False) for k in range(8)]
    first = ls.arrange(selected, seed=7)
    assert first == ls.arrange(selected, seed=7)
    assert {c.case for c in first} == {c.case for c in selected}
    assert {(c.left, c.right) for c in first} == {(("Q2", 0), ("P2", 0)), (("P2", 0), ("Q2", 0))}  # 左右都有
    assert [c.case for c in first] != [c.case for c in selected]  # 整體打亂
    others = [ls.arrange(selected, seed=s) for s in range(1, 6)]
    assert any(o != first for o in others)


def test_pair_id_format() -> None:
    c = PairCandidate("default_text@taipei_daan@R5", ("P2", 1), ("Q2", 1), 4.0, False)
    assert ls.pair_id(c) == "default_text@taipei_daan|P21|Q21"


# ---- pairs.json、label.html ----


def two_cases() -> list[dict]:
    return [
        entry("heart@a@R5", [route("Q2", r, 90.0 - 10 * r) for r in range(3)] + [route("P2", r, 70.0) for r in range(3)]),
        entry("hi@b@R5", [route("Q2", 0, 80.0), route("P2", 0, 50.0)]),
    ]


def test_build_pairs_matches_the_previous_round_format() -> None:
    entries = two_cases()
    arranged = ls.plan_pairs(entries, min_gap=3.0, max_pairs=60, seed=7)
    pairs = ls.build_pairs(entries, arranged)
    assert [p["n"] for p in pairs] == list(range(1, len(pairs) + 1))
    assert [list(p) for p in pairs] == [["id", "ref", "l", "r", "n"]] * len(pairs)
    assert len({p["id"] for p in pairs}) == len(pairs)
    path = re.compile(r"^(M[-\d.]+,[-\d.]+(L[-\d.]+,[-\d.]+)*)+$")
    for p in pairs:
        assert re.fullmatch(r"[^|@]+@[^|@]+\|(Q2|P2)\d\|(Q2|P2)\d", p["id"])
        assert all(path.match(p[k]) for k in ("ref", "l", "r"))
        for k, box in (("ref", 110), ("l", 240), ("r", 240)):
            numbers = [float(v) for v in re.findall(r"[-\d.]+", p[k])]
            assert 0 <= min(numbers) and max(numbers) <= box  # 縮放到框內
    # 原圖：正方形補回起點；北朝上（y 大的在上面 = SVG 的 y 小）
    ref = pairs[0]["ref"]
    assert ref.startswith("M6,104L104,104L104,6L6,6L6,104")


def test_fit_keeps_aspect_and_north_up() -> None:
    d = ls.fit([[(0.0, 0.0), (2.0, 0.0), (2.0, 1.0)]], 240.0)
    # 長邊 2 → 220 px，置中；y = 1（北）在上
    assert d == "M10,175L230,175L230,65"


def test_label_html_substitutes_everything(tmp_path: Path) -> None:
    entries = two_cases()
    pairs = ls.build_pairs(entries, ls.plan_pairs(entries, min_gap=3.0, max_pairs=60, seed=7))
    html = ls.label_html(pairs)
    assert "__" not in html
    assert f"共 {len(pairs)} 組" in html
    script = re.search(r"<script>(.*?)</script>", html, re.S).group(1)
    raw = re.search(r"const PAIRS = (.*?);\n", script, re.S).group(1)
    assert json.loads(raw) == pairs
    assert 'window.claude?.use("db")' in script and 'collection("votes")' in script
    assert "PAIRS.length" in script and "36" not in html  # 組數不寫死


def test_label_html_escapes_script_end() -> None:
    pairs = [{"id": "a</script>b|Q20|P20", "ref": "M0,0", "l": "M0,0", "r": "M0,0", "n": 1}]
    html = ls.label_html(pairs)
    script = re.search(r"<script>(.*?)</script>", html, re.S).group(1)
    raw = re.search(r"const PAIRS = (.*?);\n", script, re.S).group(1)
    assert json.loads(raw) == pairs


def test_template_ships_inside_the_package() -> None:
    assert ls.TEMPLATE_FILE.parent == Path(ls.__file__).parent
    template = ls.TEMPLATE_FILE.read_text()
    assert template.count("__PAIRS__") == 1 and "__TOTAL__" in template


# ---- votes → labels ----


def sample_pairs() -> list[dict]:
    return [
        {"id": "heart@a|Q20|P20", "ref": "", "l": "", "r": "", "n": 1},
        {"id": "heart@a|Q22|Q20", "ref": "", "l": "", "r": "", "n": 2},
        {"id": "hi@b|P20|Q20", "ref": "", "l": "", "r": "", "n": 3},
    ]


def vote(pair: str, choice: str, n: int) -> dict:
    return {"pair": pair, "choice": choice, "n": n, "at": "2026-10-12T01:02:03Z"}


def test_votes_list_shape_becomes_labels() -> None:
    votes = [{"id": "p03", "data": vote("hi@b|P20|Q20", "R", 3)}, {"id": "p01", "data": vote("heart@a|Q20|P20", "same", 1)}]
    labels, missing = ls.votes_to_labels(votes, sample_pairs(), "2026-10-12")
    assert labels == {
        "collected_at": "2026-10-12",
        "pairs": [
            {"n": 1, "case": "heart@a", "left": "Q20", "right": "P20", "choice": "same"},
            {"n": 3, "case": "hi@b", "left": "P20", "right": "Q20", "choice": "R"},
        ],
    }
    assert missing == 1


def test_votes_mapping_shape_becomes_labels() -> None:
    votes = {"p02": vote("heart@a|Q22|Q20", "L", 2)}
    labels, missing = ls.votes_to_labels(votes, sample_pairs(), "2026-10-12")
    assert labels["pairs"] == [{"n": 2, "case": "heart@a", "left": "Q22", "right": "Q20", "choice": "L"}]
    assert missing == 2


@pytest.mark.parametrize(
    ("votes", "message"),
    [
        ({"p01": vote("heart@a|Q20|P20", "left", 1)}, "choice"),
        ({"p09": vote("heart@a|Q29|P29", "L", 9)}, "對不上"),
        ({"p01": vote("heart@a|Q20|P20", "L", 1), "x": vote("heart@a|Q20|P20", "R", 1)}, "重複"),
        ({"p01": vote("heart@a|Q20|P20", "L", 2)}, "編號"),
        ({"p01": {"choice": "L"}}, "pair"),
        ([{"id": "p01"}], "data"),
        ("nope", "形狀"),
    ],
)
def test_bad_votes_are_errors(votes, message: str) -> None:
    with pytest.raises(ls.VotesError, match=message):
        ls.votes_to_labels(votes, sample_pairs(), "2026-10-12")


def test_labels_cli(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "pairs.json").write_text(json.dumps(sample_pairs()))
    (tmp_path / "votes.json").write_text(json.dumps({"p01": vote("heart@a|Q20|P20", "L", 1)}))
    ls.main(["labels", "--votes", str(tmp_path / "votes.json"), "--pairs", str(tmp_path / "pairs.json"), "--date", "2026-10-12"])
    out, err = capsys.readouterr()
    assert json.loads(out)["pairs"] == [{"n": 1, "case": "heart@a", "left": "Q20", "right": "P20", "choice": "L"}]
    assert "2" in err and "沒標" in err


def test_labels_cli_bad_votes_exit_nonzero(tmp_path: Path) -> None:
    (tmp_path / "pairs.json").write_text(json.dumps(sample_pairs()))
    (tmp_path / "votes.json").write_text(json.dumps({"p01": vote("nope|Q20|P20", "L", 1)}))
    with pytest.raises(SystemExit, match="對不上"):
        ls.main(["labels", "--votes", str(tmp_path / "votes.json"), "--pairs", str(tmp_path / "pairs.json"), "--date", "2026-10-12"])


def test_labels_cli_rejects_bad_date(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as usage:
        ls.main(["labels", "--votes", "v.json", "--pairs", "p.json", "--date", "10/12"])
    assert usage.value.code == 2


def test_generated_files_round_trip_through_likeness_loader(tmp_path: Path) -> None:
    """samples 寫的 routes.json.gz＋labels 轉出來的 labels.json，bench/likeness.py 讀得懂、每組配對的兩條路線都找得到。"""
    entries = two_cases()
    ls.write_routes(tmp_path / "routes.json.gz", entries)
    pairs = ls.build_pairs(entries, ls.plan_pairs(entries, min_gap=3.0, max_pairs=60, seed=7))
    votes = {f"p{p['n']:02d}": vote(p["id"], "L", p["n"]) for p in pairs}
    labels, missing = ls.votes_to_labels(votes, pairs, "2026-10-12")
    assert missing == 0
    (tmp_path / "labels.json").write_text(json.dumps(labels, ensure_ascii=False))
    samples, loaded = likeness.load(tmp_path)
    assert len(loaded) == len(pairs)
    assert all((p.case, p.left) in samples and (p.case, p.right) in samples for p in loaded)
    sample = samples[("heart@a", "Q21")]
    assert sample.rotation_deg == 5.0 and sample.targets[0][0] == sample.targets[0][-1]


# ---- 變體表與吸附 ----


def test_variant_table() -> None:
    assert list(ls.VARIANTS) == ["Q2", "P2", "F"]
    assert ls.VARIANTS["Q2"].router == {} and ls.VARIANTS["Q2"].match == {}  # 現行預設：什麼都不覆寫
    assert ls.VARIANTS["P2"].router == {"turn_penalty_factor": None}
    assert ls.VARIANTS["P2"].match == {"prune": False}
    assert ls.VARIANTS["F"].router == {} and ls.VARIANTS["F"].match == {"quality": "fine"} and ls.VARIANTS["F"].needs_graph
    assert ("F", "Q2") in ls.ALWAYS_PAIRS  # 現行 vs 規劃器：不論差距


def test_fine_variant_needs_the_graph_and_passes_it(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict] = []

    def fake_match(router, strokes, **kwargs):
        calls.append(kwargs)
        raise RuntimeError("stop")

    monkeypatch.setattr(ls, "match_shape", fake_match)
    case = SearchCase("sq@x@R5", SQUARE, (121.5, 25.05), 800.0, 1.0, 3.0, (-15.0, 15.0))
    with pytest.raises(ValueError):  # 沒有圖就不跑：否則 match_shape 悄悄退回 fast，標成 fast 對 fast
        ls.match_variant(object(), case, placement_of(0), "F")
    graph = object()
    with pytest.raises(RuntimeError):
        ls.match_variant(object(), case, placement_of(0), "F", graph)
    assert calls[-1]["quality"] == "fine" and calls[-1]["graph"] is graph


def test_variants_build_routers_and_match_with_their_parameters(monkeypatch: pytest.MonkeyPatch) -> None:
    made: list[tuple[Path, dict]] = []
    monkeypatch.setattr(ls, "Router", SimpleNamespace(from_build=lambda build, **kw: made.append((build, kw)) or kw))
    routers = ls.variant_routers(Path("/b"), ["Q2", "P2"])
    assert made == [(Path("/b"), {}), (Path("/b"), {"turn_penalty_factor": None})]
    calls: list[dict] = []

    def fake_match(router, strokes, **kwargs):
        calls.append({"router": router, **kwargs})
        raise RuntimeError("stop")

    monkeypatch.setattr(ls, "match_shape", fake_match)
    case = SearchCase("sq@x@R5", SQUARE, (121.5, 25.05), 800.0, 1.0, 3.0, (-15.0, 15.0))
    for name in ("Q2", "P2"):
        with pytest.raises(RuntimeError):
            ls.match_variant(routers[name], case, placement_of(0), name)
    q2, p2 = calls
    assert "prune" not in q2 and q2["router"] == {}
    assert p2["prune"] is False and p2["router"] == {"turn_penalty_factor": None}
    for call in calls:
        assert call["search_radius_m"] is None
        assert call["start"].center == case.center and call["start"].radius_m == case.radius_m


@pytest.mark.parametrize("raw", ["Q2,X9", "", "Q2,,P2", "Q2,Q2"])
def test_unknown_or_bad_variant_list_is_a_usage_error(raw: str, tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as usage:
        ls.main(["samples", "--out", str(tmp_path), "--variants", raw])
    assert usage.value.code == 2


def test_parse_variants() -> None:
    assert ls.parse_variants("P2, Q2") == ["P2", "Q2"]


class FakeChecker:
    """安全斷言用的假 Router：每段都配到一條不是禁行的 way；單段路線沒有接點，不會呼叫 route。"""

    def __init__(self, denied_way: int = 1) -> None:
        self.way = denied_way

    def edges(self, points):
        return [EdgeInfo(self.way, 0.0, "road", 0.1)]


def fake_result(p: Placement) -> MatchResult:
    (target,) = stroke_targets(place(SQUARE, p))
    pts = [(lon + 0.00005, lat) for lon, lat in target]
    segments = [Segment("stroke", 0, pts, polyline_length_m(pts))]
    return MatchResult(pts, 1700.4, segments, Score(91, 82, 80, direction=60, exact=80.4567), 1600.2, [MatchWarning(0, "partial", "x")])


def test_sample_case_records_every_field(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    def fake_match(router, case, p, name):
        if name == "P2" and p.rotation_deg:
            raise MatchTimeout("x")
        return fake_result(p)

    monkeypatch.setattr(ls, "match_variant", fake_match)
    case = SearchCase("sq@x@R5", SQUARE, (121.5, 25.05), 800.0, 1.0, 3.0, (-15.0, 15.0))
    placements = [placement_of(0), placement_of(1)]
    e = ls.sample_case(case, placements, {"Q2": object(), "P2": object()}, FakeChecker(), frozenset({99}))
    assert {k: e[k] for k in ("case", "center", "radius_m")} == {"case": "sq@x@R5", "center": [121.5, 25.05], "radius_m": 800.0}
    assert e["shape"] == SHAPE
    assert [(r["variant"], r["rank"]) for r in e["routes"]] == [("Q2", 0), ("P2", 0), ("Q2", 1), ("P2", 1)]
    assert e["routes"][3] == {"variant": "P2", "rank": 1, "error": "MatchTimeout"}
    r = e["routes"][0]
    assert set(r) == {
        "variant", "rank", "placement", "score_v3", "rank_score", "score_v2", "route_m", "target_m", "ms",
        "warnings", "unsafe", "bad_turns", "steps_m", "segments",
    }
    assert r["score_v3"] == {"overall": 80, "coverage": 91, "cleanliness": 82, "direction": 60, "exact": 80.457}
    assert r["rank_score"] == pytest.approx(80.457)  # 旋轉 0°：不扣分
    assert e["routes"][2]["rank_score"] == pytest.approx(80.4567 - 5 * (5 / 15) ** 2, abs=1e-3)
    assert set(r["score_v2"]) == {"overall", "coverage", "cleanliness"} and 0 < r["score_v2"]["overall"] <= 100
    assert (r["route_m"], r["target_m"], r["warnings"], r["unsafe"], r["bad_turns"], r["steps_m"]) == (1700, 1600, ["partial"], None, 0, 0.0)
    assert isinstance(r["ms"], int) and r["ms"] >= 0
    assert r["segments"][0]["kind"] == "stroke" and r["segments"][0]["stroke"] == 0
    assert all(len(f"{v}".split(".")[-1]) <= 6 for pt in r["segments"][0]["points"] for v in pt)
    assert "" == capsys.readouterr().err


def test_sample_case_warns_about_unsafe_routes(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(ls, "match_variant", lambda router, case, p, name: fake_result(p))
    case = SearchCase("sq@x@R5", SQUARE, (121.5, 25.05), 800.0, 1.0, 3.0, (-15.0, 15.0))
    e = ls.sample_case(case, [placement_of(0)], {"Q2": object()}, FakeChecker(denied_way=7), frozenset({7}))
    assert e["routes"][0]["unsafe"].startswith("UnsafeRoute")
    assert not ls.is_safe(e["routes"][0])
    err = capsys.readouterr().err
    assert "sq@x@R5" in err and "Q20" in err and "不進配對" in err


def test_write_routes_is_gzip_json(tmp_path: Path) -> None:
    entries = two_cases()
    ls.write_routes(tmp_path / "routes.json.gz", entries)
    with gzip.open(tmp_path / "routes.json.gz", "rt") as f:
        assert json.load(f) == entries


# ---- 合成城市上跑一次（真的 Valhalla） ----


@pytest.mark.valhalla
def test_samples_cli_on_a_real_build(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    from engine.data.build import DENIED_FILE
    from engine.data.layout import DataLayout
    from engine.data.pipeline import run_build
    from engine.data.rideable import extract_rideable
    from engine.routing.tiles import build_tiles
    from osm_fixture import write_grid_pbf
    from search_city import CITY_CENTER, CITY_MAP

    pbf = tmp_path / "city.osm.pbf"
    write_grid_pbf(pbf, CITY_MAP)

    def builder(src: Path, build_dir: Path) -> None:
        extract_rideable(src, build_dir)
        build_tiles(src, build_dir)
        (build_dir / DENIED_FILE).write_text("[]")

    data = tmp_path / "data"
    run_build(DataLayout(data), pbf, "md5", "20261011T000000Z", builder, lambda d: [])
    case = SearchCase("square@city@R5", SQUARE, CITY_CENTER, 800.0, 2.0, 4.0, (-15.0, 15.0))
    monkeypatch.setattr(ls, "SEARCH_CASES", [case])
    out = tmp_path / "out"
    ls.main(["samples", "--out", str(out), "--data-dir", str(data), "--top", "2", "--min-gap", "0", "--variants", "Q2,P2"])
    with gzip.open(out / "routes.json.gz", "rt") as f:
        (e,) = json.load(f)
    assert e["case"] == "square@city@R5"
    ok = [r for r in e["routes"] if "error" not in r]
    assert ok and {r["variant"] for r in ok} == {"Q2", "P2"}
    assert all(ls.is_safe(r) for r in ok)
    pairs = json.loads((out / "pairs.json").read_text())
    assert pairs and all(p["id"].startswith("square@city|") for p in pairs)
    html = (out / "label.html").read_text()
    assert "__" not in html and f"共 {len(pairs)} 組" in html
    stdout = capsys.readouterr().out
    assert "square@city@R5" in stdout and "配對" in stdout


def test_likeness_kind_handles_single_letter_and_new_current_variants() -> None:
    from engine.bench.likeness import Pair, kind, split_key

    assert split_key("V02") == ("V0", 2) and split_key("Q21") == ("Q2", 1) and split_key("F0") == ("F", 0)
    assert kind(Pair(1, "c", "F0", "F2", "L")) == "不同擺放"  # 舊寫法 left[:2] 會把 F0、F2 當成不同變體
    assert kind(Pair(1, "c", "F0", "Q20", "L")) == "現行 vs F"
    assert kind(Pair(1, "c", "P21", "Q21", "R")) == "現行 vs P2"
    assert kind(Pair(1, "c", "V00", "V20", "same")) == "現行 vs V2"
    assert kind(Pair(1, "c", "V00", "V02", "L")) == "不同擺放"


def test_preferences_count_which_variant_the_user_picked() -> None:
    from engine.bench.likeness import Pair, format_preferences, preferences

    pairs = [
        Pair(1, "a", "F0", "Q20", "L"), Pair(2, "a", "Q21", "F1", "R"), Pair(3, "b", "F0", "Q20", "R"),
        Pair(4, "b", "Q22", "F2", "same"), Pair(5, "a", "P20", "Q20", "R"),
    ]
    prefs = preferences(pairs)
    assert prefs["現行 vs F"] == {"F": 2, "Q2": 1, "差不多": 1}
    assert prefs["現行 vs P2"] == {"Q2": 1}
    text = format_preferences(prefs)
    assert "F 2（67%）" in text and "Q2 1（33%）" in text and "差不多 1" in text
