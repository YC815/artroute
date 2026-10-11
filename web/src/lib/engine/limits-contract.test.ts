import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { matchRequestSchema, searchRequestSchema } from "@/lib/engine/contract";
import {
  MAX_NORM_COORD,
  MAX_SEARCH_AREA_RADIUS_M,
  MAX_SEARCH_CANDIDATES,
  MAX_SEARCH_KM,
  MAX_SEARCH_RADIUS_M,
  MAX_SEARCH_ROTATION_DEG,
  MAX_STROKE_POINTS,
  MAX_STROKES,
  MIN_SEARCH_AREA_RADIUS_M,
  MIN_SEARCH_KM,
  MIN_SEARCH_RADIUS_M,
  MIN_STROKE_POINTS,
} from "@/lib/limits";

// openapi.d.ts 不帶 minimum／maximum／minItems／maxItems，所以限制要直接讀引擎的 openapi.json 比對。
type Bounds = { minItems?: number; maxItems?: number; minimum?: number; maximum?: number };
type Schemas = {
  MatchRequest: { properties: { strokes: Bounds; search_radius_m: { anyOf: (Bounds & { type: string })[] } } };
  StrokeIn: { properties: { points: Bounds } };
  StartCircle: { properties: { radius_m: Bounds } };
  SearchRequest: {
    properties: Record<"strokes" | "radius_m" | "min_km" | "max_km" | "rotation_min_deg" | "rotation_max_deg", Bounds>;
  };
  NormStrokeIn: { properties: { points: Bounds & { items: { prefixItems: Bounds[] } } } };
  SearchResult: { properties: { candidates: Bounds } };
};

const path = fileURLToPath(new URL("../../../../engine/openapi.json", import.meta.url));
const schemas = (JSON.parse(readFileSync(path, "utf8")) as { components: { schemas: Schemas } }).components.schemas;
const strokes = schemas.MatchRequest.properties.strokes;
const points = schemas.StrokeIn.properties.points;
const radius = schemas.MatchRequest.properties.search_radius_m.anyOf.find((branch) => branch.type === "number");
const search = schemas.SearchRequest.properties;
const normPoints = schemas.NormStrokeIn.properties.points;

const stroke = { points: [[121.5, 25.03], [121.51, 25.03]] as [number, number][], closed: false };
const normStroke = { points: [[-0.5, 0], [0.5, 0]] as [number, number][], closed: false };
const SEARCH = {
  strokes: [normStroke],
  center: [121.5654, 25.033] as [number, number],
  radius_m: 3000,
  min_km: 10,
  max_km: 20,
  rotation_min_deg: -15,
  rotation_max_deg: 15,
};

