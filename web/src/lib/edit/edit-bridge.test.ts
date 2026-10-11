import { describe, expect, it } from "vitest";
import {
  flattenSegments,
  responseFromRoute,
  routeFromResponse,
  signedOf,
  STALE_DATA_MESSAGE,
  toEditOutcome,
  toEditRequest,
} from "@/lib/edit/edit-bridge";
import type { EditError } from "@/lib/engine/client";
import { SAMPLE_EDIT_REQUEST, SAMPLE_EDIT_RESPONSE, SAMPLE_MATCH_RESPONSE, without } from "@/lib/engine/fixtures";

const error = (overrides: Partial<EditError>): EditError => ({
  status: 422,
  code: undefined,
  message: "引擎說明",
  segments: [],
  engineDown: false,
  ...overrides,
});

describe("routeFromResponse / responseFromRoute", () => {
  it("來回轉換保留簽章、版本、段落、分數與長度", () => {
    const route = routeFromResponse(SAMPLE_EDIT_RESPONSE);
    expect(route).toEqual({
      segments: SAMPLE_EDIT_RESPONSE.segments,
      dataVersion: SAMPLE_EDIT_RESPONSE.data_version,
      routeSig: SAMPLE_EDIT_RESPONSE.route_sig,
      score: SAMPLE_EDIT_RESPONSE.score,
      lengthM: SAMPLE_EDIT_RESPONSE.route.length_m,
    });
    const back = responseFromRoute(route, 2100.2);
    expect(back.segments).toBe(route.segments);
    expect(back.route_sig).toBe(SAMPLE_EDIT_RESPONSE.route_sig);
    expect(back.route.length_m).toBe(SAMPLE_EDIT_RESPONSE.route.length_m);
    expect(back.warnings).toEqual([]);
    expect(back.target_length_m).toBe(2100.2);
  });

  it("route.points 與引擎的攤平一致（GPX 用它）", () => {
    // SAMPLE_MATCH_RESPONSE.route.points 就是照引擎規則攤平 segments 的結果
    expect(responseFromRoute(routeFromResponse(SAMPLE_MATCH_RESPONSE), null).route.points).toEqual(
      SAMPLE_MATCH_RESPONSE.route.points,
    );
  });
});

describe("flattenSegments", () => {
  it("相接的點只算一次、連續重複的點只留一個", () => {
    expect(
      flattenSegments([
        { points: [[1, 1], [2, 2]] },
        { points: [[2, 2], [2, 2], [3, 3]] },
        { points: [[3, 3], [4, 4]] },
      ]),
    ).toEqual([[1, 1], [2, 2], [3, 3], [4, 4]]);
  });

  it("沒有段落是空陣列", () => {
    expect(flattenSegments([])).toEqual([]);
  });
});

describe("toEditRequest", () => {
  it("整條路線與簽章原封不動帶回，加上操作與原圖", () => {
    const route = routeFromResponse(SAMPLE_MATCH_RESPONSE);
    expect(toEditRequest(route, SAMPLE_EDIT_REQUEST.op, SAMPLE_EDIT_REQUEST.strokes)).toEqual(SAMPLE_EDIT_REQUEST);
  });
});

describe("toEditOutcome", () => {
  it("成功：換成新路線", () => {
    expect(toEditOutcome({ ok: true, response: SAMPLE_EDIT_RESPONSE })).toEqual({
      ok: true,
      route: routeFromResponse(SAMPLE_EDIT_RESPONSE),
    });
  });

  it("引擎拒絕：保留 code 與引擎的說明", () => {
    expect(toEditOutcome({ ok: false, error: error({ code: "nothing_to_cut", message: "這裡沒有圈" }) })).toEqual({
      ok: false,
      code: "nothing_to_cut",
      message: "這裡沒有圈",
    });
  });

  it("409 stale_data：說明統一成「路網已更新」，code 留著讓介面提供重新驗證", () => {
    expect(toEditOutcome({ ok: false, error: error({ status: 409, code: "stale_data", message: "別的說法" }) })).toEqual({
      ok: false,
      code: "stale_data",
      message: STALE_DATA_MESSAGE,
    });
  });

  it("沒有 code：連不上與其他 HTTP 錯誤分開", () => {
    expect(toEditOutcome({ ok: false, error: error({ status: 0, engineDown: true }) })).toMatchObject({ code: "engine_down" });
    expect(toEditOutcome({ ok: false, error: error({ status: 504 }) })).toMatchObject({ code: "http_504" });
  });
});

describe("signedOf", () => {
  it("有簽章：原樣；沒有簽章（舊版引擎）：null", () => {
    expect(signedOf(SAMPLE_MATCH_RESPONSE)).toEqual(SAMPLE_MATCH_RESPONSE);
    const unsigned = without(SAMPLE_MATCH_RESPONSE, "route_sig");
    expect(signedOf(unsigned)).toBeNull();
  });
});
