import type { MatchRequest } from "@/lib/engine/contract";
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

/** 依目前擺放把圖形轉成經緯度，組成 /match 請求（search_radius_m 省略，用引擎預設）。 */
export function buildMatchRequest(shape: Shape, t: Transform): MatchRequest {
  return { strokes: transformStrokes(shape.strokes, t) };
}
