import { describe, expect, expectTypeOf, it } from "vitest";
import type { z } from "zod";
import {
  errorBodySchema,
  errorDetailSchema,
  matchRequestSchema,
  matchResponseSchema,
  searchEventSchema,
  searchFailedEventSchema,
  searchRequestSchema,
  statusResponseSchema,
} from "@/lib/engine/contract";
import { SAMPLE_MATCH_RESPONSE, sampleCandidate, sampleResult } from "@/lib/engine/fixtures";
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

  it("start_circle 可省略、可為 null；半徑 300–10000 m", () => {
    const circle = (radius_m: number) => ({ strokes: [stroke(2)], start_circle: { center: [121.5, 25.03], radius_m } });
    expect(matchRequestSchema.safeParse({ strokes: [stroke(2)], start_circle: null }).success).toBe(true);
    expect(matchRequestSchema.safeParse(circle(3000)).success).toBe(true);
    expect(matchRequestSchema.safeParse(circle(299)).success).toBe(false);
    expect(matchRequestSchema.safeParse(circle(10_001)).success).toBe(false);
  });

  it("start_circle 會被保留（代理只轉發 schema 內的欄位）", () => {
    const request = { strokes: [stroke(2)], start_circle: { center: [121.5, 25.03], radius_m: 3000 } };
    expect(matchRequestSchema.parse(request)).toEqual(request);
  });
});

describe("searchRequestSchema", () => {
  const request = {
    strokes: [{ points: [[-0.5, 0], [0.5, 0.25]], closed: true }],
    center: [121.5654, 25.033],
    radius_m: 3000,
    min_km: 10,
    max_km: 25,
    rotation_min_deg: -15,
    rotation_max_deg: 15,
  };

  it("接受合法請求", () => {
    expect(searchRequestSchema.parse(request)).toEqual(request);
  });

  it("min > max 時錯誤訊息是繁中、指向 min 欄位", () => {
    const km = searchRequestSchema.safeParse({ ...request, min_km: 30, max_km: 20 });
    expect(km.success).toBe(false);
    expect(km.error?.issues[0]).toMatchObject({ path: ["min_km"], message: "最小公里數不能大於最大公里數" });
    const rotation = searchRequestSchema.safeParse({ ...request, rotation_min_deg: 20, rotation_max_deg: 10 });
    expect(rotation.error?.issues[0]).toMatchObject({ path: ["rotation_min_deg"] });
  });

  it("缺欄位不合法", () => {
    const rest: Partial<typeof request> = { ...request };
    delete rest.center;
    expect(searchRequestSchema.safeParse(rest).success).toBe(false);
  });
});

describe("searchEventSchema", () => {
  it("五種事件都能解析", () => {
    const events = [
      { type: "queued", position: 1 },
      { type: "progress", stage: "coarse", done: 3, total: 10 },
      { type: "best", candidate: sampleCandidate() },
      sampleResult([sampleCandidate()], "沒有符合公里數的候選"),
      { type: "error", code: "busy", message: "排隊太久" },
    ];
    for (const event of events) expect(searchEventSchema.parse(event)).toEqual(event);
  });

  it("result 的 notice 可省略或為 null；候選最多 5 個", () => {
    expect(searchEventSchema.safeParse(sampleResult([])).success).toBe(true);
    expect(searchEventSchema.safeParse(sampleResult([], null)).success).toBe(true);
    const five = Array.from({ length: 5 }, () => sampleCandidate());
    expect(searchEventSchema.safeParse(sampleResult(five)).success).toBe(true);
    expect(searchEventSchema.safeParse(sampleResult([...five, sampleCandidate()])).success).toBe(false);
  });

  it("擺放：size_m > 0、rotation_deg 在 [0, 360)", () => {
    const placement = (size_m: number, rotation_deg: number) => ({
      type: "best",
      candidate: sampleCandidate({ placement: { center: [121.5, 25], size_m, rotation_deg } }),
    });
    expect(searchEventSchema.safeParse(placement(1000, 0)).success).toBe(true);
    expect(searchEventSchema.safeParse(placement(1000, 359.9)).success).toBe(true);
    expect(searchEventSchema.safeParse(placement(1000, 360)).success).toBe(false);
    expect(searchEventSchema.safeParse(placement(0, 0)).success).toBe(false);
  });

  it("排隊位置至少是 1；進度是非負整數；階段只有三種", () => {
    expect(searchEventSchema.safeParse({ type: "queued", position: 0 }).success).toBe(false);
    expect(searchEventSchema.safeParse({ type: "progress", stage: "coarse", done: 1.5, total: 2 }).success).toBe(false);
    expect(searchEventSchema.safeParse({ type: "progress", stage: "polish", done: 1, total: 2 }).success).toBe(false);
  });

  it("候選的 match 必須是合法的 /match 回應", () => {
    const bad = { type: "best", candidate: { ...sampleCandidate(), match: { route: null } } };
    expect(searchEventSchema.safeParse(bad).success).toBe(false);
  });

  it("失敗事件的 code 限定四種", () => {
    expect(searchFailedEventSchema.safeParse({ type: "error", code: "oops", message: "x" }).success).toBe(false);
  });

  it("不認得的 type 不合法（呼叫端依 SSE 事件名稱先略過不認得的事件）", () => {
    expect(searchEventSchema.safeParse({ type: "hello" }).success).toBe(false);
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
    expect(statusResponseSchema.parse({ data: null, search_ready: false, planner_ready: false })).toEqual({
      data: null,
      search_ready: false,
      planner_ready: false,
    });
  });

  it("舊版引擎沒有 search_ready／planner_ready：當成 false，不是格式錯誤", () => {
    expect(statusResponseSchema.parse({ data: null })).toEqual({ data: null, search_ready: false, planner_ready: false });
    expect(statusResponseSchema.parse({ data: null, search_ready: true })).toEqual({
      data: null,
      search_ready: true,
      planner_ready: false,
    });
  });

  it("planner_ready 原樣讀出；不是布林值就不合法", () => {
    expect(statusResponseSchema.parse({ data: null, planner_ready: true }).planner_ready).toBe(true);
    expect(statusResponseSchema.safeParse({ data: null, planner_ready: 1 }).success).toBe(false);
  });

  it("search_ready 原樣讀出；不是布林值就不合法", () => {
    expect(statusResponseSchema.parse({ data: null, search_ready: true }).search_ready).toBe(true);
    expect(statusResponseSchema.safeParse({ data: null, search_ready: "yes" }).success).toBe(false);
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

  it("/search 的每一種事件可指派", () => {
    type Schemas = components["schemas"];
    type Event = z.infer<typeof searchEventSchema>;
    expectTypeOf<Schemas["SearchQueued"]>().toExtend<Event>();
    expectTypeOf<Schemas["SearchProgress"]>().toExtend<Event>();
    expectTypeOf<Schemas["SearchBest"]>().toExtend<Event>();
    expectTypeOf<Schemas["SearchResult"]>().toExtend<Event>();
    expectTypeOf<Schemas["SearchFailed"]>().toExtend<Event>();
  });

  it("ErrorDetail 可被 errorDetailSchema（超集）接受；不綁 code enum", () => {
    expectTypeOf<components["schemas"]["ErrorDetail"]>().toExtend<z.infer<typeof errorDetailSchema>>();
  });
});
