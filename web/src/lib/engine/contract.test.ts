import { describe, expect, expectTypeOf, it } from "vitest";
import type { z } from "zod";
import {
  errorBodySchema,
  errorDetailSchema,
  matchRequestSchema,
  matchResponseSchema,
  statusResponseSchema,
} from "@/lib/engine/contract";
import { SAMPLE_MATCH_RESPONSE } from "@/lib/engine/fixtures";
import type { components } from "@/lib/engine/openapi";

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

describe("errorDetailSchema", () => {
  it("warnings 可以是 null（引擎的 OpenAPI 宣告 array | null）、省略或陣列", () => {
    const base = { code: "timeout", message: "吸附超過時間上限" };
    expect(errorDetailSchema.parse({ ...base, warnings: null })).toEqual({ ...base, warnings: null });
    expect(errorDetailSchema.parse(base)).toEqual(base);
    const warnings = SAMPLE_MATCH_RESPONSE.warnings;
    expect(errorDetailSchema.parse({ ...base, warnings })).toEqual({ ...base, warnings });
  });

  it("代理自己的錯誤碼也能解析（刻意比引擎的 code enum 寬）", () => {
    for (const code of ["too_large", "validation", "engine_unreachable"]) {
      expect(errorDetailSchema.safeParse({ code, message: "x" }).success).toBe(true);
    }
  });
});

// contract.ts 的 `satisfies` 擋住「web 解析結果不符合引擎型別」；這裡補另一個方向：
// 引擎的回應型別必須能被 zod 解析，引擎拿掉 web 需要的欄位時 tsc 會失敗（型別檢查，不是執行期斷言）。
describe("引擎回應型別 → zod 解析結果", () => {
  it("MatchResponse、StatusResponse 可指派", () => {
    expectTypeOf<components["schemas"]["MatchResponse"]>().toExtend<z.infer<typeof matchResponseSchema>>();
    expectTypeOf<components["schemas"]["StatusResponse"]>().toExtend<z.infer<typeof statusResponseSchema>>();
  });
});
