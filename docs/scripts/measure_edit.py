"""在引擎容器內量測線上引擎的 POST /edit（route editing spec §3、§6.3）。

用法（容器內）：uv run --no-sync python /tmp/measure_edit.py /tmp/edit-measure.json
- 輸入：engine.bench 的 25 個案例，用線上引擎 /match（fast）取得路線與簽章。
- 操作：heart@taipei_daan 剪右側 708 m 繞路（三個點擊位置）；每個案例各做一次拖曳（抓路線中點，拖到旁邊平行路上的點，
  另外多做一次「垂直偏移 100 m、不吸附」的拖曳，量 no_road 的比例）、重畫・最短、重畫・貼圖形（筆畫相同時）。
- 所有成功的輸出：bench 的安全斷言（matching.safety.unsafe_segments＋bad_turns＋樓梯 0 m）。
只讀線上引擎與 build，不改任何資料。
"""

import json
import math
import os
import statistics
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from engine.app import router_factory_from_env
from engine.bench.cases import CASES, place
from engine.bench.runner import bad_turns, use_meters
from engine.data.build import load_denied
from engine.data.layout import DataLayout
from engine.edit.position import pos_at_arc, resolve
from engine.matching.compose import flat
from engine.matching.safety import unsafe_segments
from engine.matching.turns import distance_to_polyline_m
from engine.matching.types import Segment
from engine.routing.geo import haversine_m, polyline_length_m
from engine.routing.router import RoutingError

BASE = "http://127.0.0.1:8080"
OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/edit-measure.json")


def post(path: str, body: dict) -> tuple[int, dict, int]:
    request = urllib.request.Request(
        BASE + path, data=json.dumps(body).encode(), headers={"content-type": "application/json"}
    )
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            status, raw = response.status, response.read()
    except urllib.error.HTTPError as error:
        status, raw = error.code, error.read()
    return status, json.loads(raw), round((time.perf_counter() - t0) * 1000)


def to_segments(payload: list[dict]) -> list[Segment]:
    return [Segment(s["kind"], s["stroke_index"], [tuple(p) for p in s["points"]], s["length_m"]) for s in payload]


build = DataLayout(Path("/data")).current()
denied = frozenset(d.id for d in load_denied(build))
router = router_factory_from_env(os.environ)(build)


def safety(response: dict) -> dict:
    segments = to_segments(response["segments"])
    bad = unsafe_segments(router, segments, denied)
    turns = bad_turns(router, segments)
    steps = use_meters(router, segments)["steps"]
    return {"ok": bad is None and turns == 0 and steps == 0, "bad_segments": bad, "bad_turns": turns, "steps_m": steps}


records: list[dict] = []


def run_edit(case: str, label: str, base: dict, strokes: list[dict], op: dict) -> dict:
    body = {
        "segments": base["segments"], "data_version": base["data_version"], "route_sig": base["route_sig"],
        "strokes": strokes, "op": op,
    }
    status, payload, ms = post("/edit", body)
    record: dict = {"case": case, "op": label, "status": status, "ms": ms}
    if status == 200:
        record["length_m"] = round(payload["route"]["length_m"])
        record["score"] = payload["score"]
        record["edit_segments"] = sum(s["kind"] == "edit" for s in payload["segments"])
        record["safety"] = safety(payload)
    else:
        detail = payload.get("detail", payload)
        record["code"] = detail.get("code") if isinstance(detail, dict) else "validation"
        record["message"] = detail.get("message") if isinstance(detail, dict) else str(detail)[:200]
    records.append(record)
    flag = "" if status != 200 else (" SAFE" if record["safety"]["ok"] else " UNSAFE!!")
    print(f"{case:34s} {label:16s} {status} {ms:5d} ms {record.get('code', '')}{flag}", flush=True)
    return record


def perpendicular_point(origin, heading_from, heading_to, meters: float, side: int):
    kx, ky = 111_320.0 * math.cos(math.radians(origin[1])), 110_574.0
    dx, dy = (heading_to[0] - heading_from[0]) * kx, (heading_to[1] - heading_from[1]) * ky
    norm = math.hypot(dx, dy) or 1.0
    nx, ny = -dy / norm * side, dx / norm * side
    return (origin[0] + nx * meters / kx, origin[1] + ny * meters / ky)


def parallel_road_point(segments: list[Segment], grab_point, a, b):
    """grab 點兩側 60/100/150 m 處，用 router.route 吸到最近的路；吸到的點離原路線 ≥ 30 m 才算「旁邊的平行路」。"""
    line = [p.point for p in flat(segments)]
    for dist in (60, 100, 150):
        for side in (1, -1):
            probe = perpendicular_point(grab_point, a, b, dist, side)
            scale = 30 / max(haversine_m(a, b), 1)  # 沿路線方向再走 30 m 當 route 的終點
            ahead = (probe[0] + (b[0] - a[0]) * scale, probe[1] + (b[1] - a[1]) * scale)
            try:
                snapped = router.route(probe, ahead).points[0]
            except RoutingError:
                continue
            if distance_to_polyline_m(snapped, line) >= 30:
                return snapped, dist
    return None, None


