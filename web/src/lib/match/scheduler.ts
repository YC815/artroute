import type { MatchOutcome } from "@/lib/engine/client";
import type { MatchRequest } from "@/lib/engine/contract";
import { requestKey } from "@/lib/match/request";

/** 拖曳中兩次送出之間的最小間隔（spec §4.2），從上一筆送出的時間起算。 */
export const THROTTLE_MS = 300;

export type SchedulerOptions = {
  send: (request: MatchRequest, signal: AbortSignal) => Promise<MatchOutcome>;
  /**
   * 只會收到「比已套用的更新」且未被取消的結果，舊結果永遠不會覆蓋新結果。
   * tag 是產生這個結果的請求在 schedule 時帶的標記（送出當下就綁定，之後排定的新 tag 不會套到舊結果上）。
   * key 是產生這個結果的請求的 requestKey：和「目前請求的 key」相等，結果才是目前這個擺放的路線。
   */
  onOutcome: (outcome: MatchOutcome, tag: string | null, key: string) => void;
  /**
   * 有請求排定（節流計時器）、進行中、或拖曳中等在途那筆回來後要補送時為 true，結果套用或取消後為 false。
   * 值不變時不呼叫。
   */
  onPendingChange: (pending: boolean) => void;
  intervalMs?: number;
  /** 單調遞增的毫秒時鐘；預設 performance.now()，不受系統時間調整影響。 */
  now?: () => number;
};

export type MatchScheduler = {
  /**
   * 排程送出 request。
   * - dragging 為 true（拖曳中）：最多一筆在途，不 abort 在途的請求。沒有在途時節流（兩次送出至少相隔 intervalMs，
   *   送最新的那個）；有在途時只記住最新的，等在途那筆回來（成功或失敗）後，若它和剛回來的那筆內容不同才補送，
   *   補送同樣遵守 intervalMs。在途很慢（超過 intervalMs）時，使用者整段拖曳也看得到每筆回來的結果。
   * - dragging 為 false（放開、鍵盤微調、改文字等）：立刻送出，並 abort 在途的請求（最終位置優先）。
   * 內容與上一個送出的相同（同一個物件或結構相等）且沒有失敗就不重送；失敗的可以再送。
   * 在途那筆失敗、而最新的和它相同時不會自動重送（不對壞掉的引擎無限重試），要下一次 schedule 才重送。
   * tag 會跟著這個請求，結果回來時交給 onOutcome（例如標記請求屬於哪個圖形）。
   */
  schedule: (request: MatchRequest, dragging: boolean, tag?: string | null) => void;
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
  let latestTag: string | null = null;
  let lastSent: MatchRequest | null = null; // 同一個物件的快速判斷
  let lastSentKey: string | null = null; // 結構相等的判斷（呼叫端每次 render 都重建 request 時也不會重送）
  let lastSentAt = -Infinity;
  let timer: ReturnType<typeof setTimeout> | null = null;
  let controller: AbortController | null = null;
  let issued = 0; // 最後送出的序號
  let applied = 0; // 最後套用（或取消）的序號
  let pending = false; // 最後通知的 pending

  function clearTimer() {
    if (timer !== null) clearTimeout(timer);
    timer = null;
  }

  function forgetLastSent() {
    lastSent = null;
    lastSentKey = null;
  }

  /** 進行中：已送出、結果還沒套用也沒被取消。 */
  function inFlight() {
    return applied !== issued;
  }

  /**
   * 排定的計時器或進行中的請求都算 pending：拖曳中「吸附中…」不會在兩次送出之間閃掉。
   * 拖曳中等在途完成後要補送的 latest，在補送之前在途那筆本身就讓 pending 維持 true；
   * apply 在判斷完要不要補送之後才呼叫 syncPending，所以兩筆之間 pending 也不會閃。
   */
  function syncPending() {
    const next = timer !== null || inFlight();
    if (next === pending) return;
    pending = next;
    onPendingChange(next);
  }

  function apply(seq: number, tag: string | null, key: string, outcome: MatchOutcome) {
    applied = seq;
    // 最新的請求失敗了：允許同一個 request 重送（例如引擎恢復後重試）
    if (!outcome.ok && seq === issued) forgetLastSent();
    onOutcome(outcome, tag, key);
    // 拖曳中被擋下的 latest：和剛回來的這筆內容不同才補送。比的是這筆的 key 而不是 lastSentKey：
    // 失敗時 lastSentKey 已被清掉，拿它比會把「剛失敗的同一個請求」當成新請求而無限重送。
    if (latest !== null && requestKey(latest) !== key) arm();
    syncPending();
  }

  /**
   * 拖曳中的送出時機。已排定計時器就交給它；有請求在途就不動（不 abort），apply 在它回來時會再呼叫這裡。
   * 否則距離上一筆送出滿 intervalMs 就立刻送，不滿就排計時器補足。
   */
  function arm() {
    if (timer !== null || inFlight()) return;
    // 夾在 [0, intervalMs]：時鐘倒退時 wait 會變得很大，不能因此卡住
    const wait = Math.min(Math.max(lastSentAt + intervalMs - now(), 0), intervalMs);
    if (wait === 0) sendLatest();
    else timer = setTimeout(fireThrottled, wait);
  }

  /** 節流計時器到期。arm 只在沒有在途時排計時器，這裡再擋一次：萬一有在途，也只能等它回來，不能 abort 它。 */
  function fireThrottled() {
    timer = null;
    if (!inFlight()) sendLatest();
    syncPending();
  }

  /** 送出 latest（abort 在途的請求）；與上一個送出的相同就不送。拖曳中的呼叫端必須先確認沒有在途。 */
  function sendLatest() {
    if (latest === null || latest === lastSent) return;
    const request = latest;
    const tag = latestTag;
    const key = requestKey(request);
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
    void send(request, current.signal)
      .catch(() => UNEXPECTED_FAILURE)
      .then((outcome) => {
        // 被取消的結果一律丟掉（fetchMatch 取消時會立刻回「已取消」的錯誤，序號卻可能比已套用的大）；
        // 其餘比已套用的還舊的結果也丟掉（序號單調遞增）。
        if (current.signal.aborted || seq <= applied) return;
        apply(seq, tag, key, outcome);
      });
  }

  return {
    schedule(request, dragging, tag = null) {
      latest = request;
      latestTag = tag;
      if (dragging) {
        arm();
      } else {
        // 放開：最終位置優先。取消節流計時器、abort 在途的請求，立刻送
        clearTimer();
        sendLatest();
      }
      syncPending();
    },
    cancel() {
      clearTimer();
      controller?.abort();
      latest = null;
      // 進行中的請求結果會被丟棄，等於沒送成：之後再排程同一個請求必須能重送
      if (applied !== issued) forgetLastSent();
      // 被取消的請求視同已了結（結果一律丟棄），pending 才會回到 false
      applied = issued;
      syncPending();
    },
    dispose() {
      clearTimer();
      controller?.abort();
      latest = null;
    },
  };
}
