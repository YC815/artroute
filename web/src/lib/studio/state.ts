import type { MatchError } from "@/lib/engine/client";
import type { MatchResponse, MatchWarning } from "@/lib/engine/contract";
import { shapeProblem } from "@/lib/match/request";
import type { Shape } from "@/lib/shape/types";
import type { SavedSource } from "@/lib/storage/work";
import { normalizeText, textShape, validateText } from "@/lib/text/layout";

/** 圖片來源的 GPX 名稱。 */
export const IMAGE_ROUTE_NAME = "圖片路線";

/** GPX 的作品名稱：文字來源用文字本身（與畫出來的一致），圖片來源用「圖片路線」。控制字元與換行由 buildGpx 處理。 */
export function gpxName(source: SavedSource): string {
  return source.kind === "text" ? normalizeText(source.text) : IMAGE_ROUTE_NAME;
}

/** 圖形來源 → 圖形；來源不合法（文字不合規、圖片沒有筆畫）時是 null。 */
export function shapeOfSource(source: SavedSource): Shape | null {
  if (source.kind === "image") return source.strokes.length > 0 ? { kind: "image", strokes: source.strokes } : null;
  return validateText(source.text) === null ? textShape(source.text, source.font) : null;
}

/**
 * 可以自動存檔的來源：文字要通過 validateText；圖片要有筆畫且符合 /match 上限。
 * 與 loadWork 的驗證一致，避免存了之後讀不回來（讀檔失敗會蓋掉上一份合法的作品）。
 */
export function isSavable(source: SavedSource): boolean {
  return source.kind === "text"
    ? validateText(source.text) === null
    : shapeProblem({ kind: "image", strokes: source.strokes }) === null;
}

/**
 * 有路線、至少 2 點，而且路線屬於「目前的請求」才能下載 GPX：結果所屬請求的 key（resultKey）必須等於目前請求的 key。
 * 同一個圖形換了擺放位置，圖形的 tag 不變、請求的 key 會變，所以舊擺放的路線不會被下載。
 * 不靠 pending：cancel 會把 pending 清掉，結果卻還留著，任何取消路徑都不能因此放行舊路線。
 */
export function canDownloadGpx(
  response: MatchResponse | null,
  resultKey: string | null,
  currentKey: string | null,
): boolean {
  return response !== null && response.route.points.length >= 2 && resultKey !== null && resultKey === currentKey;
}

/** 圖形內容的識別字：內容相同的圖形（即使是不同物件）key 相同。 */
export function shapeKey(shape: Shape | null): string | null {
  return shape ? JSON.stringify(shape.strokes) : null;
}

/** 吸附結果：路線或錯誤（兩者不會同時存在）。 */
export type Outcome = { response: MatchResponse | null; error: MatchError | null };

const NOTHING: Outcome = { response: null, error: null };

/**
 * 只顯示屬於目前圖形的結果。appliedTag 是結果的請求在「送出時」標記的圖形 key（useLiveMatch 的 appliedTag），
 * 所以圖形換了之後，不論舊結果早到晚到、新請求送出了沒，都不會畫在新圖形上。
 */
export function visibleOutcome(outcome: Outcome, appliedTag: string | null, key: string | null): Outcome {
  return key !== null && appliedTag === key ? outcome : NOTHING;
}

/**
 * 錯誤旁要不要放「重新吸附」：引擎離線（連不上、503）由橫幅的「重試」處理（重新查狀態、恢復後自動重送），
 * 其他錯誤（504、422、400、502…）停用吸附的橫幅不會出現，只能靠這個按鈕重送同一個請求。
 */
export function canRetry(error: MatchError | null): boolean {
  return error !== null && !error.engineDown;
}

// 固定的空陣列：沒有 warnings 時每次 render 回傳同一個物件，useMemo 才不會白白重算
const NO_WARNINGS: readonly MatchWarning[] = [];

/** 要標紅的部件：200 回應的 warnings，或 422（所有部件都失敗）錯誤 body 的 warnings。 */
export function outcomeWarnings(outcome: Outcome): readonly MatchWarning[] {
  return outcome.response?.warnings ?? outcome.error?.warnings ?? NO_WARNINGS;
}
