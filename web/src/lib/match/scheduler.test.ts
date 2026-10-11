import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { MatchOutcome } from "@/lib/engine/client";
import type { MatchRequest } from "@/lib/engine/contract";
import { SAMPLE_MATCH_RESPONSE } from "@/lib/engine/fixtures";
import { requestKey } from "@/lib/match/request";
import { createMatchScheduler, type SchedulerOptions } from "@/lib/match/scheduler";

const OK: MatchOutcome = { ok: true, response: SAMPLE_MATCH_RESPONSE };
const FAIL: MatchOutcome = {
  ok: false,
  error: { status: 503, message: "引擎暫時無法服務", warnings: [], engineDown: true },
};

function req(lon: number): MatchRequest {
  return { strokes: [{ points: [[lon, 25], [lon + 0.01, 25]], closed: false }] };
}

/** 可以手動決定何時、以什麼結果完成的 send；故意不理會 abort，用來測序號保護。 */
function controllableSend() {
  const calls: { request: MatchRequest; signal: AbortSignal; resolve: (o: MatchOutcome) => void }[] = [];
  const send = vi.fn(
    (request: MatchRequest, signal: AbortSignal) =>
      new Promise<MatchOutcome>((resolve) => calls.push({ request, signal, resolve })),
  );
  return { send, calls };
}

