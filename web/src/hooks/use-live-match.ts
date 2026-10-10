"use client";

import { useEffect, useRef, useState } from "react";
import { fetchMatch, type MatchError } from "@/lib/engine/client";
import type { MatchRequest, MatchResponse } from "@/lib/engine/contract";
import { createMatchScheduler, type MatchScheduler } from "@/lib/match/scheduler";

export type LiveMatch = { response: MatchResponse | null; error: MatchError | null; pending: boolean };

/**
 * 即時吸附：request 變了就排程送出（拖曳中每 300ms 一次、放開立刻送）。
 * enabled 為 false（引擎不可用）時不送。最新的結果是錯誤時清掉舊路線，避免顯示過時的結果。
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
      onPendingChange: (pending) => setState((s) => ({ ...s, pending })),
    });
    schedulerRef.current = scheduler;
    return () => scheduler.dispose();
  }, []);

  useEffect(() => {
    if (request && enabled) schedulerRef.current?.schedule(request, dragging);
  }, [request, dragging, enabled]);

  return state;
}
