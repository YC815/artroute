"""在引擎容器內量測線上引擎的 POST /verify（route editing spec §3.6、§6.3）。

用法（容器內）：uv run --no-sync python /tmp/measure_verify.py /tmp/verify-measure.json OLD_BUILD_ID
- 直接開舊 build 目錄的 Router（不切換 /data/current、不動任何 build），用 match_shape 產生路線，
  以 /data/edit-key 與 sign_route（data_version = 舊 build）簽章，再 POST 到線上引擎的 /verify。
- 兩個 build 的 osm_md5 相同時，舊路網的路線在新路網上都應該通過：任何 verify_failed 都是誤報候選，
  另用 inspect_segments 在新路網上列出原因（缺口／禁行／逆向／判斷不了）。
- 最後量排隊：/verify 進行中同時打 /match，看 /match 的延遲與是否 504（共用 Semaphore(1)，每個 worker 各一個）。
"""

import json
import os
import statistics
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from engine.app import router_factory_from_env
from engine.bench.cases import CASES, LOCATIONS, SHAPES, place
from engine.data.build import load_denied
from engine.data.layout import DataLayout
from engine.edit.signature import load_or_create_key, sign_route
from engine.matching.deadline import Deadline
from engine.matching.safety import inspect_segments
from engine.matching.service import match_shape
from engine.routing.geo import polyline_length_m

BASE = "http://127.0.0.1:8080"
OUT = Path(sys.argv[1])
OLD = sys.argv[2]


def post(path: str, body: dict, timeout: float = 90) -> tuple[int, dict, int]:
    request = urllib.request.Request(
        BASE + path, data=json.dumps(body).encode(), headers={"content-type": "application/json"}
    )
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status, raw = response.status, response.read()
    except urllib.error.HTTPError as error:
        status, raw = error.code, error.read()
    return status, json.loads(raw), round((time.perf_counter() - t0) * 1000)


root = Path("/data")
layout = DataLayout(root)
new_build = layout.current()
old_build = layout.builds_dir / OLD
assert old_build.is_dir() and old_build != new_build, (old_build, new_build)
key = load_or_create_key(root / "edit-key")
factory = router_factory_from_env(os.environ)
old_router, new_router = factory(old_build), factory(new_build)
denied_new = frozenset(d.id for d in load_denied(new_build))
print(f"舊 build {old_build.name}，現行 {new_build.name}", flush=True)


def seg_payload(segments) -> list[dict]:
    return [
        {"kind": s.kind, "stroke_index": s.stroke_index, "points": [list(p) for p in s.points], "length_m": s.length_m}
        for s in segments
    ]


cases = [(c.name, place(c.shape, c.center, c.size_m, c.rotation_deg)) for c in CASES]
for shape_name, place_name, size in (
    ("hi", "taichung_west", 6000.0), ("square", "taipei_riverside", 6000.0), ("heart", "taipei_riverside", 10000.0),
    ("star", "taipei_daan", 8000.0), ("square", "kaohsiung_lingya", 12000.0),
):
    shape = next(s for s in SHAPES if s.name == shape_name)
    cases.append((f"{shape_name}@{place_name}@{size / 1000:g}km", place(shape, LOCATIONS[place_name], size, 0.0)))

records, routes = [], {}
for name, strokes in cases:
    t0 = time.perf_counter()
    try:
        result = match_shape(old_router, strokes, search_radius_m=None, deadline=Deadline(120.0))
    except Exception as error:  # noqa: BLE001 - 量測腳本：記錄任何失敗後繼續
        print(f"{name:36s} 舊路網產生路線失敗：{type(error).__name__}: {error}", flush=True)
        records.append({"case": name, "op": "make", "error": f"{type(error).__name__}: {error}"})
        continue
    segments = result.segments
    sig = sign_route(key, OLD, segments)
    body = {
        "segments": seg_payload(segments), "data_version": OLD, "route_sig": sig,
        "strokes": [{"points": [list(p) for p in s.points], "closed": s.closed} for s in strokes],
    }
    longest_edge_pts = max(len(s.points) for s in segments)
    status, payload, ms = post("/verify", body)
    record = {
        "case": name, "op": "verify", "status": status, "ms": ms, "route_km": round(result.route_length_m / 1000, 1),
        "segments": len(segments), "max_segment_points": longest_edge_pts,
        "make_s": round(time.perf_counter() - t0, 1),
    }
    if status == 200:
        record["new_version"] = payload["data_version"]
    else:
        detail = payload.get("detail", payload)
        record["code"] = detail.get("code") if isinstance(detail, dict) else "validation"
        record["problem_segments"] = detail.get("segments") if isinstance(detail, dict) else None
        if record["code"] == "verify_failed":
            report = inspect_segments(new_router, segments, denied_new)
            record["reasons"] = {
                "gaps": report.gaps, "undetermined": report.undetermined,
                "used": {i: sorted(v) for i, v in report.used.items()},
                "wrong": {i: sorted(v) for i, v in report.wrong.items()}, "unknown": sorted(report.unknown),
            }
    records.append(record)
    routes[name] = body
    print(f"{name:36s} {record['route_km']:5.1f} km {len(segments):3d} 段 /verify {status} {ms:6d} ms {record.get('code', '')} {record.get('problem_segments') or ''}", flush=True)

# ---- 排隊：/verify 進行中打 /match ----
ok = [r for r in records if r.get("op") == "verify" and r.get("status") in (200, 422, 504)]
slow = max(ok, key=lambda r: r["ms"]) if ok else None
queue: dict = {"verify_case": slow and slow["case"], "verify_ms_alone": slow and slow["ms"]}
if slow:
    small = next(c for c in CASES if c.name == "letter_l@kaohsiung_lingya")
    match_body = {
        "strokes": [{"points": s.points, "closed": s.closed} for s in place(small.shape, small.center, small.size_m, 0.0)],
        "quality": "fast",
    }
    alone = [post("/match", match_body)[2] for _ in range(8)]
    queue["match_alone_ms"] = alone
    trials = []
    for _ in range(3):
        outcome: dict = {}

        def run_verify() -> None:
            outcome["verify"] = post("/verify", routes[slow["case"]])[::2]

        thread = threading.Thread(target=run_verify)
        thread.start()
        time.sleep(0.2)
        results: list = []

        def run_match() -> None:
            status, _, ms = post("/match", match_body)
            results.append((status, ms))

        matchers = [threading.Thread(target=run_match) for _ in range(6)]
        for m in matchers:
            m.start()
        for m in matchers:
            m.join()
        thread.join()
        trials.append({"verify": outcome["verify"], "match": sorted(results, key=lambda x: x[1])})
        print("排隊試驗：verify", outcome["verify"], "match", sorted(results, key=lambda x: x[1]), flush=True)
    queue["trials"] = trials

OUT.write_text(json.dumps({"records": records, "queue": queue}, ensure_ascii=False, indent=1))
rows = [r for r in records if r.get("op") == "verify"]
ms = sorted(r["ms"] for r in rows)
print(f"\n/verify n={len(rows)} 200={sum(r['status'] == 200 for r in rows)} 422={sum(r['status'] == 422 for r in rows)} "
      f"504={sum(r['status'] == 504 for r in rows)} 中位數={statistics.median(ms)} ms 最慢={ms[-1]} ms")
