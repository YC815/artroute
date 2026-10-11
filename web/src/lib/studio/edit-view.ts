import { responseFromRoute } from "@/lib/edit/edit-bridge";
import type { EditState } from "@/lib/edit/edit-state";
import type { MatchResponse } from "@/lib/engine/contract";
import type { Transform } from "@/lib/shape/transform";

/** 微調開始時的圖形與擺放：之後圖形或擺放跟它不同，就代表使用者移動了圖形（見 editSupersededBy）。 */
export type EditBase = { shapeKey: string | null; transform: Transform };

/** 即時吸附只在引擎可用、而且沒有微調（含按了「完成」之後還留著的微調路線）時進行。 */
export function liveMatchEnabled(engineOk: boolean, edit: EditState | null): boolean {
  return engineOk && edit === null;
}

function sameTransform(a: Transform, b: Transform): boolean {
  return a.center[0] === b.center[0] && a.center[1] === b.center[1] && a.sizeM === b.sizeM && a.rotationDeg === b.rotationDeg;
}

/**
 * 使用者移動了圖形（拖曳、鍵盤、套用搜尋候選、改文字、換圖片）：圖形或擺放跟進入微調時不同。
 * 比內容：點一下圖形沒有移動（pointerup 也會產生新的 transform 物件）不算。
 */
export function editSupersededBy(base: EditBase | null, current: EditBase): boolean {
  return base !== null && (base.shapeKey !== current.shapeKey || !sameTransform(base.transform, current.transform));
}

/** 能進入微調：有屬於目前擺放的路線、沒有請求在途、也還沒在微調。 */
export function canEnterEdit(args: {
  response: MatchResponse | null;
  downloadable: boolean;
  pending: boolean;
  edit: EditState | null;
}): boolean {
  return args.response !== null && args.downloadable && !args.pending && args.edit === null;
}

export type DisplayedRoute = {
  response: MatchResponse | null;
  /** GPX 與下載按鈕以畫面上的路線為準。 */
  downloadable: boolean;
  /** 畫面上是微調後的路線（微調中，或按了「完成」之後還留著）。 */
  edited: boolean;
};

/**
 * 畫面上、資訊面板、GPX 用的路線：有微調狀態就用微調後（歷史目前那一步）的路線，否則用即時吸附的結果。
 * 微調的路線由引擎簽章、整條都檢查過，不需要比對請求的 key。
 */
export function resolveDisplayedRoute(
  edit: EditState | null,
  live: { response: MatchResponse | null; downloadable: boolean },
  targetLengthM: number | null,
): DisplayedRoute {
  if (edit === null) return { ...live, edited: false };
  const response = responseFromRoute(edit.history.present, targetLengthM);
  return { response, downloadable: response.route.points.length >= 2, edited: true };
}
