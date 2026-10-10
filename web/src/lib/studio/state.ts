import type { MatchError } from "@/lib/engine/client";
import type { MatchResponse, MatchWarning } from "@/lib/engine/contract";
import { shapeProblem } from "@/lib/match/request";
import type { Shape } from "@/lib/shape/types";
import type { SavedSource } from "@/lib/storage/work";
import { normalizeText, validateText } from "@/lib/text/layout";

/** 圖片來源的 GPX 名稱。 */
export const IMAGE_ROUTE_NAME = "圖片路線";

/** GPX 的作品名稱：文字來源用文字本身（與畫出來的一致），圖片來源用「圖片路線」。控制字元與換行由 buildGpx 處理。 */
export function gpxName(source: SavedSource): string {
  return source.kind === "text" ? normalizeText(source.text) : IMAGE_ROUTE_NAME;
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

/** 有路線、至少 2 點，而且不在吸附中（吸附中顯示的路線可能還是上一個擺放的）才能下載 GPX。 */
export function canDownloadGpx(response: MatchResponse | null, pending: boolean): boolean {
  return !pending && response !== null && response.route.points.length >= 2;
}

/** 圖形內容的識別字：內容相同的圖形（即使是不同物件）key 相同。 */
export function shapeKey(shape: Shape | null): string | null {
  return shape ? JSON.stringify(shape.strokes) : null;
}

/** 吸附結果：路線或錯誤（兩者不會同時存在）。 */
export type Outcome = { response: MatchResponse | null; error: MatchError | null };

/** 最近一次吸附結果與它所屬的圖形（shapeKey）。 */
export type OwnedOutcome = Outcome & { shapeKey: string | null };

export const NO_OUTCOME: OwnedOutcome = { response: null, error: null, shapeKey: null };

const NOTHING: Outcome = { response: null, error: null };

/**
 * 新的結果（不同物件）出現時，記下它屬於目前的圖形。
 * 結果被遮掉（都是 null，例如 request 為 null）時不更新：同一個結果之後再出現，仍然屬於原來的圖形。
 * 排程器只套用最新請求的結果，而圖形一變就立刻送出新請求、取消舊的，所以新結果一定屬於目前的圖形。
 */
export function trackOutcome(owned: OwnedOutcome, outcome: Outcome, key: string | null): OwnedOutcome {
  if (!outcome.response && !outcome.error) return owned;
  if (outcome.response === owned.response && outcome.error === owned.error) return owned;
  return { response: outcome.response, error: outcome.error, shapeKey: key };
}

/**
 * 只顯示屬於目前圖形的結果。圖形換了（例如從不合法變回另一個圖形），
 * useLiveMatch 在新結果回來前仍回傳舊圖形的路線，這裡把它藏起來。owned 要先經過 trackOutcome。
 */
export function visibleOutcome(owned: OwnedOutcome, outcome: Outcome, key: string | null): Outcome {
  return owned.shapeKey === key ? outcome : NOTHING;
}

// 固定的空陣列：沒有 warnings 時每次 render 回傳同一個物件，useMemo 才不會白白重算
const NO_WARNINGS: readonly MatchWarning[] = [];

/** 要標紅的部件：200 回應的 warnings，或 422（所有部件都失敗）錯誤 body 的 warnings。 */
export function outcomeWarnings(outcome: Outcome): readonly MatchWarning[] {
  return outcome.response?.warnings ?? outcome.error?.warnings ?? NO_WARNINGS;
}
