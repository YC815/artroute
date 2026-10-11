"""在 samples.json 的前 2 名擺放上，用改過 meili sigma_z 的 Router 重新吸附（V3、V4）。stdin = samples.json。"""
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
from engine.search.types import Placement

VARIANTS = {"V3": (15.0, 500), "V4": (30.0, 500)}
TPF: list = []
_orig = R.Router._call
def _patched(self, method, request):
    if method == "trace_route" and TPF:
        request = {**request, "trace_options": {**request["trace_options"], "turn_penalty_factor": TPF[0]}}
    return _orig(self, method, request)
R.Router._call = _patched

build = DataLayout(Path("/data")).current()
denied = frozenset(d.id for d in load_denied(build))
checker = R.Router.from_build(build)
cases = {c.name: c for c in SEARCH_CASES}
samples = json.load(sys.stdin)
routers = {}
for name, (sigma, tpf) in VARIANTS.items():
    cfg = R.load_config(build); cfg["meili"]["default"]["sigma_z"] = sigma
    routers[name] = R.Router(cfg)
r6 = lambda p: [round(p[0], 6), round(p[1], 6)]
out = {}
for entry in samples:
    case = cases[entry["case"]]; rows = []
    for v0 in [r for r in entry["routes"] if r["variant"] == "V0" and r["rank"] < 2]:
        p = v0["placement"]; placement = Placement(tuple(p["center"]), p["size_m"], p["rotation_deg"])
        for name, (sigma, tpf) in VARIANTS.items():
            TPF[:] = [tpf]; t = time.monotonic()
            try:
                res = match_shape(routers[name], place(case.strokes, placement), search_radius_m=None, deadline=Deadline(10), start=StartHint(case.center, case.radius_m))
            except Exception as e:  # noqa: BLE001
                rows.append({"variant": name, "rank": v0["rank"], "error": type(e).__name__}); continue
            unsafe = safety_problem(checker, res.segments, denied)
            rows.append({"variant": name, "rank": v0["rank"], "placement": p,
                "score_v2": {k: getattr(res.score, k) for k in ("overall", "coverage", "cleanliness")},
                "route_m": round(res.route_length_m), "target_m": round(res.target_length_m), "ms": round((time.monotonic() - t) * 1000),
                "warnings": [w.code for w in res.warnings], "unsafe": None if unsafe is None else f"{unsafe[0]}：{unsafe[1]}",
                "bad_turns": bad_turns(checker, res.segments), "steps_m": use_meters(checker, res.segments).get("steps", 0.0),
                "segments": [{"kind": s.kind, "stroke": s.stroke_index, "points": [r6(q) for q in s.points]} for s in res.segments]})
    TPF.clear()
    out[entry["case"]] = rows
    print(entry["case"], file=sys.stderr, flush=True)
json.dump(out, sys.stdout)
