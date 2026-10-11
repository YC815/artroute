import { canRedo, canUndo, pushHistory, redo, startHistory, undo, type History } from "@/lib/edit/history";
import type { LonLat } from "@/lib/shape/types";
import type { DraftRoute } from "@/lib/storage/drafts";

/** 引擎 /match、/edit、/verify 回應裡網站要留著的部分（與草稿存的同一個形狀）。 */
export type EditedRoute = DraftRoute;

export type EditTool = "cut" | "reroute" | "drag";
export type RerouteMode = "shortest" | "shape";

/** 路線上點到的位置；point 只給地圖畫選到的點，送給引擎的是 {segment, offset_m}。 */
export type RoutePos = { segment: number; offsetM: number; point: LonLat };

// spec §3.1 的 op。D3 合併契約後改成引用 contract.ts。
export type EditOpPos = { segment: number; offset_m: number };
export type EditOp =
  | { type: "cut"; at: EditOpPos }
  | { type: "reroute"; from: EditOpPos; to: EditOpPos; mode: RerouteMode }
  | { type: "drag"; grab: EditOpPos; to: LonLat };

export type EditState = {
  /** false：按了「完成」，路線與歷史留著（畫面照樣顯示），但不收任何操作，直到 resume。 */
  active: boolean;
  history: History<EditedRoute>;
  tool: EditTool | null;
  /** 剪掉、拖曳最多 1 個（拖曳是抓點），重畫最多 2 個（依點的順序，引擎會照路線先後排好）。 */
  picks: RoutePos[];
  rerouteMode: RerouteMode | null;
  dragTo: LonLat | null;
  /** 等待引擎：不能復原、重做、換工具或送新的操作。 */
  pending: boolean;
  message: string | null;
  /** 引擎的錯誤碼（例如 stale_data 要提供「重新驗證」）；沒有錯誤時是 null。 */
  errorCode: string | null;
  draftId: string | null;
};

export type EditAction =
  | { type: "selectTool"; tool: EditTool | null }
  | { type: "pick"; pos: RoutePos }
  | { type: "chooseMode"; mode: RerouteMode }
  | { type: "drag"; grab: RoutePos; to: LonLat }
  | { type: "cancel" }
  | { type: "submit" }
  | { type: "succeeded"; route: EditedRoute }
  | { type: "failed"; code: string; message: string }
  | { type: "undo" }
  | { type: "redo" }
  | { type: "finish" }
  | { type: "resume" }
  | { type: "setDraftId"; draftId: string };

export function startEdit(route: EditedRoute, draftId: string | null): EditState {
  return {
    active: true,
    history: startHistory(route),
    tool: null,
    picks: [],
    rerouteMode: null,
    dragTo: null,
    pending: false,
    message: null,
    errorCode: null,
    draftId,
  };
}

const opPos = (p: RoutePos): EditOpPos => ({ segment: p.segment, offset_m: p.offsetM });

/** 選好的操作 → 送給引擎的 op；還沒選完時是 null。不看 pending（送不送由 reducer 的 submit 決定）。 */
export function buildEditOp(state: EditState): EditOp | null {
  const [first, second] = state.picks;
  switch (state.tool) {
    case "cut":
      return state.picks.length === 1 ? { type: "cut", at: opPos(first) } : null;
    case "reroute":
      return state.picks.length === 2 && state.rerouteMode
        ? { type: "reroute", from: opPos(first), to: opPos(second), mode: state.rerouteMode }
        : null;
    case "drag":
      return state.picks.length === 1 && state.dragTo ? { type: "drag", grab: opPos(first), to: state.dragTo } : null;
    default:
      return null;
  }
}

export function canUndoEdit(state: EditState): boolean {
  return state.active && !state.pending && canUndo(state.history);
}

export function canRedoEdit(state: EditState): boolean {
  return state.active && !state.pending && canRedo(state.history);
}

function hasSelection(state: EditState): boolean {
  return state.picks.length > 0 || state.rerouteMode !== null || state.dragTo !== null;
}

const noSelection = { picks: [], rerouteMode: null, dragTo: null } satisfies Partial<EditState>;
const noMessage = { message: null, errorCode: null } satisfies Partial<EditState>;

/** 不適用的 action 回傳同一個物件（React 的 state 不變就不會重繪）。 */
export function editReducer(state: EditState, action: EditAction): EditState {
  const idle = state.active && !state.pending;
  switch (action.type) {
    case "selectTool":
      return idle ? { ...state, ...noSelection, ...noMessage, tool: action.tool } : state;
    case "pick":
      if (!idle) return state;
      if (state.tool === "cut") return { ...state, ...noMessage, picks: [action.pos] };
      if (state.tool === "reroute" && state.picks.length < 2) {
        return { ...state, ...noMessage, picks: [...state.picks, action.pos] };
      }
      return state;
    case "chooseMode":
      return idle && state.tool === "reroute" && state.picks.length === 2 ? { ...state, rerouteMode: action.mode } : state;
    case "drag":
      return idle && state.tool === "drag" ? { ...state, ...noMessage, picks: [action.grab], dragTo: action.to } : state;
    case "cancel":
      if (!state.active) return state;
      if (state.pending || hasSelection(state)) return { ...state, ...noSelection, ...noMessage, pending: false };
      if (state.tool !== null || state.message !== null) return { ...state, ...noMessage, tool: null };
      return state;
    case "submit":
      return idle && buildEditOp(state) ? { ...state, ...noMessage, pending: true } : state;
    case "succeeded":
      return state.pending
        ? { ...state, ...noSelection, ...noMessage, history: pushHistory(state.history, action.route), pending: false }
        : state;
    case "failed":
      if (!state.pending) return state;
      // 重畫失敗多半是換個模式就好（例如「貼圖形」跨筆畫時改用「最短」）：兩個點留著
      return {
        ...state,
        ...(state.tool === "reroute" ? { rerouteMode: null, dragTo: null } : noSelection),
        pending: false,
        message: action.message,
        errorCode: action.code,
      };
    case "undo":
    case "redo": {
      if (!idle) return state;
      const history = action.type === "undo" ? undo(state.history) : redo(state.history);
      return history === state.history ? state : { ...state, ...noSelection, ...noMessage, history };
    }
    case "finish":
      return state.active ? { ...state, ...noSelection, ...noMessage, active: false, tool: null, pending: false } : state;
    case "resume":
      return state.active ? state : { ...state, active: true };
    case "setDraftId":
      return state.draftId === action.draftId ? state : { ...state, draftId: action.draftId };
  }
}
