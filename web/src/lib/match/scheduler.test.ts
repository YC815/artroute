import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { MatchOutcome } from "@/lib/engine/client";
import type { MatchRequest } from "@/lib/engine/contract";
import { SAMPLE_MATCH_RESPONSE } from "@/lib/engine/fixtures";
import { createMatchScheduler } from "@/lib/match/scheduler";

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

function setup() {
  const { send, calls } = controllableSend();
  const onOutcome = vi.fn();
  const onPendingChange = vi.fn();
  const scheduler = createMatchScheduler({ send, onOutcome, onPendingChange });
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
    await vi.advanceTimersByTimeAsync(1000);
    expect(calls).toHaveLength(2);
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
});
