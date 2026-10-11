import { responseFromRoute, signedOf } from "@/lib/edit/edit-bridge";
import type { EditedRoute, EditState } from "@/lib/edit/edit-state";
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

/** 能進入微調：有屬於目前擺放的路線、路線有簽章、沒有請求在途、也還沒在微調。 */
export function canEnterEdit(args: {
  response: MatchResponse | null;
  downloadable: boolean;
  pending: boolean;
  edit: EditState | null;
}): boolean {
  return args.response !== null && signedOf(args.response) !== null && args.downloadable && !args.pending && args.edit === null;
}

export type DisplayedRoute = {
  response: MatchResponse | null;
  /** GPX 與下載按鈕以畫面上的路線為準。 */
  downloadable: boolean;
  /** 畫面上是微調後的路線（微調中，或按了「完成」之後還留著）。 */
  edited: boolean;
};

/**
 * 畫面上、資訊面板、GPX 用的路線：有微調的路線（edited：微調狀態裡歷史目前那一步，含按了「完成」之後）就用它，
 * 否則用即時吸附的結果。
 * 微調的路線由引擎簽章、整條都檢查過，不需要比對請求的 key。
 */
export function resolveDisplayedRoute(
  edited: EditedRoute | null,
  live: { response: MatchResponse | null; downloadable: boolean },
  targetLengthM: number | null,
): DisplayedRoute {
  if (edited === null) return { ...live, edited: false };
  const response = responseFromRoute(edited, targetLengthM);
  return { response, downloadable: response.route.points.length >= 2, edited: true };
}

/** 重新驗證通過的版本，連同當時 /status 回報的版本（engine 的 /status 還沒重新讀之前，用它判斷路線是不是最新）。 */
export type VerifiedBuild = { seenEngineVersion: string | null; version: string };

/**
 * 目前路網的版本：頁面載入時讀到的 /status 在引擎重建路網之後就過時了，重新驗證通過的版本才是真的；
 * /status 重新讀到不同的版本之後（seenEngineVersion 不再相等），以它為準。
 */
export function currentBuildVersion(engineVersion: string | null, verified: VerifiedBuild | null): string | null {
  return verified && verified.seenEngineVersion === engineVersion ? verified.version : engineVersion;
}

/**
 * 微調相關的畫面元件各在什麼時候出現：
 * - 工具列：微調中（active）而且路線不是舊路網的；
 * - 重新驗證的提示：微調中而且路線是舊路網的（打開舊草稿，或引擎回 409）。按了「完成」之後不顯示——
 *   它只在要編輯時有意義（完成後路線照常顯示、可下載 GPX，要編輯用「繼續微調」，那時才被要求重新驗證）；
 * - 「繼續微調」：畫面上是微調過的路線、但已經按了「完成」。
 */
export function editChrome(args: { edited: boolean; active: boolean; outdated: boolean }) {
  return {
    toolbar: args.active && !args.outdated,
    notice: args.active && args.outdated,
    resume: args.edited && !args.active,
  };
}

/** 路線沒有簽章時，「微調路線」停用的原因（引擎沒有回簽章：版本較舊，或網站先於引擎部署）。 */
export const NO_SIGNATURE_NOTE = "這份吸附結果沒有簽章（引擎版本較舊），不能微調；仍可下載 GPX。";

/** 有可用的即時路線、卻因為沒有簽章而不能微調時回傳說明，否則 null。 */
export function editUnavailableNote(response: MatchResponse | null, downloadable: boolean): string | null {
  return response !== null && downloadable && signedOf(response) === null ? NO_SIGNATURE_NOTE : null;
}

/**
 * 在「我的路線」按「打開」：這份草稿就是目前按了「完成」的那份（路線、復原紀錄都還在記憶體裡）就接著微調（resume），
 * 否則從列表的內容重新打開。重新打開會用列表的快照取代畫面上的路線、歷史重來：
 * 最後一次存檔還沒寫完（或失敗）時畫面會退回舊路線，排隊中的舊存檔之後又把較新的路線寫進去，畫面與草稿就不一致。
 */
export function openDraftAction(edit: EditState | null, draftId: string): "resume" | "open" {
  return edit !== null && !edit.active && edit.draftId === draftId ? "resume" : "open";
}
