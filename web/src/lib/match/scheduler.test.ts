import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { MatchOutcome } from "@/lib/engine/client";
import type { MatchRequest } from "@/lib/engine/contract";
import { SAMPLE_MATCH_RESPONSE } from "@/lib/engine/fixtures";
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
    await vi.waitFor(() => expect(onOutcome).toHaveBeenCalledWith(OK));
  });

  it("拖曳中每 300ms 最多送一次，送的是最新的請求", async () => {
    const { scheduler, calls } = setup();
    scheduler.schedule(req(1), true); // 第一次立刻送（leading）
    expect(calls).toHaveLength(1);
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
    await vi.advanceTimersByTimeAsync(50);
    scheduler.schedule(req(2), true);
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
    expect(onOutcome).toHaveBeenLastCalledWith(newer);
  });

  it("依序回來的結果都會套用，以最後一個為準", async () => {
    const { scheduler, calls, onOutcome } = setup();
    scheduler.schedule(req(1), false);
    calls[0].resolve(OK);
    await vi.waitFor(() => expect(onOutcome).toHaveBeenCalledTimes(1));
    scheduler.schedule(req(2), false);
    calls[1].resolve(FAIL);
    await vi.waitFor(() => expect(onOutcome).toHaveBeenCalledTimes(2));
    expect(onOutcome).toHaveBeenLastCalledWith(FAIL);
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
    expect(onOutcome).toHaveBeenLastCalledWith(OK);
  });

  it("cancel：引擎在 trailing 計時器排定後回 503，取消後不會再送出（停用吸附）", async () => {
    const { scheduler, calls, onOutcome, onPendingChange } = setup();
    scheduler.schedule(req(1), true); // t=0 送出 R1
    await vi.advanceTimersByTimeAsync(100);
    scheduler.schedule(req(2), true); // 排定 t=300 的 trailing
    await vi.advanceTimersByTimeAsync(50);
    calls[0].resolve(FAIL); // t=150：R1 回 503
    await vi.waitFor(() => expect(onOutcome).toHaveBeenCalledWith(FAIL));
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
    clock = 0; // 時鐘倒退：未夾限時 wait 會是 1_300_000 ms
    scheduler.schedule(req(2), true);
    await vi.advanceTimersByTimeAsync(299);
    expect(calls).toHaveLength(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(calls).toHaveLength(2);
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
