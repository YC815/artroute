"use client";

import { useCallback, useEffect, useReducer, useRef } from "react";
import type { SearchRequest } from "@/lib/engine/contract";
import { fetchSearch } from "@/lib/engine/search-client";
import { INITIAL_SEARCH, searchReducer, visibleSearch, type VisibleSearch } from "@/lib/search/state";

export type SearchControls = VisibleSearch & {
  /** 開始搜尋（取消進行中的那一次）。tag 是圖形的 key：結果只在圖形還是同一個時顯示。 */
  start: (request: SearchRequest, tag: string) => void;
  /** 取消進行中的搜尋並清掉結果，回到 idle。 */
  cancel: () => void;
};

/**
 * 自動搜尋（spec §4.6）：一次最多一個搜尋，SSE 事件更新狀態（排隊位置、階段與進度、即時預覽、結果）。
 * - 取消、卸載時 abort 請求：代理會取消對引擎的請求，引擎偵測到斷線就停止搜尋。
 * - currentTag（目前圖形的 key）和搜尋時的 tag 不同：候選的擺放屬於舊圖形，不顯示；進行中的就取消。
 * - 搜尋因引擎離線失敗時呼叫 onEngineDown（和即時吸附一樣顯示橫幅、停用吸附）。
 */
export function useSearch(currentTag: string | null, onEngineDown: () => void): SearchControls {
  const [state, dispatch] = useReducer(searchReducer, INITIAL_SEARCH);
  // 進行中那次搜尋的 AbortController（不是 DOM 節點：卸載時要 abort 的就是當下這一個）
  const controllerRef = useRef<AbortController | null>(null);
  const runRef = useRef(0);
  const onEngineDownRef = useRef(onEngineDown);

  useEffect(() => {
    onEngineDownRef.current = onEngineDown;
  });

  const start = useCallback(
    (request: SearchRequest, tag: string) => {
      controllerRef.current?.abort();
      const controller = new AbortController();
      controllerRef.current = controller;
      const run = ++runRef.current;
      dispatch({ type: "start", run, tag });
      void fetchSearch(request, controller.signal, (update) => dispatch({ type: "update", run, update })).then(
        (outcome) => {
          if (controllerRef.current === controller) controllerRef.current = null;
          dispatch({ type: "finish", run, outcome });
          if (outcome.kind === "failed" && outcome.error.engineDown) onEngineDownRef.current();
        },
      );
    },
    [],
  );

  const cancel = useCallback(() => {
    controllerRef.current?.abort();
    controllerRef.current = null;
    dispatch({ type: "cancel" });
  }, []);

  // 圖形換了：進行中的搜尋屬於舊圖形，取消它（結果回來是 aborted，狀態回到 idle）
  const stale = state.tag !== null && state.tag !== currentTag;
  useEffect(() => {
    if (stale) controllerRef.current?.abort();
  }, [stale]);

  useEffect(() => () => controllerRef.current?.abort(), []);

  return { ...visibleSearch(state, currentTag), start, cancel };
}