# ---- 1. 要編輯的基底路線 ----
bases: dict[str, tuple[dict, list[dict]]] = {}
for case in CASES:
    strokes = [{"points": s.points, "closed": s.closed} for s in place(case.shape, case.center, case.size_m, case.rotation_deg)]
    status, payload, ms = post("/match", {"strokes": strokes, "quality": "fast"})
    if status != 200:
        print(f"{case.name}: /match 失敗 {status} {payload}", flush=True)
        records.append({"case": case.name, "op": "match", "status": status, "ms": ms})
        continue
    bases[case.name] = (payload, strokes)
    print(f"{case.name:34s} match {ms} ms {payload['route']['length_m'] / 1000:.1f} km {len(payload['segments'])} 段", flush=True)

# ---- 2. heart@taipei_daan：剪右側 708 m 繞路 ----
heart = bases.get("heart@taipei_daan")
if heart:
    base, strokes = heart
    detours = [(i, s) for i, s in enumerate(base["segments"]) if s["kind"] == "detour"]
    print("heart@taipei_daan detour 段：", [(i, round(polyline_length_m(s["points"]))) for i, s in detours], flush=True)
    target = min(detours, key=lambda x: abs(polyline_length_m(x[1]["points"]) - 708), default=None)
    if target:
        index, seg = target
        length = polyline_length_m(seg["points"])
        for frac in (0.25, 0.5, 0.75):
            run_edit(
                "heart@taipei_daan", f"cut@{frac}", base, strokes,
                {"type": "cut", "at": {"segment": index, "offset_m": length * frac}},
            )
            records[-1]["detour_m"] = round(length)
    else:
        print("找不到 detour 段", flush=True)

# ---- 3. 每個案例：拖曳、重畫・最短、重畫・貼圖形 ----
for name, (base, strokes) in bases.items():
    segments = to_segments(base["segments"])
    total = sum(polyline_length_m(s.points) for s in segments)
    mid = pos_at_arc(segments, total / 2)
    grab_point = resolve(segments, mid).point
    near = resolve(segments, pos_at_arc(segments, total / 2 + 15)).point
    far = resolve(segments, pos_at_arc(segments, max(total / 2 - 15, 0))).point
    to, dist = parallel_road_point(segments, grab_point, far, near)
    if to is None:
        print(f"{name:34s} drag 找不到旁邊的平行路（略過這一項）", flush=True)
        records.append({"case": name, "op": "drag", "status": None, "code": "no_parallel_found"})
    else:
        run_edit(name, "drag@road", base, strokes,
                 {"type": "drag", "grab": {"segment": mid.segment, "offset_m": mid.offset_m}, "to": list(to)})
        records[-1]["parallel_dist_m"] = dist
    raw = perpendicular_point(grab_point, far, near, 100, 1)
    run_edit(name, "drag@raw100m", base, strokes,
             {"type": "drag", "grab": {"segment": mid.segment, "offset_m": mid.offset_m}, "to": list(raw)})

    span = min(200.0, total / 4)
    a, b = pos_at_arc(segments, total / 2 - span), pos_at_arc(segments, total / 2 + span)
    for mode, label in (("shortest", "reroute-short"), ("shape", "reroute-shape")):
        if mode == "shape":
            ia, ib = segments[a.segment].stroke_index, segments[b.segment].stroke_index
            if ia is None or ia != ib:
                print(f"{name:34s} {label:16s} 兩點不在同一筆畫，略過", flush=True)
                records.append({"case": name, "op": label, "status": None, "code": "skipped_cross_stroke"})
                continue
        run_edit(name, label, base, strokes, {
            "type": "reroute", "from": {"segment": a.segment, "offset_m": a.offset_m},
            "to": {"segment": b.segment, "offset_m": b.offset_m}, "mode": mode,
        })

OUT.write_text(json.dumps(records, ensure_ascii=False, indent=1))

# ---- 摘要 ----
def summarize(label_prefix: str) -> None:
    rows = [r for r in records if r["op"].startswith(label_prefix) and r.get("status") is not None]
    if not rows:
        return
    ms = sorted(r["ms"] for r in rows)
    ok = [r for r in rows if r["status"] == 200]
    codes: dict[str, int] = {}
    for r in rows:
        if r["status"] != 200:
            codes[r.get("code", str(r["status"]))] = codes.get(r.get("code", str(r["status"])), 0) + 1
    print(f"{label_prefix:14s} n={len(rows)} 成功={len(ok)} 中位數={statistics.median(ms)} ms 最慢={ms[-1]} ms 錯誤={codes}")


print("\n=== 摘要 ===")
for prefix in ("cut", "drag@road", "drag@raw100m", "reroute-short", "reroute-shape"):
    summarize(prefix)
unsafe = [r for r in records if r.get("safety") and not r["safety"]["ok"]]
print(f"安全斷言失敗：{len(unsafe)}", [(r['case'], r['op'], r['safety']) for r in unsafe])
