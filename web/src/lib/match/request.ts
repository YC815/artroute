import type { MatchRequest, StartCircle } from "@/lib/engine/contract";
import { MAX_STROKE_POINTS, MAX_STROKES, MIN_STROKE_POINTS } from "@/lib/limits";
import { transformStrokes, type Transform } from "@/lib/shape/transform";
import type { Shape } from "@/lib/shape/types";

/** 圖形不能送去吸附的原因（給使用者看）；可以送時回傳 null。 */
export function shapeProblem(shape: Shape): string | null {
  const count = shape.strokes.length;
  if (count === 0) return "沒有任何筆畫";
  if (count > MAX_STROKES) return `筆畫有 ${count} 筆，超過上限 ${MAX_STROKES} 筆：請減少字數或選取的輪廓`;
  if (shape.strokes.some((s) => s.points.length > MAX_STROKE_POINTS)) {
    return `有筆畫超過 ${MAX_STROKE_POINTS} 點`;
  }
  if (shape.strokes.some((s) => s.points.length < MIN_STROKE_POINTS)) {
    return `有筆畫少於 ${MIN_STROKE_POINTS} 點`;
  }
  return null;
}

/**
 * 依目前擺放把圖形轉成經緯度，組成 /match 請求（search_radius_m 省略，用引擎預設）。
 * 有自動搜尋的範圍時帶上 start_circle：手動微調時路線起點仍盡量落在圈內；沒有範圍時省略（行為與以前相同）。
 */
export function buildMatchRequest(shape: Shape, t: Transform, startCircle: StartCircle | null = null): MatchRequest {
  const strokes = transformStrokes(shape.strokes, t);
  return startCircle ? { strokes, start_circle: startCircle } : { strokes };
}

/**
 * 請求的 key：內容相同的請求（不論是不是同一個物件）key 相同。排程器用它判斷不重送；
 * 結果回來時也帶著它（onOutcome 的第三個參數），所以「結果屬於哪個請求」可以直接和目前請求的 key 比對。
 */
export function requestKey(request: MatchRequest): string {
  return JSON.stringify(request);
}
