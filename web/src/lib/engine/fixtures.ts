import type { EditRequest, MatchResponse, SearchCandidate, SearchEvent, SearchResult, VerifyRequest } from "@/lib/engine/contract";

/** 測試用的 /match 200 回應（形狀照 spec §4.7）。 */
export const SAMPLE_MATCH_RESPONSE: MatchResponse = {
  route: {
    points: [
      [121.5, 25.03],
      [121.501, 25.03],
      [121.502, 25.031],
      [121.503, 25.031],
    ],
    length_m: 2400.5,
  },
  segments: [
    { kind: "stroke", stroke_index: 0, points: [[121.5, 25.03], [121.501, 25.03]], length_m: 1000 },
    { kind: "connector", stroke_index: null, points: [[121.501, 25.03], [121.502, 25.031]], length_m: 400.5 },
    { kind: "detour", stroke_index: 1, points: [[121.502, 25.031], [121.503, 25.031]], length_m: 1000 },
  ],
  score: { coverage: 82, cleanliness: 74, direction: 66, overall: 78 },
  target_length_m: 2100.2,
  warnings: [{ stroke_index: 2, code: "no_route", message: "這一筆附近 150 m 內沒有可騎的道路" }],
  data_version: "20261010T145928Z",
  route_sig: "c2lnbmF0dXJl",
};

/** 測試用的 /edit 200 回應：中間那段被換成 kind: "edit"。 */
export const SAMPLE_EDIT_RESPONSE: MatchResponse = {
  ...SAMPLE_MATCH_RESPONSE,
  segments: SAMPLE_MATCH_RESPONSE.segments.map((segment, i) => (i === 1 ? { ...segment, kind: "edit" } : segment)),
  route_sig: "ZWRpdGVk",
};

/** 測試用的 /edit 請求：整條 SAMPLE_MATCH_RESPONSE 原封不動帶回，剪掉第 0 段 120.5 m 處。 */
export const SAMPLE_EDIT_REQUEST: EditRequest = {
  segments: SAMPLE_MATCH_RESPONSE.segments,
  data_version: SAMPLE_MATCH_RESPONSE.data_version,
  route_sig: SAMPLE_MATCH_RESPONSE.route_sig,
  strokes: [{ points: [[121.5, 25.03], [121.51, 25.03]], closed: false }],
  op: { type: "cut", at: { segment: 0, offset_m: 120.5 } },
};

/** 測試用的 /verify 請求：同一份路線與簽章，沒有操作。 */
export const SAMPLE_VERIFY_REQUEST: VerifyRequest = without(SAMPLE_EDIT_REQUEST, "op");

/** 拿掉一個欄位的拷貝（測「缺欄位」）。 */
export function without<T extends object, K extends keyof T>(value: T, key: K): Omit<T, K> {
  const copy = { ...value };
  delete copy[key];
  return copy;
}

/** 測試用的搜尋候選：擺放在台北 101 附近，吸附結果沿用 SAMPLE_MATCH_RESPONSE。 */
export function sampleCandidate(overrides: Partial<SearchCandidate> = {}): SearchCandidate {
  return {
    placement: { center: [121.5654, 25.033], size_m: 1800, rotation_deg: 10 },
    match: SAMPLE_MATCH_RESPONSE,
    start_distance_m: 420.5,
    length_ok: true,
    ...overrides,
  };
}

/** 測試用的 /search 最後一個事件（成功）。 */
export function sampleResult(candidates: SearchCandidate[], notice?: string | null): SearchResult {
  return { type: "result", candidates, coarse_candidates: 270_000, evaluated: 10, elapsed_ms: 18_250, notice };
}

/** 把事件寫成引擎送出的 SSE 格式（event 名稱 = data.type，空行結尾）。 */
export function toSse(event: SearchEvent): string {
  return `event: ${event.type}\ndata: ${JSON.stringify(event)}\n\n`;
}
