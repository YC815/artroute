import type { MatchOutcome } from "@/lib/engine/client";
import type { MatchRequest } from "@/lib/engine/contract";

/** 拖曳中送出吸附請求的最小間隔（spec §4.2）。 */
export const THROTTLE_MS = 300;

export type SchedulerOptions = {
  send: (request: MatchRequest, signal: AbortSignal) => Promise<MatchOutcome>;
  /** 只會收到「比已套用的更新」且未被取消的結果，舊結果永遠不會覆蓋新結果。 */
  onOutcome: (outcome: MatchOutcome) => void;
  onPendingChange: (pending: boolean) => void;
  intervalMs?: number;
  now?: () => number;
};

export type MatchScheduler = {
  /**
   * 排程送出 request。dragging 為 true 時節流（每 intervalMs 最多一次，送最新的那個），
   * false 時立刻送出。同一個 request 物件成功送過就不重送；失敗的可以再送。
   */
  schedule: (request: MatchRequest, dragging: boolean) => void;
  /** 取消計時器與進行中的請求，之後不再回呼。 */
  dispose: () => void;
};

export function createMatchScheduler(options: SchedulerOptions): MatchScheduler {
  const { send, onOutcome, onPendingChange, intervalMs = THROTTLE_MS, now = Date.now } = options;
  let latest: MatchRequest | null = null;
  let lastSent: MatchRequest | null = null;
  let lastSentAt = -Infinity;
  let timer: ReturnType<typeof setTimeout> | null = null;
  let controller: AbortController | null = null;
  let issued = 0; // 最後送出的序號
  let applied = 0; // 最後套用的序號

  function clearTimer() {
    if (timer !== null) clearTimeout(timer);
    timer = null;
  }

  function apply(seq: number, outcome: MatchOutcome) {
    applied = seq;
    // 最新的請求失敗了：允許同一個 request 重送（例如引擎恢復後重試）
    if (!outcome.ok && seq === issued) lastSent = null;
    onOutcome(outcome);
    onPendingChange(applied !== issued);
  }

  function fire() {
    clearTimer();
    if (latest === null || latest === lastSent) return;
    const request = latest;
    const seq = ++issued;
    lastSent = request;
    lastSentAt = now();
    controller?.abort();
    const current = new AbortController();
    controller = current;
    onPendingChange(true);
    void send(request, current.signal).then((outcome) => {
      // 被取消的結果一律丟掉（fetchMatch 取消時會立刻回「已取消」的錯誤，序號卻可能比已套用的大）；
      // 其餘比已套用的還舊的結果也丟掉（序號單調遞增）。
      if (current.signal.aborted || seq <= applied) return;
      apply(seq, outcome);
    });
  }

  return {
    schedule(request, dragging) {
      latest = request;
      if (!dragging) return fire();
      if (timer !== null) return;
      const wait = lastSentAt + intervalMs - now();
      if (wait <= 0) return fire();
      timer = setTimeout(fire, wait);
    },
    dispose() {
      clearTimer();
      controller?.abort();
      latest = null;
    },
  };
}
