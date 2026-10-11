"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import {
  buildEditOp,
  canRedoEdit,
  canUndoEdit,
  editReducer,
  startEdit,
  type EditAction,
  type EditedRoute,
  type EditOp,
  type EditState,
  type EditTool,
  type RerouteMode,
  type RoutePos,
} from "@/lib/edit/edit-state";
import type { DraftPlacement } from "@/lib/storage/drafts";
import type { LonLat } from "@/lib/shape/types";

export type EditOutcome = { ok: true; route: EditedRoute } | { ok: false; code: string; message: string };
/** 送一個操作給引擎 /edit；signal 被 abort 時結果一律不套用。 */
export type PostEdit = (op: EditOp, route: EditedRoute, signal: AbortSignal) => Promise<EditOutcome>;
/**
 * draftId 為 null 時建立草稿（記 placement），否則更新它；回傳草稿 id，存不了時回 null。
 * placement 是進入這次微調時綁定的：存檔排隊的時候使用者可能已經打開了別的草稿，不能讀「現在」的。
 */
export type SaveDraft = (route: EditedRoute, draftId: string | null, placement: DraftPlacement) => Promise<string | null>;

const UNEXPECTED_MESSAGE = "微調失敗，請再試一次";

export type RouteEditActions = {
  /** 開始新的微調（自動吸附的路線：draftId 為 null；打開的草稿：帶它的 id）；placement 是這條路線對應的圖形來源與擺放。 */
  enter(route: EditedRoute, draftId: string | null, placement: DraftPlacement): void;
  /** 「完成」之後回到同一個微調，復原的紀錄還在。 */
  resume(): void;
  /** 「完成」：取消在途的請求，路線留著但不收操作。 */
  finish(): void;
  /** 圖形移動、回到自動吸附：取消在途的請求、丟掉微調狀態（路線已經在草稿裡）。 */
  reset(): void;
  /**
   * 重新驗證通過：換成新版路網的路線、歷史重新開始，並存進草稿（已經有草稿，或這次微調已經存過才存；
   * 還沒存過草稿的微調不會因為驗證而建立草稿）。from 是送去驗證的那條路線的簽章：目前的路線已經不是它
   * （使用者在等待中打開了別的草稿、回到自動吸附）就不套用。
   */
  replaceRoute(route: EditedRoute, from: string): void;
  selectTool(tool: EditTool | null): void;
  pick(pos: RoutePos): void;
  chooseMode(mode: RerouteMode): void;
  drag(grab: RoutePos, to: LonLat): void;
  /** Esc：等待中就放棄這次操作，否則清掉正在選的點，再按一次取消工具。 */
  cancel(): void;
  undo(): void;
  redo(): void;
};

export type RouteEditController = RouteEditActions & {
  /** null：沒有在微調，也沒有「完成」後留著的路線。 */
  getState(): EditState | null;
  /** 卸載：取消在途的請求，之後什麼都不做、不再通知（已經開始的存檔照樣寫完）。 */
  dispose(): void;
};

/**
 * 微調的流程（useRouteEdit 的核心，不依賴 React 所以能在 node 測）：
 * 選完操作就送出；同一時間最多一個請求，abort 掉的請求晚到的結果不套用。
 * 每次路線變了（成功的編輯、復原、重做）都存草稿，存檔排成一列：第一次建立的 id 回來之前，
 * 下一次存檔要等它，否則會建出兩份草稿。
 */
