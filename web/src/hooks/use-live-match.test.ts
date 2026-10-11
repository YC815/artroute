import { describe, expect, it } from "vitest";
import {
  applyFastOutcome,
  applyFineResponse,
  matchForRequest,
  type LiveMatch,
} from "@/hooks/use-live-match";
import type { MatchRequest } from "@/lib/engine/contract";
import { SAMPLE_MATCH_RESPONSE } from "@/lib/engine/fixtures";

const request: MatchRequest = { strokes: [{ points: [[121.5, 25], [121.51, 25]], closed: false }] };
const withResponse: LiveMatch = {
  response: SAMPLE_MATCH_RESPONSE,
  fineResponse: null,
  error: null,
  pending: false,
  appliedTag: "A",
  appliedKey: "K",
};
const withError: LiveMatch = {
  response: null,
  fineResponse: null,
  error: { status: 422, message: "輸入不合法", warnings: [], engineDown: false },
  pending: false,
  appliedTag: "A",
  appliedKey: "K",
};

describe("matchForRequest", () => {
  it("有 request 時原樣回傳", () => {
    expect(matchForRequest(withResponse, request)).toBe(withResponse);
    expect(matchForRequest(withError, request)).toBe(withError);
  });

  it("request 為 null（輸入被清空或圖形不合法）時不回傳舊路線與舊錯誤", () => {
    const cleared = {
      response: null,
      fineResponse: null,
      error: null,
      pending: false,
      appliedTag: "A",
      appliedKey: "K",
    };
    expect(matchForRequest(withResponse, null)).toEqual(cleared);
    expect(matchForRequest(withError, null)).toEqual(cleared);
  });
});

describe("fast 與描形版狀態", () => {
  const fine = { ...SAMPLE_MATCH_RESPONSE, score: { ...SAMPLE_MATCH_RESPONSE.score, overall: 91 } };

  it("描形版回來後仍保留 fast，兩版都拿得到", () => {
    const refined = applyFineResponse(withResponse, fine, "K");
    expect(refined.response).toBe(SAMPLE_MATCH_RESPONSE);
    expect(refined.fineResponse).toBe(fine);
  });

  it("不同擺放的描形版不會套用", () => {
    expect(applyFineResponse(withResponse, fine, "別的擺放")).toBe(withResponse);
  });

  it("新的 fast 成功或失敗套用時都清掉舊描形版", () => {
    const refined = { ...withResponse, fineResponse: fine };
    const next = { ...SAMPLE_MATCH_RESPONSE, score: { ...SAMPLE_MATCH_RESPONSE.score, overall: 64 } };
    expect(applyFastOutcome(refined, { ok: true, response: next }, "B", "NEXT")).toMatchObject({
      response: next,
      fineResponse: null,
      error: null,
      appliedTag: "B",
      appliedKey: "NEXT",
    });

    const error = { status: 504, message: "逾時", warnings: [], engineDown: false };
    expect(applyFastOutcome(refined, { ok: false, error }, "B", "NEXT")).toMatchObject({
      response: null,
      fineResponse: null,
      error,
      appliedTag: "B",
      appliedKey: "NEXT",
    });
  });
});
