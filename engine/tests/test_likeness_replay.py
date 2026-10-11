"""likeness_replay：重新吸附校正樣本的某個變體，比對路線是否逐點相同。"""

import gzip
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import engine.bench.likeness_replay as replay_module
from engine.bench.likeness import DATA_DIR, _route
from engine.bench.likeness_replay import max_gap_m, replay
from engine.data.layout import DataLayout


@pytest.fixture(scope="module")
def cases() -> list[dict]:
    with gzip.open(DATA_DIR / "routes.json.gz", "rt") as f:
        return json.load(f)


def test_identical_routes_are_reported_same_and_others_with_the_gap(
    cases: list[dict], monkeypatch: pytest.MonkeyPatch
) -> None:
    stored = {(c["case"], r["rank"]): _route(r["segments"]) for c in cases for r in c["routes"] if r["variant"] == "V2"}
    calls: list[dict] = []

    def fake_match(router, strokes, *, search_radius_m, deadline, start, **kwargs):
        calls.append(kwargs)
        key = next(k for k, v in stored.items() if k[0] == fake_match.case and k[1] == fake_match.rank)
        points = stored[key]
        if fake_match.rank == 1:  # 第 2 名：整條往東移約 10 m
            points = [(lon + 0.0001, lat) for lon, lat in points]
        return SimpleNamespace(route_points=points)

    monkeypatch.setattr(replay_module, "match_shape", fake_match)
    case = cases[0]

    real_place = replay_module.place

    def tracking_place(strokes, placement):
        r = next(r for r in case["routes"] if r["variant"] == "V2" and r["placement"]["size_m"] == placement.size_m)
        fake_match.case, fake_match.rank = case["case"], r["rank"]
        return real_place(strokes, placement)

    monkeypatch.setattr(replay_module, "place", tracking_place)
    results = replay([case], "V2", object(), {"prune": False})
    assert [(r.rank, r.same) for r in results] == [(0, True), (1, False)]
    assert results[0].max_gap_m == 0.0
    assert 9.0 < results[1].max_gap_m < 11.0
    assert calls == [{"prune": False}, {"prune": False}]


def test_failed_rematch_is_recorded_not_raised(cases: list[dict], monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*args, **kwargs):
        raise RuntimeError("x")

    monkeypatch.setattr(replay_module, "match_shape", boom)
    results = replay(cases[:1], "V2", object(), {})
    assert results and all(not r.same and r.max_gap_m is None and r.error == "RuntimeError" for r in results)


def test_max_gap_is_symmetric_and_zero_for_same_route() -> None:
    a = [(121.5, 25.0), (121.501, 25.0)]
    b = [(121.5, 25.0), (121.501, 25.0), (121.501, 25.0002)]  # 多一段約 22 m 的尾巴
    assert max_gap_m(a, a) == 0.0
    assert max_gap_m(a, b) == pytest.approx(max_gap_m(b, a)) == pytest.approx(22.1, abs=0.5)


def test_cli_passes_router_and_match_options(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    layout = DataLayout(tmp_path / "data")
    layout.activate(layout.new_build_dir("b1"))
    seen: dict = {}

    def make_router(build, **kwargs):
        seen["router"] = kwargs
        return object()

    def fake_replay(cases, variant, router, options):
        seen["variant"], seen["options"] = variant, options
        return []

    monkeypatch.setattr(replay_module, "replay", fake_replay)
    replay_module.main(
        ["--samples", str(DATA_DIR), "--variant", "V2", "--data-dir", str(tmp_path / "data"),
         "--turn-penalty-factor", "500", "--no-prune"],
        make_router=make_router,
    )
    assert seen == {"router": {"turn_penalty_factor": 500.0, "sigma_z": None}, "variant": "V2", "options": {"prune": False}}
    assert "共 0 條" in capsys.readouterr().out
