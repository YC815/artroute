import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { matchRequestSchema } from "@/lib/engine/contract";
import {
  MAX_SEARCH_RADIUS_M,
  MAX_STROKE_POINTS,
  MAX_STROKES,
  MIN_SEARCH_RADIUS_M,
  MIN_STROKE_POINTS,
} from "@/lib/limits";

// openapi.d.ts 不帶 minimum／maximum／minItems／maxItems，所以限制要直接讀引擎的 openapi.json 比對。
type Bounds = { minItems?: number; maxItems?: number; minimum?: number; maximum?: number };
type Schemas = {
  MatchRequest: { properties: { strokes: Bounds; search_radius_m: { anyOf: (Bounds & { type: string })[] } } };
  StrokeIn: { properties: { points: Bounds } };
};

const path = fileURLToPath(new URL("../../../../engine/openapi.json", import.meta.url));
const schemas = (JSON.parse(readFileSync(path, "utf8")) as { components: { schemas: Schemas } }).components.schemas;
const strokes = schemas.MatchRequest.properties.strokes;
const points = schemas.StrokeIn.properties.points;
const radius = schemas.MatchRequest.properties.search_radius_m.anyOf.find((branch) => branch.type === "number");

const stroke = { points: [[121.5, 25.03], [121.51, 25.03]] as [number, number][], closed: false };

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
});
