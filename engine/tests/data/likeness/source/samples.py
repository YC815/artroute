"""P2.5 校正樣本：12 個搜尋案例的前 5 名（V0 = 現行），前 2 名的擺放再用 A 參數（V1、V2）重新吸附。"""
import json, sys, time
from pathlib import Path
import engine.routing.router as R
from engine.bench.runner import bad_turns, safety_problem, use_meters
from engine.bench.search_cases import SEARCH_CASES
from engine.data.build import load_denied
from engine.data.layout import DataLayout
from engine.matching.deadline import Deadline
from engine.matching.service import match_shape
from engine.matching.types import StartHint
from engine.search.geometry import place
from engine.search.roads import RoadIndex
from engine.search.service import run_search
from engine.search.types import SearchParams

VARIANTS = {"V1": {"gps": 1 / 3}, "V2": {"gps": 1 / 2, "tpf": 500}}
VAR: dict = {}
_orig = R.Router._call
def _patched(self, method, request):
    if method == "trace_route" and VAR:
        opts = dict(request["trace_options"])
        opts["gps_accuracy"] = opts["search_radius"] * VAR["gps"]
        if "tpf" in VAR:
            opts["turn_penalty_factor"] = VAR["tpf"]
        request = {**request, "trace_options": opts}
    return _orig(self, method, request)
R.Router._call = _patched

build = DataLayout(Path("/data")).current()
roads = RoadIndex.load(build)
denied = frozenset(d.id for d in load_denied(build))
checker = R.Router.from_build(build)
r6 = lambda p: [round(p[0], 6), round(p[1], 6)]

def route_entry(variant, rank, placement, result, ms):
    unsafe = safety_problem(checker, result.segments, denied)
    return {
        "variant": variant, "rank": rank,
        "placement": {"center": list(placement.center), "size_m": round(placement.size_m, 1), "rotation_deg": round(placement.rotation_deg, 1)},
        "score_v2": {k: getattr(result.score, k) for k in ("overall", "coverage", "cleanliness")},
        "route_m": round(result.route_length_m), "target_m": round(result.target_length_m), "ms": ms,
        "warnings": [w.code for w in result.warnings],
        "unsafe": None if unsafe is None else f"{unsafe[0]}：{unsafe[1]}",
        "bad_turns": bad_turns(checker, result.segments),
        "steps_m": use_meters(checker, result.segments).get("steps", 0.0),
        "segments": [{"kind": s.kind, "stroke": s.stroke_index, "points": [r6(p) for p in s.points]} for s in result.segments],
    }

out = []
for case in SEARCH_CASES:
    VAR.clear()
    params = SearchParams(case.strokes, case.center, case.radius_m, case.min_km, case.max_km, case.rotation[0], case.rotation[1])
    outcome = run_search(params, [R.Router.from_build(build) for _ in range(2)], roads, Deadline(60), lambda e: None)
    entry = {"case": case.name, "center": list(case.center), "radius_m": case.radius_m,
             "shape": [{"points": [list(p) for p in s.points], "closed": s.closed} for s in case.strokes], "routes": []}
    for rank, c in enumerate(outcome.candidates):
        entry["routes"].append(route_entry("V0", rank, c.placement, c.result, None))
    router = R.Router.from_build(build)
    for rank, c in enumerate(outcome.candidates[:2]):
        strokes = place(case.strokes, c.placement)
        for name, var in VARIANTS.items():
            VAR.clear(); VAR.update(var)
            t = time.monotonic()
            try:
                res = match_shape(router, strokes, search_radius_m=None, deadline=Deadline(10), start=StartHint(case.center, case.radius_m))
                entry["routes"].append(route_entry(name, rank, c.placement, res, round((time.monotonic() - t) * 1000)))
            except Exception as e:  # noqa: BLE001
                entry["routes"].append({"variant": name, "rank": rank, "error": type(e).__name__})
    VAR.clear()
    out.append(entry)
    print(case.name, len(entry["routes"]), file=sys.stderr, flush=True)
json.dump(out, sys.stdout)
