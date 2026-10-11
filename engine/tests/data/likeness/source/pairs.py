import json, math, random, sys
d = json.load(open("all.json"))
BOX, REF = 240.0, 110.0

def fit(paths, box, pad=10.0):
    xs = [x for p in paths for x, _ in p]; ys = [y for p in paths for _, y in p]
    w, h = max(xs) - min(xs) or 1e-9, max(ys) - min(ys) or 1e-9
    s = (box - 2 * pad) / max(w, h)
    ox = (box - w * s) / 2 - min(xs) * s; oy = (box - h * s) / 2 + max(ys) * s
    out = []
    for p in paths:
        pts, last = [], None
        for x, y in p:
            q = (round(x * s + ox, 1), round(oy - y * s, 1))
            if q != last: pts.append(q); last = q
        out.append("M" + "L".join(f"{a:g},{b:g}" for a, b in pts))
    return "".join(out)

def route_svg(r):
    lat0 = r["placement"]["center"][1]; k = math.cos(math.radians(lat0))
    return fit([[(lon * k, lat) for lon, lat in s["points"]] for s in r["segments"]], BOX)

def shape_svg(shape):
    return fit([[tuple(p) for p in s["points"]] + ([tuple(s["points"][0])] if s["closed"] else []) for s in shape], REF, 6)

rng = random.Random(7)
pairs = []
for c in d:
    by = {(r["variant"], r["rank"]): r for r in c["routes"] if "error" not in r}
    for a, b in [(("V0", 0), ("V2", 0)), (("V0", 0), ("V3", 0)), (("V0", 0), ("V0", 2))]:
        L, R = by[a], by[b]
        if rng.random() < 0.5: L, R, a, b = R, L, b, a
        pairs.append({"id": f'{c["case"].split("@R5")[0]}|{a[0]}{a[1]}|{b[0]}{b[1]}', "ref": shape_svg(c["shape"]), "l": route_svg(L), "r": route_svg(R)})
rng.shuffle(pairs)
for i, p in enumerate(pairs): p["n"] = i + 1
json.dump(pairs, open("pairs.json", "w"), separators=(",", ":"))
print(len(pairs), sum(len(p["l"]) + len(p["r"]) for p in pairs))
