import type { MatchResponse, SearchCandidate, SearchEvent, SearchResult } from "@/lib/engine/contract";

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
};

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
