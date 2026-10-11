"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { EditedRoute } from "@/lib/edit/edit-state";
import {
  IDLE_REVERIFY,
  reverifyFor,
  reverifyRoute,
  stateAfterReverify,
  verifyingState,
  type ReverifyState,
} from "@/lib/edit/reverify";
import type { VerifyRequest } from "@/lib/engine/contract";

export type Reverify = {
  /** 對應 currentSig 那條路線的狀態（別條路線的結果不會出現）。 */
  state: ReverifyState;
  /** 把舊路網的路線送去驗證；同時只有一個，再按會取消前一個。 */
  run: (route: EditedRoute, strokes: VerifyRequest["strokes"]) => void;
  /** 取消在途的驗證（打開別的草稿、回到自動吸附）：/verify 會佔住引擎名額到 15 秒，不能放著不管。 */
  cancel: () => void;
};

/**
 * 重新驗證（POST /verify）。通過時呼叫 onVerified(新路線, 送去驗證的路線簽章)；沒過或出錯只更新狀態，路線維持原狀。
 * 卸載時取消在途的請求。
 */
export function useReverify(currentSig: string | null, onVerified: (route: EditedRoute, from: string) => void): Reverify {
  const [state, setState] = useState<ReverifyState>(IDLE_REVERIFY);
  const abortRef = useRef<AbortController | null>(null);
  const onVerifiedRef = useRef(onVerified);

  useEffect(() => {
    onVerifiedRef.current = onVerified;
  });
  useEffect(() => () => abortRef.current?.abort(), []);

  const cancel = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    setState(IDLE_REVERIFY);
  }, []);

  const run = useCallback((route: EditedRoute, strokes: VerifyRequest["strokes"]) => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    const sig = route.routeSig;
    setState(verifyingState(sig));
    void reverifyRoute(route, strokes, controller.signal).then((result) => {
      if (result === null || abortRef.current !== controller) return;
      abortRef.current = null;
      if (result.kind === "ok") onVerifiedRef.current(result.route, sig);
      setState(stateAfterReverify(sig, result));
    });
  }, []);

  return useMemo(() => ({ state: reverifyFor(state, currentSig), run, cancel }), [state, currentSig, run, cancel]);
}
