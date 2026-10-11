import type { SearchCandidate, SearchResult, SearchStage } from "@/lib/engine/contract";
import type { SearchOutcome, SearchUpdate } from "@/lib/engine/search-client";

/** 搜尋的階段：送出後還沒有任何事件是 starting；排隊中 queued；搜尋中 running；結束是 done 或 failed。 */
export type SearchStatus =
  | { phase: "idle" }
  | { phase: "starting" }
  | { phase: "queued"; position: number }
  | { phase: "running"; stage: SearchStage; done: number; total: number }
  | { phase: "done"; result: SearchResult }
  | { phase: "failed"; message: string };

/**
 * run：目前這次搜尋的編號（0 = 沒有進行中的搜尋）；事件與結果帶著送出時的編號，不同的一律丟掉。
 * tag：搜尋時的圖形 key（候選的擺放只對那個圖形有意義）。
 * best：搜尋中到目前為止最好的候選（地圖上的即時預覽）；結束後清掉。
 */
export type SearchState = { run: number; tag: string | null; status: SearchStatus; best: SearchCandidate | null };

export type SearchAction =
  | { type: "start"; run: number; tag: string }
  | { type: "update"; run: number; update: SearchUpdate }
  | { type: "finish"; run: number; outcome: SearchOutcome }
  | { type: "cancel" };

export const INITIAL_SEARCH: SearchState = { run: 0, tag: null, status: { phase: "idle" }, best: null };

function isActive(status: SearchStatus): boolean {
  return status.phase === "starting" || status.phase === "queued" || status.phase === "running";
}

export function searchReducer(state: SearchState, action: SearchAction): SearchState {
  if (action.type === "start") return { run: action.run, tag: action.tag, status: { phase: "starting" }, best: null };
  if (action.type === "cancel") return INITIAL_SEARCH;
  // 已取消、已被新的搜尋取代或已結束的搜尋：晚到的事件與結果一律丟掉
  if (action.run !== state.run || !isActive(state.status)) return state;
  if (action.type === "update") {
    const { update } = action;
    if (update.type === "queued") return { ...state, status: { phase: "queued", position: update.position } };
    if (update.type === "progress") {
      return { ...state, status: { phase: "running", stage: update.stage, done: update.done, total: update.total } };
    }
    return { ...state, best: update.candidate };
  }
  const { outcome } = action;
  if (outcome.kind === "aborted") return INITIAL_SEARCH;
  if (outcome.kind === "result") return { ...state, status: { phase: "done", result: outcome.result }, best: null };
  return { ...state, status: { phase: "failed", message: outcome.error.message }, best: null };
}

export type VisibleSearch = { status: SearchStatus; best: SearchCandidate | null; active: boolean };

const HIDDEN: VisibleSearch = { status: { phase: "idle" }, best: null, active: false };

/** 只顯示屬於目前圖形的搜尋：圖形換了之後，舊圖形的結果、預覽與進度都不顯示。 */
export function visibleSearch(state: SearchState, currentTag: string | null): VisibleSearch {
  if (state.tag !== null && state.tag !== currentTag) return HIDDEN;
  return { status: state.status, best: state.best, active: isActive(state.status) };
}