describe("limits.ts 與 engine/openapi.json 的限制一致", () => {
  it("筆數上限", () => {
    expect(strokes.maxItems).toBe(MAX_STROKES);
  });

  it("筆數下限：引擎的 minItems 剛好是 matchRequestSchema 接受的最少筆數", () => {
    const min = strokes.minItems as number;
    expect(matchRequestSchema.safeParse({ strokes: Array.from({ length: min }, () => stroke) }).success).toBe(true);
    expect(matchRequestSchema.safeParse({ strokes: Array.from({ length: min - 1 }, () => stroke) }).success).toBe(false);
  });

  it("每筆點數 2–2000", () => {
    expect(points.minItems).toBe(MIN_STROKE_POINTS);
    expect(points.maxItems).toBe(MAX_STROKE_POINTS);
  });

  it("search_radius_m 30–150", () => {
    expect(radius?.minimum).toBe(MIN_SEARCH_RADIUS_M);
    expect(radius?.maximum).toBe(MAX_SEARCH_RADIUS_M);
  });

  it("start_circle 與 /search 的圓半徑 300–10000 m", () => {
    for (const bounds of [schemas.StartCircle.properties.radius_m, search.radius_m]) {
      expect(bounds.minimum).toBe(MIN_SEARCH_AREA_RADIUS_M);
      expect(bounds.maximum).toBe(MAX_SEARCH_AREA_RADIUS_M);
    }
  });

  it("/search 的公里數 1–300、旋轉 ±180°", () => {
    for (const bounds of [search.min_km, search.max_km]) {
      expect(bounds.minimum).toBe(MIN_SEARCH_KM);
      expect(bounds.maximum).toBe(MAX_SEARCH_KM);
    }
    for (const bounds of [search.rotation_min_deg, search.rotation_max_deg]) {
      expect(bounds.minimum).toBe(-MAX_SEARCH_ROTATION_DEG);
      expect(bounds.maximum).toBe(MAX_SEARCH_ROTATION_DEG);
    }
  });

  it("/search 的筆畫：1–40 筆、每筆 2–2000 點、座標在 [-1, 1]", () => {
    expect(search.strokes.minItems).toBe(1);
    expect(search.strokes.maxItems).toBe(MAX_STROKES);
    expect(normPoints.minItems).toBe(MIN_STROKE_POINTS);
    expect(normPoints.maxItems).toBe(MAX_STROKE_POINTS);
    for (const coord of normPoints.items.prefixItems) {
      expect(coord.minimum).toBe(-MAX_NORM_COORD);
      expect(coord.maximum).toBe(MAX_NORM_COORD);
    }
  });

  it("/search 的結果最多 5 個候選", () => {
    expect(schemas.SearchResult.properties.candidates.maxItems).toBe(MAX_SEARCH_CANDIDATES);
  });
});

describe("searchRequestSchema 的邊界與引擎相同", () => {
  const ok = (patch: object) => searchRequestSchema.safeParse({ ...SEARCH, ...patch }).success;

  it("圓半徑：上下限本身合法，超出就不合法", () => {
    expect(ok({ radius_m: MIN_SEARCH_AREA_RADIUS_M })).toBe(true);
    expect(ok({ radius_m: MAX_SEARCH_AREA_RADIUS_M })).toBe(true);
    expect(ok({ radius_m: MIN_SEARCH_AREA_RADIUS_M - 1 })).toBe(false);
    expect(ok({ radius_m: MAX_SEARCH_AREA_RADIUS_M + 1 })).toBe(false);
  });

  it("公里數：1–300，而且最小不能大於最大（相等可以）", () => {
    expect(ok({ min_km: MIN_SEARCH_KM, max_km: MAX_SEARCH_KM })).toBe(true);
    expect(ok({ min_km: 5, max_km: 5 })).toBe(true);
    expect(ok({ min_km: MIN_SEARCH_KM - 0.1 })).toBe(false);
    expect(ok({ max_km: MAX_SEARCH_KM + 0.1 })).toBe(false);
    expect(ok({ min_km: 20, max_km: 10 })).toBe(false);
  });

  it("旋轉：±180°，而且最小不能大於最大", () => {
    expect(ok({ rotation_min_deg: -180, rotation_max_deg: 180 })).toBe(true);
    expect(ok({ rotation_min_deg: -181 })).toBe(false);
    expect(ok({ rotation_max_deg: 181 })).toBe(false);
    expect(ok({ rotation_min_deg: 10, rotation_max_deg: -10 })).toBe(false);
  });

  it("正規化座標超出 [-1, 1] 不合法", () => {
    expect(ok({ strokes: [{ points: [[-1, 1], [1, -1]], closed: false }] })).toBe(true);
    expect(ok({ strokes: [{ points: [[0, 0], [1.01, 0]], closed: false }] })).toBe(false);
  });

  it("筆畫 1–40 筆", () => {
    expect(ok({ strokes: [] })).toBe(false);
    expect(ok({ strokes: Array.from({ length: MAX_STROKES }, () => normStroke) })).toBe(true);
    expect(ok({ strokes: Array.from({ length: MAX_STROKES + 1 }, () => normStroke) })).toBe(false);
  });
});
