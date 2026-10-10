import { describe, expect, it } from "vitest";
import { matchForRequest, type LiveMatch } from "@/hooks/use-live-match";
import type { MatchRequest } from "@/lib/engine/contract";
import { SAMPLE_MATCH_RESPONSE } from "@/lib/engine/fixtures";

const request: MatchRequest = { strokes: [{ points: [[121.5, 25], [121.51, 25]], closed: false }] };
const withResponse: LiveMatch = { response: SAMPLE_MATCH_RESPONSE, error: null, pending: false };
const withError: LiveMatch = {
  response: null,
  error: { status: 422, message: "輸入不合法", warnings: [], engineDown: false },
  pending: false,
};

describe("matchForRequest", () => {
  it("有 request 時原樣回傳", () => {
    expect(matchForRequest(withResponse, request)).toBe(withResponse);
    expect(matchForRequest(withError, request)).toBe(withError);
  });

  it("request 為 null（輸入被清空或圖形不合法）時不回傳舊路線與舊錯誤", () => {
    expect(matchForRequest(withResponse, null)).toEqual({ response: null, error: null, pending: false });
    expect(matchForRequest(withError, null)).toEqual({ response: null, error: null, pending: false });
  });
});