function setup(extra: Partial<SchedulerOptions> = {}) {
  const { send, calls } = controllableSend();
  const onOutcome = vi.fn();
  const onPendingChange = vi.fn();
  const scheduler = createMatchScheduler({ send, onOutcome, onPendingChange, ...extra });
  return { scheduler, send, calls, onOutcome, onPendingChange };
}

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("createMatchScheduler", () => {
  it("沒在拖曳時立刻送出，結果交給 onOutcome", async () => {
    const { scheduler, calls, onOutcome } = setup();
    const r = req(121.5);
    scheduler.schedule(r, false);
    expect(calls).toHaveLength(1);
    expect(calls[0].request).toBe(r);
    calls[0].resolve(OK);
    await vi.waitFor(() => expect(onOutcome).toHaveBeenCalledWith(OK, null, expect.any(String)));
  });

  it("拖曳中每 300ms 最多送一次，送的是最新的請求（前一筆已回來，節流間隔才是唯一的限制）", async () => {
    const { scheduler, calls } = setup();
    scheduler.schedule(req(1), true); // 第一次立刻送（leading）
    expect(calls).toHaveLength(1);
    calls[0].resolve(OK); // 前一筆很快就回來：沒有在途的請求擋著，這裡只測節流間隔
    await vi.advanceTimersByTimeAsync(100);
    scheduler.schedule(req(2), true);
    await vi.advanceTimersByTimeAsync(100);
    const r3 = req(3);
    scheduler.schedule(r3, true);
    expect(calls).toHaveLength(1);
    await vi.advanceTimersByTimeAsync(99); // t = 299
    expect(calls).toHaveLength(1);
    await vi.advanceTimersByTimeAsync(1); // t = 300
    expect(calls).toHaveLength(2);
    expect(calls[1].request).toBe(r3);
  });

  it("放開時立刻送出，並取消排定的節流計時器", async () => {
    const { scheduler, calls } = setup();
    scheduler.schedule(req(1), true);
    calls[0].resolve(OK); // 前一筆已回來，下一筆才會排節流計時器（在途時不排計時器，見「拖曳中在途」那組）
    await vi.advanceTimersByTimeAsync(50);
    scheduler.schedule(req(2), true);
    expect(vi.getTimerCount()).toBe(1); // 放開前確實排了節流計時器，下面的 0 才不是空洞斷言
    const released = req(3);
    scheduler.schedule(released, false);
    expect(calls).toHaveLength(2);
    expect(calls[1].request).toBe(released);
    expect(vi.getTimerCount()).toBe(0); // 沒有殘留的節流計時器
    await vi.advanceTimersByTimeAsync(1000);
    expect(calls).toHaveLength(2);
  });

  it("拖曳中由節流計時器送出 M 後，放開時同一個 M 不重送", async () => {
    const { scheduler, calls } = setup();
    scheduler.schedule(req(1), true); // leading
    calls[0].resolve(OK); // 前一筆已回來，節流計時器才會送 m
    await vi.advanceTimersByTimeAsync(100);
    const m = req(2);
    scheduler.schedule(m, true);
    await vi.advanceTimersByTimeAsync(200); // t = 300：trailing 送出 m
    expect(calls).toHaveLength(2);
    expect(calls[1].request).toBe(m);
    scheduler.schedule(m, false); // 放開
    expect(calls).toHaveLength(2);
    expect(vi.getTimerCount()).toBe(0);
    expect(calls[1].signal.aborted).toBe(false); // 進行中的 m 沒被取消
  });

  it("送出新請求時取消舊請求", () => {
    const { scheduler, calls } = setup();
    scheduler.schedule(req(1), false);
    scheduler.schedule(req(2), false);
    expect(calls[0].signal.aborted).toBe(true);
    expect(calls[1].signal.aborted).toBe(false);
  });

  it("舊請求的結果晚到也不會覆蓋新結果（即使 send 不理會 abort）", async () => {
    const { scheduler, calls, onOutcome } = setup();
    scheduler.schedule(req(1), false);
    scheduler.schedule(req(2), false);
    const newer: MatchOutcome = { ok: true, response: { ...SAMPLE_MATCH_RESPONSE, data_version: "newer" } };
    calls[1].resolve(newer);
    await vi.waitFor(() => expect(onOutcome).toHaveBeenCalledTimes(1));
    calls[0].resolve(OK);
    await vi.advanceTimersByTimeAsync(0);
    expect(onOutcome).toHaveBeenCalledTimes(1);
    expect(onOutcome).toHaveBeenLastCalledWith(newer, null, expect.any(String));
  });

  it("依序回來的結果都會套用，以最後一個為準", async () => {
    const { scheduler, calls, onOutcome } = setup();
    scheduler.schedule(req(1), false);
    calls[0].resolve(OK);
    await vi.waitFor(() => expect(onOutcome).toHaveBeenCalledTimes(1));
    scheduler.schedule(req(2), false);
    calls[1].resolve(FAIL);
    await vi.waitFor(() => expect(onOutcome).toHaveBeenCalledTimes(2));
    expect(onOutcome).toHaveBeenLastCalledWith(FAIL, null, expect.any(String));
  });

  it("同一個 request 物件成功後不重送；失敗後可以重送", async () => {
    const { scheduler, calls, onOutcome } = setup();
    const r = req(1);
    scheduler.schedule(r, false);
    calls[0].resolve(OK);
    await vi.waitFor(() => expect(onOutcome).toHaveBeenCalledTimes(1));
    scheduler.schedule(r, false);
    expect(calls).toHaveLength(1);

    const r2 = req(2);
    scheduler.schedule(r2, false);
    calls[1].resolve(FAIL);
    await vi.waitFor(() => expect(onOutcome).toHaveBeenCalledTimes(2));
    scheduler.schedule(r2, false);
    expect(calls).toHaveLength(3);
  });

  it("非離線錯誤（例如 504）之後，再排程同一個 request 會重送一次，成功後不再重送（「重新吸附」靠這個）", async () => {
    const { scheduler, calls, onOutcome } = setup();
    const timeout: MatchOutcome = {
      ok: false,
      error: { status: 504, message: "吸附超過時間上限", warnings: [], engineDown: false },
    };
    const r = req(1);
    scheduler.schedule(r, false);
    calls[0].resolve(timeout);
    await vi.waitFor(() => expect(onOutcome).toHaveBeenCalledTimes(1));

    scheduler.schedule(r, false); // 重新吸附
    expect(calls).toHaveLength(2);
    expect(calls[1].request).toBe(r);
    calls[1].resolve(OK);
    await vi.waitFor(() => expect(onOutcome).toHaveBeenCalledTimes(2));
    expect(onOutcome).toHaveBeenLastCalledWith(OK, null, requestKey(r));

    scheduler.schedule(r, false); // 已成功：再按也不重送
    expect(calls).toHaveLength(2);
  });

  it("pending：送出時 true，最新的結果套用後 false", async () => {
    const { scheduler, calls, onPendingChange } = setup();
    scheduler.schedule(req(1), false);
    expect(onPendingChange).toHaveBeenLastCalledWith(true);
    calls[0].resolve(OK);
    await vi.waitFor(() => expect(onPendingChange).toHaveBeenLastCalledWith(false));
  });

  it("dispose 後取消進行中的請求，也不再回呼", async () => {
    const { scheduler, calls, onOutcome } = setup();
    scheduler.schedule(req(1), true);
    scheduler.schedule(req(2), true);
    scheduler.dispose();
    expect(calls[0].signal.aborted).toBe(true);
    calls[0].resolve(OK);
    await vi.advanceTimersByTimeAsync(1000);
    expect(onOutcome).not.toHaveBeenCalled();
    expect(calls).toHaveLength(1);
  });

  it("已取消的請求先以錯誤結果回來（fetchMatch 的行為），也不會被套用；較新的結果仍會套用", async () => {
    const { scheduler, calls, onOutcome } = setup();
    scheduler.schedule(req(1), false);
    scheduler.schedule(req(2), false);
    expect(calls[0].signal.aborted).toBe(true);
    const cancelled: MatchOutcome = {
      ok: false,
      error: { status: 0, message: "已取消", warnings: [], engineDown: false },
    };
    calls[0].resolve(cancelled); // 先於新請求回來，序號 1 < 2 但 signal 已 aborted
    await vi.advanceTimersByTimeAsync(0);
    expect(onOutcome).not.toHaveBeenCalled();
    calls[1].resolve(OK);
    await vi.waitFor(() => expect(onOutcome).toHaveBeenCalledTimes(1));
    expect(onOutcome).toHaveBeenLastCalledWith(OK, null, expect.any(String));
  });

  it("cancel：在途那筆回 503、補送的計時器排定後取消，不會再送出（停用吸附）", async () => {
    const { scheduler, calls, onOutcome, onPendingChange } = setup();
    scheduler.schedule(req(1), true); // t=0 送出 R1
    await vi.advanceTimersByTimeAsync(100);
    scheduler.schedule(req(2), true); // R1 在途：req(2) 先記著
    await vi.advanceTimersByTimeAsync(50);
    calls[0].resolve(FAIL); // t=150：R1 回 503，補送 req(2) 的 trailing 計時器排在 t=300
    await vi.waitFor(() => expect(onOutcome).toHaveBeenCalledWith(FAIL, null, expect.any(String)));
    scheduler.cancel(); // 呼叫端因此停用吸附
    expect(onPendingChange).toHaveBeenLastCalledWith(false);
    expect(vi.getTimerCount()).toBe(0);
    await vi.advanceTimersByTimeAsync(1000);
    expect(calls).toHaveLength(1);
  });

  it("cancel：取消進行中的請求，其結果被丟棄，pending 為 false", async () => {
    const { scheduler, calls, onOutcome, onPendingChange } = setup();
    scheduler.schedule(req(1), false);
    expect(onPendingChange).toHaveBeenLastCalledWith(true);
    scheduler.cancel();
    expect(calls[0].signal.aborted).toBe(true);
    expect(onPendingChange).toHaveBeenLastCalledWith(false);
    calls[0].resolve(OK);
    await vi.advanceTimersByTimeAsync(0);
    expect(onOutcome).not.toHaveBeenCalled();
    expect(onPendingChange).toHaveBeenLastCalledWith(false);
  });

  it("cancel 之後排程器仍可使用：被取消而沒有結果的請求可以重送，已成功套用的不重送", async () => {
    const { scheduler, calls, onOutcome } = setup();
    const r1 = req(1);
    scheduler.schedule(r1, false);
    scheduler.cancel(); // r1 還沒有結果
    scheduler.schedule(r1, false);
    expect(calls).toHaveLength(2);
    calls[1].resolve(OK);
    await vi.waitFor(() => expect(onOutcome).toHaveBeenCalledTimes(1));
    scheduler.cancel(); // r1 已套用
    scheduler.schedule(r1, false);
    expect(calls).toHaveLength(2);
    const r2 = req(2);
    scheduler.schedule(r2, false);
    expect(calls).toHaveLength(3);
    calls[2].resolve(OK);
    await vi.waitFor(() => expect(onOutcome).toHaveBeenCalledTimes(2)); // 序號沒被 cancel 弄亂
  });

  it("內容相同的新 request 物件視同同一個請求：成功後不重送，失敗後可重送", async () => {
    const { scheduler, calls, onOutcome } = setup();
    scheduler.schedule(req(1), false);
    scheduler.schedule(req(1), false); // 不同物件、同內容，進行中也不重送
    expect(calls).toHaveLength(1);
    expect(calls[0].signal.aborted).toBe(false);
    calls[0].resolve(OK);
    await vi.waitFor(() => expect(onOutcome).toHaveBeenCalledTimes(1));
    scheduler.schedule(req(1), false);
    expect(calls).toHaveLength(1);

    scheduler.schedule(req(2), false);
    calls[1].resolve(FAIL);
    await vi.waitFor(() => expect(onOutcome).toHaveBeenCalledTimes(2));
    scheduler.schedule(req(2), false);
    expect(calls).toHaveLength(3);
  });

  it("拖曳中內容相同的新 request 物件不會觸發節流送出", async () => {
    const { scheduler, calls } = setup();
    scheduler.schedule(req(1), true);
    await vi.advanceTimersByTimeAsync(100);
    scheduler.schedule(req(1), true);
    await vi.advanceTimersByTimeAsync(1000);
    expect(calls).toHaveLength(1);
  });

  it("系統時鐘倒退時，等待時間最多一個 intervalMs", async () => {
    let clock = 1_000_000;
    const { scheduler, calls } = setup({ now: () => clock });
    scheduler.schedule(req(1), true); // lastSentAt = 1_000_000
    calls[0].resolve(OK); // 前一筆已回來，才會排節流計時器
    await vi.advanceTimersByTimeAsync(0);
    clock = 0; // 時鐘倒退：未夾限時 wait 會是 1_300_000 ms
    scheduler.schedule(req(2), true);
    await vi.advanceTimersByTimeAsync(299);
    expect(calls).toHaveLength(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(calls).toHaveLength(2);
  });

  it("onOutcome 帶回送出時的 tag；沒給 tag 時是 null", async () => {
    const { scheduler, calls, onOutcome } = setup();
    scheduler.schedule(req(1), false, "A");
    calls[0].resolve(OK);
    await vi.waitFor(() => expect(onOutcome).toHaveBeenCalledWith(OK, "A", requestKey(req(1))));
    scheduler.schedule(req(2), false);
    calls[1].resolve(FAIL);
    await vi.waitFor(() => expect(onOutcome).toHaveBeenLastCalledWith(FAIL, null, expect.any(String)));
  });

  it("結果標記的是產生它的請求的 tag：之後才排定的新 tag 不會套到舊結果上", async () => {
    const { scheduler, calls, onOutcome } = setup();
    scheduler.schedule(req(1), true, "K1"); // leading 送出 K1
    await vi.advanceTimersByTimeAsync(100);
    scheduler.schedule(req(2), true, "K2"); // K1 在途：K2 先記著
    const k1: MatchOutcome = { ok: true, response: { ...SAMPLE_MATCH_RESPONSE, data_version: "k1" } };
    calls[0].resolve(k1); // K1 的結果在 K2 送出前回來（t=100），K2 排在 t=300
    await vi.waitFor(() => expect(onOutcome).toHaveBeenCalledTimes(1));
    expect(onOutcome).toHaveBeenLastCalledWith(k1, "K1", requestKey(req(1)));
    await vi.advanceTimersByTimeAsync(200);
    expect(calls).toHaveLength(2);
    calls[1].resolve(OK);
    await vi.waitFor(() => expect(onOutcome).toHaveBeenCalledTimes(2));
    expect(onOutcome).toHaveBeenLastCalledWith(OK, "K2", requestKey(req(2)));
  });

  it("結果帶著產生它的請求的 key（requestKey），之後排定的新請求不會改掉它", async () => {
    const { scheduler, calls, onOutcome } = setup();
    scheduler.schedule(req(1), true); // leading 送出 req(1)
    await vi.advanceTimersByTimeAsync(100);
    scheduler.schedule(req(2), true); // req(1) 在途：req(2) 先記著，之後才送
    calls[0].resolve(OK);
    await vi.waitFor(() => expect(onOutcome).toHaveBeenCalledTimes(1));
    expect(onOutcome.mock.lastCall?.[2]).toBe(requestKey(req(1)));
    expect(onOutcome.mock.lastCall?.[2]).not.toBe(requestKey(req(2)));
  });

  it("內容相同的請求（不同物件）key 相同、不重送；內容不同 key 就不同", () => {
    expect(requestKey(req(1))).toBe(requestKey(req(1)));
    expect(requestKey(req(1))).not.toBe(requestKey(req(2)));
    const { scheduler, calls } = setup();
    scheduler.schedule(req(1), false);
    scheduler.schedule(req(1), false);
    expect(calls).toHaveLength(1);
  });

  it("pending：節流計時器排定時就是 true，前一個結果先回來也不會掉回 false", async () => {
    const { scheduler, calls, onPendingChange } = setup();
    scheduler.schedule(req(1), true); // t=0 leading 送出
    await vi.advanceTimersByTimeAsync(100);
    scheduler.schedule(req(2), true); // R1 在途：req(2) 先記著
    const before = onPendingChange.mock.calls.length;
    calls[0].resolve(OK); // t=100 第一個結果回來，補送的 trailing（t=300）還沒送
    await vi.advanceTimersByTimeAsync(0);
    expect(onPendingChange.mock.calls.slice(before)).not.toContainEqual([false]);
    await vi.advanceTimersByTimeAsync(200); // t=300 送出 trailing
    expect(calls).toHaveLength(2);
    expect(onPendingChange.mock.calls.slice(before)).not.toContainEqual([false]);
    calls[1].resolve(OK);
    await vi.waitFor(() => expect(onPendingChange).toHaveBeenLastCalledWith(false));
  });

  it("pending：沒有進行中的請求、只排了計時器時也是 true", async () => {
    const { scheduler, calls, onPendingChange } = setup();
    scheduler.schedule(req(1), true);
    calls[0].resolve(OK);
    await vi.waitFor(() => expect(onPendingChange).toHaveBeenLastCalledWith(false));
    await vi.advanceTimersByTimeAsync(100);
    scheduler.schedule(req(2), true); // 只排定計時器
    expect(calls).toHaveLength(1);
    expect(onPendingChange).toHaveBeenLastCalledWith(true);
  });

  it("pending：cancel 清掉排定的計時器後回到 false", async () => {
    const { scheduler, calls, onPendingChange } = setup();
    scheduler.schedule(req(1), true);
    calls[0].resolve(OK);
    await vi.advanceTimersByTimeAsync(100);
    scheduler.schedule(req(2), true);
    expect(onPendingChange).toHaveBeenLastCalledWith(true);
    scheduler.cancel();
    expect(onPendingChange).toHaveBeenLastCalledWith(false);
    await vi.advanceTimersByTimeAsync(1000);
    expect(onPendingChange).toHaveBeenLastCalledWith(false);
  });

  it("pending：計時器到了但請求與已套用的相同（不重送）時回到 false", async () => {
    const { scheduler, calls, onPendingChange } = setup();
    scheduler.schedule(req(1), true);
    calls[0].resolve(OK);
    await vi.advanceTimersByTimeAsync(100);
    scheduler.schedule(req(1), true); // 內容相同的新物件
    expect(onPendingChange).toHaveBeenLastCalledWith(true);
    await vi.advanceTimersByTimeAsync(200);
    expect(calls).toHaveLength(1);
    expect(onPendingChange).toHaveBeenLastCalledWith(false);
  });

  it("pending：cancel 掉進行中的請求後，下一個請求的結果套用時 pending 回到 false", async () => {
    const { scheduler, calls, onPendingChange } = setup();
    scheduler.schedule(req(1), false);
    scheduler.cancel();
    expect(onPendingChange).toHaveBeenLastCalledWith(false);
    scheduler.schedule(req(2), false);
    expect(onPendingChange).toHaveBeenLastCalledWith(true);
    calls[1].resolve(OK);
    await vi.waitFor(() => expect(onPendingChange).toHaveBeenLastCalledWith(false));
  });

  describe("拖曳中最多一筆在途", () => {
    it("在途 600ms、每 100ms 拖到新位置：期間不 abort、不送第二筆；回來後立刻送最新位置，中間位置不送", async () => {
      const { scheduler, calls, onOutcome, onPendingChange } = setup();
      const positions = [1, 2, 3, 4, 5, 6].map((lon) => req(lon));
      scheduler.schedule(positions[0], true, "T0"); // t=0 leading
      expect(calls).toHaveLength(1);
      for (let i = 1; i <= 5; i += 1) {
        await vi.advanceTimersByTimeAsync(100); // t = 100 … 500
        scheduler.schedule(positions[i], true, `T${i}`);
        expect(calls).toHaveLength(1);
        expect(calls[0].signal.aborted).toBe(false);
      }
      await vi.advanceTimersByTimeAsync(99); // t = 599：還在途
      expect(calls).toHaveLength(1);
      expect(calls[0].signal.aborted).toBe(false);
      expect(onPendingChange).not.toHaveBeenCalledWith(false); // 「吸附中…」不閃
      expect(vi.getTimerCount()).toBe(0); // 在途時不排計時器，回來時才決定

      await vi.advanceTimersByTimeAsync(1); // t = 600：在途那筆回來
      calls[0].resolve(OK);
      await vi.advanceTimersByTimeAsync(0);
      expect(onOutcome).toHaveBeenCalledTimes(1);
      expect(onOutcome).toHaveBeenLastCalledWith(OK, "T0", requestKey(positions[0]));
      expect(calls).toHaveLength(2); // 已超過 intervalMs，立刻補送
      expect(calls[1].request).toBe(positions[5]);
      expect(calls.map((c) => c.request)).not.toContain(positions[1]);
      expect(calls.map((c) => c.request)).not.toContain(positions[4]);
      expect(calls[1].signal.aborted).toBe(false);
      expect(onPendingChange).not.toHaveBeenCalledWith(false); // 補送期間 pending 一直是 true

      calls[1].resolve(OK);
      await vi.advanceTimersByTimeAsync(0);
      expect(onOutcome).toHaveBeenLastCalledWith(OK, "T5", requestKey(positions[5])); // tag 是補送那筆送出當下的
      expect(onPendingChange).toHaveBeenLastCalledWith(false);
      await vi.advanceTimersByTimeAsync(1000);
      expect(calls).toHaveLength(2);
    });

    it("在途回來時還沒滿 intervalMs：補足到上一筆送出後 300ms 才送，期間 pending 一直是 true", async () => {
      const { scheduler, calls, onPendingChange } = setup();
      scheduler.schedule(req(1), true); // t=0
      await vi.advanceTimersByTimeAsync(50);
      const r2 = req(2);
      scheduler.schedule(r2, true);
      await vi.advanceTimersByTimeAsync(50);
      calls[0].resolve(OK); // t=100 回來
      await vi.advanceTimersByTimeAsync(0);
      expect(calls).toHaveLength(1);
      expect(vi.getTimerCount()).toBe(1);
      await vi.advanceTimersByTimeAsync(199); // t = 299
      expect(calls).toHaveLength(1);
      await vi.advanceTimersByTimeAsync(1); // t = 300
      expect(calls).toHaveLength(2);
      expect(calls[1].request).toBe(r2);
      expect(onPendingChange).not.toHaveBeenCalledWith(false);
    });

    it("拖曳中在途時放開：abort 在途、立刻送最終位置，在途那筆晚到的結果被丟棄", async () => {
      const { scheduler, calls, onOutcome, onPendingChange } = setup();
      scheduler.schedule(req(1), true, "A"); // t=0 在途
      await vi.advanceTimersByTimeAsync(100);
      scheduler.schedule(req(2), true, "A"); // 拖曳中的中間位置，被記著
      expect(calls).toHaveLength(1);
      await vi.advanceTimersByTimeAsync(50);
      const released = req(3);
      scheduler.schedule(released, false, "A"); // 放開
      expect(calls).toHaveLength(2);
      expect(calls[0].signal.aborted).toBe(true);
      expect(calls[1].request).toBe(released);
      expect(calls[1].signal.aborted).toBe(false);
      expect(vi.getTimerCount()).toBe(0);
      expect(onPendingChange).not.toHaveBeenCalledWith(false);

      const stale: MatchOutcome = { ok: true, response: { ...SAMPLE_MATCH_RESPONSE, data_version: "stale" } };
      calls[0].resolve(stale); // 被 abort 的那筆（send 不理會 abort）晚到
      await vi.advanceTimersByTimeAsync(0);
      expect(onOutcome).not.toHaveBeenCalled();
      expect(onPendingChange).not.toHaveBeenCalledWith(false);

      calls[1].resolve(OK);
      await vi.advanceTimersByTimeAsync(0);
      expect(onOutcome).toHaveBeenCalledTimes(1);
      expect(onOutcome).toHaveBeenLastCalledWith(OK, "A", requestKey(released));
      expect(onPendingChange).toHaveBeenLastCalledWith(false);
      await vi.advanceTimersByTimeAsync(1000);
      expect(calls).toHaveLength(2);
    });

    it("放開時的位置就是在途那筆（結構相等）：不 abort、不重送", async () => {
      const { scheduler, calls, onOutcome } = setup();
      scheduler.schedule(req(1), true); // 在途
      await vi.advanceTimersByTimeAsync(100);
      scheduler.schedule(req(2), true); // 拖遠了
      scheduler.schedule(req(1), true); // 又拖回來
      scheduler.schedule(req(1), false); // 在這裡放開
      expect(calls).toHaveLength(1);
      expect(calls[0].signal.aborted).toBe(false);
      calls[0].resolve(OK);
      await vi.waitFor(() => expect(onOutcome).toHaveBeenCalledTimes(1));
      await vi.advanceTimersByTimeAsync(1000);
      expect(calls).toHaveLength(1);
    });

    it("在途回來時 latest 與已送出的結構相等（拖遠又拖回來）：不再送，pending 回到 false", async () => {
      const { scheduler, calls, onOutcome, onPendingChange } = setup();
      scheduler.schedule(req(1), true);
      await vi.advanceTimersByTimeAsync(100);
      scheduler.schedule(req(2), true);
      scheduler.schedule(req(1), true); // 不同物件、同內容
      calls[0].resolve(OK);
      await vi.advanceTimersByTimeAsync(0);
      expect(onOutcome).toHaveBeenCalledTimes(1);
      expect(onPendingChange).toHaveBeenLastCalledWith(false);
      expect(vi.getTimerCount()).toBe(0);
      await vi.advanceTimersByTimeAsync(1000);
      expect(calls).toHaveLength(1);
    });

    it("在途回來是失敗：仍補送不同的 latest；失敗的結果照常套用，pending 全程為 true 直到補送那筆回來", async () => {
      const { scheduler, calls, onOutcome, onPendingChange } = setup();
      scheduler.schedule(req(1), true, "K1");
      await vi.advanceTimersByTimeAsync(100);
      const r2 = req(2);
      scheduler.schedule(r2, true, "K2");
      await vi.advanceTimersByTimeAsync(300); // t = 400
      calls[0].resolve(FAIL);
      await vi.advanceTimersByTimeAsync(0);
      expect(onOutcome).toHaveBeenCalledTimes(1);
      expect(onOutcome).toHaveBeenLastCalledWith(FAIL, "K1", requestKey(req(1)));
      expect(calls).toHaveLength(2);
      expect(calls[1].request).toBe(r2);
      expect(onPendingChange).not.toHaveBeenCalledWith(false);

      calls[1].resolve(OK);
      await vi.advanceTimersByTimeAsync(0);
      expect(onOutcome).toHaveBeenLastCalledWith(OK, "K2", requestKey(r2));
      expect(onPendingChange).toHaveBeenLastCalledWith(false);
    });

    it("在途回來是失敗、latest 與失敗那筆相同：不會自動重送（不能無限重試），之後再排程同一個才重送", async () => {
      const { scheduler, calls, onOutcome, onPendingChange } = setup();
      scheduler.schedule(req(1), true);
      await vi.advanceTimersByTimeAsync(100);
      scheduler.schedule(req(1), true); // 同內容的新物件
      calls[0].resolve(FAIL);
      await vi.advanceTimersByTimeAsync(0);
      expect(onOutcome).toHaveBeenCalledTimes(1);
      expect(onPendingChange).toHaveBeenLastCalledWith(false);
      await vi.advanceTimersByTimeAsync(2000);
      expect(calls).toHaveLength(1);

      scheduler.schedule(req(1), true); // 使用者再動一下（或按重新吸附）
      expect(calls).toHaveLength(2);
    });

    it("拖曳開始時上一筆（放開時送的）還在途：不 abort，等它回來再送拖曳中的位置", async () => {
      const { scheduler, calls, onOutcome } = setup();
      scheduler.schedule(req(1), false); // 例如鍵盤微調送出的
      await vi.advanceTimersByTimeAsync(1000); // 慢的吸附還沒回來
      const dragged = req(2);
      scheduler.schedule(dragged, true);
      expect(calls).toHaveLength(1);
      expect(calls[0].signal.aborted).toBe(false);
      calls[0].resolve(OK);
      await vi.waitFor(() => expect(onOutcome).toHaveBeenCalledTimes(1));
      expect(calls).toHaveLength(2);
      expect(calls[1].request).toBe(dragged);
    });

    it("補送的間隔算法在時鐘倒退時也最多一個 intervalMs", async () => {
      let clock = 1_000_000;
      const { scheduler, calls } = setup({ now: () => clock });
      scheduler.schedule(req(1), true);
      clock = 0; // 在途期間時鐘倒退
      scheduler.schedule(req(2), true);
      calls[0].resolve(OK);
      await vi.advanceTimersByTimeAsync(299);
      expect(calls).toHaveLength(1);
      await vi.advanceTimersByTimeAsync(1);
      expect(calls).toHaveLength(2);
    });

    it("cancel：等待在途完成後補送的 latest 被丟掉，在途那筆回來也不會送出，之後仍可使用", async () => {
      const { scheduler, calls, onOutcome, onPendingChange } = setup();
      scheduler.schedule(req(1), true);
      await vi.advanceTimersByTimeAsync(100);
      scheduler.schedule(req(2), true); // 被記著，等 R1 回來
      scheduler.cancel();
      expect(calls[0].signal.aborted).toBe(true);
      expect(onPendingChange).toHaveBeenLastCalledWith(false);
      calls[0].resolve(OK);
      await vi.advanceTimersByTimeAsync(1000);
      expect(onOutcome).not.toHaveBeenCalled();
      expect(calls).toHaveLength(1);
      scheduler.schedule(req(2), true); // 排程器仍可使用
      expect(calls).toHaveLength(2);
    });

    it("dispose：清掉已排定的補送計時器", async () => {
      const { scheduler, calls, onOutcome } = setup();
      scheduler.schedule(req(1), true);
      await vi.advanceTimersByTimeAsync(100);
      scheduler.schedule(req(2), true);
      calls[0].resolve(OK); // 回來時補送的計時器排在 t=300
      await vi.advanceTimersByTimeAsync(0);
      expect(vi.getTimerCount()).toBe(1);
      scheduler.dispose();
      expect(vi.getTimerCount()).toBe(0);
      await vi.advanceTimersByTimeAsync(1000);
      expect(calls).toHaveLength(1);
      expect(onOutcome).toHaveBeenCalledTimes(1);
    });

    it("dispose：在途時有待補送的 latest，dispose 後在途那筆回來也不補送、不回呼", async () => {
      const { scheduler, calls, onOutcome, onPendingChange } = setup();
      scheduler.schedule(req(1), true);
      await vi.advanceTimersByTimeAsync(400); // 已超過 intervalMs：回來時會立刻補送，所以這裡能證明是 dispose 擋下的
      scheduler.schedule(req(2), true); // 被記著，等 R1 回來
      expect(calls).toHaveLength(1);
      scheduler.dispose();
      expect(calls[0].signal.aborted).toBe(true);
      calls[0].resolve(OK);
      await vi.advanceTimersByTimeAsync(1000);
      expect(calls).toHaveLength(1);
      expect(onOutcome).not.toHaveBeenCalled();
      expect(vi.getTimerCount()).toBe(0);
      expect(onPendingChange).not.toHaveBeenCalledWith(false); // dispose 之後不再回呼
    });
  });

  it("send 意外 reject：當成失敗套用，pending 不會卡住，且允許重送", async () => {
    const send = vi.fn((): Promise<MatchOutcome> => Promise.reject(new Error("boom")));
    const onOutcome = vi.fn();
    const onPendingChange = vi.fn();
    const scheduler = createMatchScheduler({ send, onOutcome, onPendingChange });
    const r = req(1);
    scheduler.schedule(r, false);
    await vi.waitFor(() => expect(onOutcome).toHaveBeenCalledTimes(1));
    const outcome = onOutcome.mock.calls[0][0] as MatchOutcome;
    expect(outcome.ok).toBe(false);
    expect(onPendingChange).toHaveBeenLastCalledWith(false);
    scheduler.schedule(r, false);
    expect(send).toHaveBeenCalledTimes(2);
  });
});
