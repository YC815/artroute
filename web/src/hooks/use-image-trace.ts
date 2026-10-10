"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { TraceRequest, TraceResponse } from "@/lib/image/trace.worker";

type TraceResult = Extract<TraceResponse, { ok: true }>;

/**
 * 圖片描邊 worker 的包裝。只把「最新一次請求」的結果交給 onResult，舊的丟掉。
 * 回傳 trace（送出請求）、working（處理中）、error（最新一次的錯誤訊息）。
 */
export function useImageTrace(onResult: (result: TraceResult) => void) {
  const workerRef = useRef<Worker | null>(null);
  const latestIdRef = useRef(0);
  const onResultRef = useRef(onResult);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    onResultRef.current = onResult;
  });

  useEffect(() => {
    const worker = new Worker(new URL("../lib/image/trace.worker.ts", import.meta.url), { type: "module" });
    worker.onmessage = (event: MessageEvent<TraceResponse>) => {
      const response = event.data;
      if (response.id !== latestIdRef.current) return;
      setWorking(false);
      setError(response.ok ? null : response.message);
      if (response.ok) onResultRef.current(response);
    };
    // worker 載入失敗或當掉時不會有回應，不處理的話 working 會永遠是 true
    worker.onerror = () => {
      setWorking(false);
      setError("圖片處理失敗，請重新整理頁面再試");
    };
    workerRef.current = worker;
    return () => worker.terminate();
  }, []);

  const trace = useCallback((request: Omit<TraceRequest, "id">) => {
    latestIdRef.current += 1;
    setWorking(true);
    workerRef.current?.postMessage({ ...request, id: latestIdRef.current } satisfies TraceRequest);
  }, []);

  return { trace, working, error };
}
