import { describe, expect, it } from "vitest";
import {
  errorBodySchema,
  matchRequestSchema,
  matchResponseSchema,
  statusResponseSchema,
} from "@/lib/engine/contract";
import { SAMPLE_MATCH_RESPONSE } from "@/lib/engine/fixtures";

const stroke = (n: number) => ({
  points: Array.from({ length: n }, (_, i) => [121.5 + i * 1e-4, 25.03] as [number, number]),
  closed: false,
});

describe("matchRequestSchema", () => {
  it("接受合法請求，search_radius_m 可省略", () => {
    expect(matchRequestSchema.safeParse({ strokes: [stroke(2)] }).success).toBe(true);
    expect(matchRequestSchema.safeParse({ strokes: [stroke(2)], search_radius_m: 60 }).success).toBe(true);
  });

  it("筆數 1–40", () => {
    expect(matchRequestSchema.safeParse({ strokes: [] }).success).toBe(false);
    expect(matchRequestSchema.safeParse({ strokes: Array.from({ length: 41 }, () => stroke(2)) }).success).toBe(false);
    expect(matchRequestSchema.safeParse({ strokes: Array.from({ length: 40 }, () => stroke(2)) }).success).toBe(true);
  });

  it("每筆 2–2000 點", () => {
    expect(matchRequestSchema.safeParse({ strokes: [stroke(1)] }).success).toBe(false);
    expect(matchRequestSchema.safeParse({ strokes: [stroke(2001)] }).success).toBe(false);
    expect(matchRequestSchema.safeParse({ strokes: [stroke(2000)] }).success).toBe(true);
  });

  it("search_radius_m 30–150", () => {
    expect(matchRequestSchema.safeParse({ strokes: [stroke(2)], search_radius_m: 29 }).success).toBe(false);
    expect(matchRequestSchema.safeParse({ strokes: [stroke(2)], search_radius_m: 151 }).success).toBe(false);
  });

  it("座標必須是有限數字", () => {
    const bad = { strokes: [{ points: [[121.5, Number.NaN], [121.6, 25]], closed: false }] };
    expect(matchRequestSchema.safeParse(bad).success).toBe(false);
  });
});

describe("matchResponseSchema", () => {
  it("接受 spec 形狀的回應（stroke_index 可為 null）", () => {
    expect(matchResponseSchema.parse(SAMPLE_MATCH_RESPONSE)).toEqual(SAMPLE_MATCH_RESPONSE);
  });

  it("不認得的段落種類視為不合法", () => {
    const bad = {
      ...SAMPLE_MATCH_RESPONSE,
      segments: [{ ...SAMPLE_MATCH_RESPONSE.segments[0], kind: "straight" }],
    };
    expect(matchResponseSchema.safeParse(bad).success).toBe(false);
  });
});

describe("statusResponseSchema", () => {
  it("data 可以是 null", () => {
    expect(statusResponseSchema.parse({ data: null })).toEqual({ data: null });
  });
});

describe("errorBodySchema", () => {
  it("detail 是 { code, message, warnings? } 物件", () => {
    const detail = { code: "no_route", message: "全部部件都吸附失敗", warnings: SAMPLE_MATCH_RESPONSE.warnings };
    expect(errorBodySchema.parse({ detail })).toEqual({ detail });
    const bare = { code: "outside_taiwan", message: "圖形超出台灣" };
    expect(errorBodySchema.parse({ detail: bare })).toEqual({ detail: bare });
  });

  it("detail 可以是字串，或 pydantic 的錯誤陣列等任意值", () => {
    expect(errorBodySchema.parse({ detail: "壞了" })).toEqual({ detail: "壞了" });
    const pydantic = [{ loc: ["body"], msg: "x" }];
    expect(errorBodySchema.parse({ detail: pydantic })).toEqual({ detail: pydantic });
  });

  it("detail 可省略", () => {
    expect(errorBodySchema.parse({})).toEqual({});
  });
});
