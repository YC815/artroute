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
  /** 單調遞增的毫秒時鐘；預設 performance.now()，不受系統時間調整影響。 */
  now?: () => number;
};

export type MatchScheduler = {
  /**
   * 排程送出 request。dragging 為 true 時節流（每 intervalMs 最多一次，送最新的那個），
   * false 時立刻送出。內容與上一個送出的相同（同一個物件或結構相等）且沒有失敗就不重送；失敗的可以再送。
   */
  schedule: (request: MatchRequest, dragging: boolean) => void;
  /**
   * 暫停：取消排定的節流計時器與進行中的請求（結果丟棄），pending 回到 false。
   * 排程器之後仍可繼續使用；已成功套用的請求不會因此被當成沒送過。
   */
  cancel: () => void;
  /** 取消計時器與進行中的請求，之後不再回呼。 */
  dispose: () => void;
};

/** send 意外 reject 時當成這個失敗結果處理（fetchMatch 不會 reject，這只是防禦）。 */
const UNEXPECTED_FAILURE: MatchOutcome = {
  ok: false,
  error: { status: 0, message: "吸附失敗（未預期的錯誤）", warnings: [], engineDown: false },
};

export function createMatchScheduler(options: SchedulerOptions): MatchScheduler {
  const { send, onOutcome, onPendingChange, intervalMs = THROTTLE_MS, now = () => performance.now() } = options;
  let latest: MatchRequest | null = null;
  let lastSent: MatchRequest | null = null; // 同一個物件的快速判斷
  let lastSentKey: string | null = null; // 結構相等的判斷（呼叫端每次 render 都重建 request 時也不會重送）
  let lastSentAt = -Infinity;
  let timer: ReturnType<typeof setTimeout> | null = null;
  let controller: AbortController | null = null;
  let issued = 0; // 最後送出的序號
  let applied = 0; // 最後套用的序號

  function clearTimer() {
    if (timer !== null) clearTimeout(timer);
    timer = null;
  }

  function forgetLastSent() {
    lastSent = null;
    lastSentKey = null;
  }

  function apply(seq: number, outcome: MatchOutcome) {
    applied = seq;
    // 最新的請求失敗了：允許同一個 request 重送（例如引擎恢復後重試）
    if (!outcome.ok && seq === issued) forgetLastSent();
    onOutcome(outcome);
    onPendingChange(applied !== issued);
  }

  function fire() {
    clearTimer();
    if (latest === null || latest === lastSent) return;
    const request = latest;
    const key = JSON.stringify(request);
    if (key === lastSentKey) {
      lastSent = request;
      return;
    }
    const seq = ++issued;
    lastSent = request;
    lastSentKey = key;
    lastSentAt = now();
    controller?.abort();
    const current = new AbortController();
    controller = current;
    onPendingChange(true);
    void send(request, current.signal)
      .catch(() => UNEXPECTED_FAILURE)
      .then((outcome) => {
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
      // 夾在 [0, intervalMs]：時鐘倒退時 wait 會變得很大，不能因此卡住
      const wait = Math.min(Math.max(lastSentAt + intervalMs - now(), 0), intervalMs);
      if (wait === 0) return fire();
      timer = setTimeout(fire, wait);
    },
    cancel() {
      clearTimer();
      controller?.abort();
      latest = null;
      // 進行中的請求結果會被丟棄，等於沒送成：之後再排程同一個請求必須能重送
      if (applied !== issued) forgetLastSent();
      onPendingChange(false);
    },
    dispose() {
      clearTimer();
      controller?.abort();
      latest = null;
    },
  };
}
