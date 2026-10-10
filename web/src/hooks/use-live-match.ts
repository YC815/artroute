"use client";

import { useEffect, useRef, useState } from "react";
import { fetchMatch, type MatchError } from "@/lib/engine/client";
import type { MatchRequest, MatchResponse } from "@/lib/engine/contract";
import { createMatchScheduler, type MatchScheduler } from "@/lib/match/scheduler";

export type LiveMatch = { response: MatchResponse | null; error: MatchError | null; pending: boolean };

/** request 為 null（輸入被清空、圖形不合法）時不能再顯示上一次的路線或錯誤。 */
export function matchForRequest(state: LiveMatch, request: MatchRequest | null): LiveMatch {
  return request ? state : { ...state, response: null, error: null };
}

/**
 * 即時吸附：request 變了就排程送出（拖曳中每 300ms 一次、放開立刻送）。
 *
 * - request 必須用 useMemo 穩定住；排程器雖會略過內容相同的請求，每次 render 都重建仍白白增加比對成本。
 * - request 為 null 或 enabled 為 false（引擎不可用）時不送，並取消已排定與進行中的請求；
 *   request 為 null 時回傳的 response、error 都是 null，不會留著舊路線。
 * - 最新的結果是錯誤時清掉舊路線，避免顯示過時的結果。
 * - 引擎離線橫幅請看 useEngineStatus().status，不要用這裡的 error 判斷。
 */
export function useLiveMatch(
  request: MatchRequest | null,
  dragging: boolean,
  enabled: boolean,
  onEngineDown: () => void,
): LiveMatch {
  const [state, setState] = useState<LiveMatch>({ response: null, error: null, pending: false });
  const schedulerRef = useRef<MatchScheduler | null>(null);
  const onEngineDownRef = useRef(onEngineDown);

  useEffect(() => {
    onEngineDownRef.current = onEngineDown;
  });

  useEffect(() => {
    const scheduler = createMatchScheduler({
      send: (req, signal) => fetchMatch(req, signal),
      onOutcome: (outcome) => {
        if (outcome.ok) {
          setState((s) => ({ ...s, response: outcome.response, error: null }));
          return;
        }
        setState((s) => ({ ...s, response: null, error: outcome.error }));
        if (outcome.error.engineDown) onEngineDownRef.current();
      },
      onPendingChange: (pending) => setState((s) => (s.pending === pending ? s : { ...s, pending })),
    });
    schedulerRef.current = scheduler;
    return () => scheduler.dispose();
  }, []);

  useEffect(() => {
    const scheduler = schedulerRef.current;
    if (request && enabled) scheduler?.schedule(request, dragging);
    else scheduler?.cancel();
  }, [request, dragging, enabled]);

  return matchForRequest(state, request);
}