export function createRouteEditController(deps: {
  postEdit: PostEdit;
  saveDraft: SaveDraft;
  onChange: (state: EditState | null) => void;
}): RouteEditController {
  let state: EditState | null = null;
  let inflight: AbortController | null = null;
  // 每次 enter 一個新的草稿紀錄：上一次微調的存檔晚到時，id 只記回它自己那一次
  let draft: { id: string | null; saved: boolean; placement: DraftPlacement } | null = null;
  let saving: Promise<void> = Promise.resolve();
  let disposed = false;

  const set = (next: EditState | null) => {
    if (disposed || next === state) return;
    state = next;
    deps.onChange(state);
  };

  const dispatch = (action: EditAction) => {
    if (state) set(editReducer(state, action));
  };

  const abortInflight = () => {
    inflight?.abort();
    inflight = null;
  };

  const save = (route: EditedRoute) => {
    const target = draft;
    if (!target) return;
    target.saved = true;
    saving = saving.then(async () => {
      try {
        const id = await deps.saveDraft(route, target.id, target.placement);
        if (id === null) return;
        target.id = id;
        if (target === draft) dispatch({ type: "setDraftId", draftId: id });
      } catch {
        // 存不了不影響微調；下一次路線變了會再存
      }
    });
  };

  const settle = (request: AbortController, outcome: EditOutcome) => {
    if (request.signal.aborted || request !== inflight) return;
    inflight = null;
    if (outcome.ok) {
      dispatch({ type: "succeeded", route: outcome.route });
      save(outcome.route);
    } else {
      dispatch({ type: "failed", code: outcome.code, message: outcome.message });
    }
  };

  /** 操作選完了就送出；等待中、不在微調時 reducer 會擋掉 submit。 */
  const submitIfReady = () => {
    const before = state;
    if (!before) return;
    const op = buildEditOp(before);
    if (!op) return;
    dispatch({ type: "submit" });
    if (state === before) return;
    const request = new AbortController();
    inflight = request;
    deps.postEdit(op, before.history.present, request.signal).then(
      (outcome) => settle(request, outcome),
      () => settle(request, { ok: false, code: "unexpected", message: UNEXPECTED_MESSAGE }),
    );
  };

  const step = (action: EditAction) => {
    const before = state;
    dispatch(action);
    if (state && before && state.history !== before.history) save(state.history.present);
  };

  return {
    getState: () => state,
    enter(route, draftId, placement) {
      if (disposed) return;
      abortInflight();
      draft = { id: draftId, saved: false, placement };
      set(startEdit(route, draftId));
    },
    resume: () => dispatch({ type: "resume" }),
    finish() {
      abortInflight();
      dispatch({ type: "finish" });
    },
    reset() {
      abortInflight();
      draft = null;
      set(null);
    },
    replaceRoute(route, from) {
      if (!state?.active || state.history.present.routeSig !== from) return;
      abortInflight();
      dispatch({ type: "replaceRoute", route });
      if (draft && (draft.id !== null || draft.saved)) save(route);
    },
    selectTool: (tool) => dispatch({ type: "selectTool", tool }),
    pick(pos) {
      dispatch({ type: "pick", pos });
      submitIfReady();
    },
    chooseMode(mode) {
      dispatch({ type: "chooseMode", mode });
      submitIfReady();
    },
    drag(grab, to) {
      dispatch({ type: "drag", grab, to });
      submitIfReady();
    },
    cancel() {
      if (state?.active && state.pending) abortInflight();
      dispatch({ type: "cancel" });
    },
    undo: () => step({ type: "undo" }),
    redo: () => step({ type: "redo" }),
    dispose() {
      abortInflight();
      disposed = true;
    },
  };
}

export type RouteEdit = RouteEditActions & {
  state: EditState | null;
  canUndo: boolean;
  canRedo: boolean;
};

/**
 * 微調路線的狀態與操作（見 createRouteEditController）。postEdit、saveDraft 每次 render 都可以是新的函式，
 * 用的永遠是最新的那個。卸載時取消在途的請求。
 */
export function useRouteEdit(deps: { postEdit: PostEdit; saveDraft: SaveDraft }): RouteEdit {
  const [state, setState] = useState<EditState | null>(null);
  const controllerRef = useRef<RouteEditController | null>(null);
  const depsRef = useRef(deps);

  useEffect(() => {
    depsRef.current = deps;
  });

  useEffect(() => {
    const controller = createRouteEditController({
      postEdit: (op, route, signal) => depsRef.current.postEdit(op, route, signal),
      saveDraft: (route, draftId, placement) => depsRef.current.saveDraft(route, draftId, placement),
      onChange: setState,
    });
    controllerRef.current = controller;
    return () => {
      controller.dispose();
      controllerRef.current = null;
    };
  }, []);

  const actions = useMemo<RouteEditActions>(
    () => ({
      enter: (route, draftId, placement) => controllerRef.current?.enter(route, draftId, placement),
      resume: () => controllerRef.current?.resume(),
      finish: () => controllerRef.current?.finish(),
      reset: () => controllerRef.current?.reset(),
      replaceRoute: (route, from) => controllerRef.current?.replaceRoute(route, from),
      selectTool: (tool) => controllerRef.current?.selectTool(tool),
      pick: (pos) => controllerRef.current?.pick(pos),
      chooseMode: (mode) => controllerRef.current?.chooseMode(mode),
      drag: (grab, to) => controllerRef.current?.drag(grab, to),
      cancel: () => controllerRef.current?.cancel(),
      undo: () => controllerRef.current?.undo(),
      redo: () => controllerRef.current?.redo(),
    }),
    [],
  );

  return {
    ...actions,
    state,
    canUndo: state ? canUndoEdit(state) : false,
    canRedo: state ? canRedoEdit(state) : false,
  };
}
