import type { MatchResponse } from "@/lib/engine/contract";

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
  score: { coverage: 82, cleanliness: 74, overall: 78 },
  target_length_m: 2100.2,
  warnings: [{ stroke_index: 2, code: "no_route", message: "這一筆附近 150 m 內沒有可騎的道路" }],
  data_version: "20261010T145928Z",
};
