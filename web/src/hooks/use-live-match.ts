"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { fetchMatch, type MatchError } from "@/lib/engine/client";
import type { MatchRequest, MatchResponse } from "@/lib/engine/contract";
import { requestKey } from "@/lib/match/request";
import { createMatchScheduler, type MatchScheduler } from "@/lib/match/scheduler";

export type LiveMatch = {
  response: MatchResponse | null;
  error: MatchError | null;
  pending: boolean;
  /** 產生目前 response／error 的請求在送出時帶的 tag（見 useLiveMatch 的 tag 參數）；還沒有結果時是 null。 */
  appliedTag: string | null;
  /** 產生目前 response／error 的請求的 key（requestKey）；還沒有結果時是 null。 */
  appliedKey: string | null;
};

/** useLiveMatch 的回傳值：多一個「目前請求的 key」，和 appliedKey 相等才代表結果是目前這個擺放的。 */
export type LiveMatchResult = LiveMatch & { currentKey: string | null };

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
 * - tag 跟著 request 送出，結果回來時成為 appliedTag（例如圖形的 key）：呼叫端用它判斷結果屬於哪個圖形，
 *   不能用「結果回來時目前是哪個圖形」推斷（圖形換了、新請求還沒送出時，舊結果可能剛好回來）。
 * - appliedKey 是結果所屬請求的 key，currentKey 是目前請求的 key：兩者相等才代表結果是目前這個擺放的路線。
 *   同一個圖形換了擺放位置，tag 不變、key 會變，所以「能不能下載 GPX」要比 key，不能只看 pending（取消會把 pending 清掉）。
 */
export function useLiveMatch(
  request: MatchRequest | null,
  dragging: boolean,
  enabled: boolean,
  onEngineDown: () => void,
  tag: string | null = null,
): LiveMatchResult {
  const [state, setState] = useState<LiveMatch>({
    response: null,
    error: null,
    pending: false,
    appliedTag: null,
    appliedKey: null,
  });
  const schedulerRef = useRef<MatchScheduler | null>(null);
  const onEngineDownRef = useRef(onEngineDown);

  useEffect(() => {
    onEngineDownRef.current = onEngineDown;
  });

  useEffect(() => {
    const scheduler = createMatchScheduler({
      send: (req, signal) => fetchMatch(req, signal),
      onOutcome: (outcome, appliedTag, appliedKey) => {
        if (outcome.ok) {
          setState((s) => ({ ...s, response: outcome.response, error: null, appliedTag, appliedKey }));
          return;
        }
        setState((s) => ({ ...s, response: null, error: outcome.error, appliedTag, appliedKey }));
        if (outcome.error.engineDown) onEngineDownRef.current();
      },
      onPendingChange: (pending) => setState((s) => (s.pending === pending ? s : { ...s, pending })),
    });
    schedulerRef.current = scheduler;
    return () => scheduler.dispose();
  }, []);

  useEffect(() => {
    const scheduler = schedulerRef.current;
    if (request && enabled) scheduler?.schedule(request, dragging, tag);
    else scheduler?.cancel();
  }, [request, dragging, enabled, tag]);

  // 每個 request 序列化一次（最大的請求約 3 ms），拖曳中也是每次 pointermove 一次
  const currentKey = useMemo(() => (request ? requestKey(request) : null), [request]);
  return { ...matchForRequest(state, request), currentKey };
}
