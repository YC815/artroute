"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { fetchStatus } from "@/lib/engine/client";

export type EngineStatus = {
  /** checking：查詢中；ok：可以吸附；down：引擎連不上或沒有路網，停用吸附。 */
  status: "checking" | "ok" | "down";
  /** 目前路網的 build id，例如 20261010T145928Z。 */
  dataVersion: string | null;
  /** 目前路網有沒有自動搜尋需要的資料（舊版引擎或舊 build 為 false）。查詢中或離線時沿用上一次的值，要搭配 status 判斷。 */
  searchReady: boolean;
  /** 目前路網能不能用描形規劃器（放開後的描形版 quality: "fine"）；舊版引擎或舊 build 為 false，只用 fast。沿用規則同 searchReady。 */
  plannerReady: boolean;
};

/** 開頁時查一次 /api/status；retry 重查；吸附遇到 503 或連不上時呼叫 markDown。 */
export function useEngineStatus(): EngineStatus & { retry: () => void; markDown: () => void } {
  const [state, setState] = useState<EngineStatus>({
    status: "checking",
    dataVersion: null,
    searchReady: false,
    plannerReady: false,
  });
  const [attempt, setAttempt] = useState(0);
  // markDown 的次數：查詢開始後才回報離線，這次查詢的結果可能是離線前的狀態
  const downCountRef = useRef(0);

  useEffect(() => {
    let cancelled = false;
    const downCount = downCountRef.current;
    void fetchStatus().then((info) => {
      // 不能讓過時的結果把 down 翻回 ok、重新啟用吸附；要等使用者按「重試」重新查
      if (cancelled || downCount !== downCountRef.current) return;
      setState(
        info
          ? { status: "ok", ...info }
          : { status: "down", dataVersion: null, searchReady: false, plannerReady: false },
      );
    });
    return () => {
      cancelled = true;
    };
  }, [attempt]);

  const retry = useCallback(() => {
    setState((s) => ({ ...s, status: "checking" }));
    setAttempt((n) => n + 1);
  }, []);

  const markDown = useCallback(() => {
    downCountRef.current += 1;
    setState((s) => ({ ...s, status: "down" }));
  }, []);

  return { ...state, retry, markDown };
}
