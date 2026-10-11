import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { MatchOutcome } from "@/lib/engine/client";
import type { MatchRequest, MatchResponse } from "@/lib/engine/contract";
import { SAMPLE_MATCH_RESPONSE } from "@/lib/engine/fixtures";
import { requestKey } from "@/lib/match/request";
import { createMatchScheduler, type RefineStatus } from "@/lib/match/scheduler";

/**
 * 放開後的描形版（P2.5 spec §2、§6.5）：fast 成功、擺放沒變、planner_ready 時接著送同一個請求加 quality: "fine"。
 * 拖曳中絕不送 fine；新的操作取消在途的 fine；fine 失敗保留 fast 並回報 failed，不自動重送。
 */

const FAST: MatchOutcome = { ok: true, response: { ...SAMPLE_MATCH_RESPONSE, data_version: "fast" } };
const FINE_RESPONSE: MatchResponse = { ...SAMPLE_MATCH_RESPONSE, data_version: "fine" };
const FINE: MatchOutcome = { ok: true, response: FINE_RESPONSE };
const TIMEOUT: MatchOutcome = {
  ok: false,
  error: { status: 504, message: "吸附超過時間上限", warnings: [], engineDown: false },
};

function req(lon: number): MatchRequest {
  return { strokes: [{ points: [[lon, 25], [lon + 0.01, 25]], closed: false }] };
}

type Call = { request: MatchRequest; signal: AbortSignal; resolve: (o: MatchOutcome) => void };

function setup({ ready = true }: { ready?: boolean } = {}) {
  const calls: Call[] = [];
  // 故意不理會 abort：被取消的結果仍可能晚到，測序號／取消保護
  const send = vi.fn(
    (request: MatchRequest, signal: AbortSignal) =>
      new Promise<MatchOutcome>((resolve) => calls.push({ request, signal, resolve })),
  );
  const onOutcome = vi.fn();
  const onPendingChange = vi.fn();
  const onRefined = vi.fn();
  const statuses: RefineStatus[] = [];
  const state = { ready };
  const scheduler = createMatchScheduler({
    send,
    onOutcome,
    onPendingChange,
    refine: {
      enabled: () => state.ready,
      onRefined,
      onStatusChange: (status) => statuses.push(status),
    },
  });
  const fineCalls = () => calls.filter((c) => c.request.quality === "fine");
  const fastCalls = () => calls.filter((c) => c.request.quality === undefined);
  return { scheduler, calls, send, onOutcome, onPendingChange, onRefined, statuses, state, fineCalls, fastCalls };
}

const flush = () => vi.advanceTimersByTimeAsync(0);

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("放開後的描形版（fine）", () => {
  it("fast 成功後接著送同一個請求加 quality: fine；fast 的請求不帶 quality（與以前逐位元相同）", async () => {
    const { scheduler, calls, onOutcome, onRefined, statuses } = setup();
    const r = req(1);
    scheduler.schedule(r, false, "A");
    expect(calls).toHaveLength(1);
    expect(calls[0].request).toBe(r); // fast 送的就是原本的物件
    expect("quality" in calls[0].request).toBe(false);
    calls[0].resolve(FAST);
    await flush();
    expect(onOutcome).toHaveBeenLastCalledWith(FAST, "A", requestKey(r));
    expect(calls).toHaveLength(2);
    expect(calls[1].request).toEqual({ ...r, quality: "fine" });
    expect(statuses).toEqual(["refining"]);
    calls[1].resolve(FINE);
    await flush();
    // fine 的結果帶 fast 請求的 key（= 目前擺放的 key，GPX 判斷照舊），不經過 onOutcome
    expect(onRefined).toHaveBeenCalledExactlyOnceWith(FINE_RESPONSE, "A", requestKey(r));
    expect(onOutcome).toHaveBeenCalledTimes(1);
    expect(statuses).toEqual(["refining", "idle"]);
    await vi.advanceTimersByTimeAsync(5000);
    expect(calls).toHaveLength(2); // 不會再送
  });

  it("fine 不影響 pending（「吸附中…」只代表 fast）", async () => {
    const { scheduler, calls, onPendingChange } = setup();
    scheduler.schedule(req(1), false);
    calls[0].resolve(FAST);
    await flush();
    expect(calls).toHaveLength(2);
    expect(onPendingChange).toHaveBeenLastCalledWith(false);
    expect(onPendingChange).toHaveBeenCalledTimes(2);
    calls[1].resolve(FINE);
    await flush();
    expect(onPendingChange).toHaveBeenCalledTimes(2);
  });

  it("planner_ready 為 false：不送 fine，狀態一直是 idle（不回呼）", async () => {
    const { scheduler, calls, statuses, onRefined } = setup({ ready: false });
    scheduler.schedule(req(1), false);
    calls[0].resolve(FAST);
    await vi.advanceTimersByTimeAsync(1000);
    expect(calls).toHaveLength(1);
    expect(statuses).toEqual([]);
    expect(onRefined).not.toHaveBeenCalled();
  });

  it("沒有給 refine 選項：行為與以前完全相同（不送 fine）", async () => {
    const calls: Call[] = [];
    const scheduler = createMatchScheduler({
      send: (request, signal) => new Promise((resolve) => calls.push({ request, signal, resolve })),
      onOutcome: vi.fn(),
      onPendingChange: vi.fn(),
    });
    scheduler.schedule(req(1), false);
    calls[0].resolve(FAST);
    await vi.advanceTimersByTimeAsync(1000);
    expect(calls).toHaveLength(1);
  });

  it("fast 失敗：不送 fine", async () => {
    const { scheduler, calls, statuses } = setup();
    scheduler.schedule(req(1), false);
    calls[0].resolve(TIMEOUT);
    await vi.advanceTimersByTimeAsync(1000);
    expect(calls).toHaveLength(1);
    expect(statuses).toEqual([]);
  });

  it("拖曳中絕不送 fine：拖曳中送出的 fast 成功也一樣", async () => {
    const { scheduler, calls, fineCalls } = setup();
    for (let i = 0; i < 10; i += 1) {
      scheduler.schedule(req(i), true);
      await vi.advanceTimersByTimeAsync(150);
      for (const call of calls) call.resolve(FAST);
      await flush();
    }
    await vi.advanceTimersByTimeAsync(1000);
    for (const call of calls) call.resolve(FAST);
    await vi.advanceTimersByTimeAsync(1000);
    expect(calls.length).toBeGreaterThan(1);
    expect(fineCalls()).toHaveLength(0);
  });

  it("放開的位置就是拖曳中已成功的那筆（不重送 fast）：放開時立刻送 fine", async () => {
    const { scheduler, calls } = setup();
    const r = req(1);
    scheduler.schedule(r, true);
    calls[0].resolve(FAST);
    await flush();
    expect(calls).toHaveLength(1);
    scheduler.schedule({ ...r }, false); // 放開：內容相同的新物件
    expect(calls).toHaveLength(2);
    expect(calls[1].request).toEqual({ ...r, quality: "fine" });
  });

  it("放開的位置就是拖曳中在途的那筆：不重送 fast，它成功回來後送 fine", async () => {
    const { scheduler, calls } = setup();
    const r = req(1);
    scheduler.schedule(r, true);
    scheduler.schedule(r, false);
    expect(calls).toHaveLength(1);
    expect(calls[0].signal.aborted).toBe(false);
    calls[0].resolve(FAST);
    await flush();
    expect(calls).toHaveLength(2);
    expect(calls[1].request.quality).toBe("fine");
  });

  it("fast 回來時擺放已經變了（拖曳中記住了新位置）：不送 fine，改補送新位置的 fast", async () => {
    const { scheduler, calls, fineCalls } = setup();
    scheduler.schedule(req(1), false);
    scheduler.schedule(req(2), true); // 拖曳開始：在途的不 abort，只記住最新的
    calls[0].resolve(FAST);
    await vi.advanceTimersByTimeAsync(300);
    expect(fineCalls()).toHaveLength(0);
    expect(calls[calls.length - 1].request).toBe(calls[1].request);
    expect(calls[1].request).toEqual(req(2));
  });

  it("fine 在途時開始拖曳到新位置：abort fine、立刻送新位置的 fast，fine 晚到的結果丟棄", async () => {
    const { scheduler, calls, onRefined, statuses } = setup();
    scheduler.schedule(req(1), false);
    calls[0].resolve(FAST);
    await flush();
    const fine = calls[1];
    expect(fine.request.quality).toBe("fine");
    scheduler.schedule(req(2), true);
    expect(fine.signal.aborted).toBe(true);
    expect(statuses).toEqual(["refining", "idle"]);
    // fine 不算拖曳中的「在途」：拖曳的第一筆只受節流間隔（從上一筆 fast 送出起算）限制，不等 fine
    await vi.advanceTimersByTimeAsync(300);
    expect(calls).toHaveLength(3);
    expect(calls[2].request).toEqual(req(2));
    fine.resolve(FINE); // 不理會 abort、晚到
    await flush();
    expect(onRefined).not.toHaveBeenCalled();
  });

  it("fine 在途時放開到新位置（鍵盤微調、套用候選）：abort fine，新位置走 fast → fine", async () => {
    const { scheduler, calls, onRefined } = setup();
    scheduler.schedule(req(1), false);
    calls[0].resolve(FAST);
    await flush();
    const oldFine = calls[1];
    scheduler.schedule(req(2), false);
    expect(oldFine.signal.aborted).toBe(true);
    expect(calls[2].request).toEqual(req(2));
    calls[2].resolve(FAST);
    await flush();
    expect(calls[3].request).toEqual({ ...req(2), quality: "fine" });
    oldFine.resolve(FINE); // 舊擺放的 fine 晚到：丟棄
    await flush();
    expect(onRefined).not.toHaveBeenCalled();
    calls[3].resolve(FINE);
    await flush();
    expect(onRefined).toHaveBeenCalledExactlyOnceWith(FINE_RESPONSE, null, requestKey(req(2)));
  });

  it("fine 的結果晚於新的 fast 回來：丟棄（即使 send 不理會 abort）", async () => {
    const { scheduler, calls, onRefined, onOutcome } = setup();
    scheduler.schedule(req(1), false);
    calls[0].resolve(FAST);
    await flush();
    const fine = calls[1];
    scheduler.schedule(req(2), false);
    calls[2].resolve(FAST);
    await flush();
    fine.resolve(FINE);
    await flush();
    expect(onRefined).not.toHaveBeenCalled();
    expect(onOutcome).toHaveBeenLastCalledWith(FAST, null, requestKey(req(2)));
  });

  it("同一個擺放再排程（重新 render、按下沒移動）：不取消在途的 fine，也不重送", async () => {
    const { scheduler, calls, onRefined } = setup();
    const r = req(1);
    scheduler.schedule(r, false);
    calls[0].resolve(FAST);
    await flush();
    scheduler.schedule({ ...r }, true);
    scheduler.schedule({ ...r }, false);
    expect(calls).toHaveLength(2);
    expect(calls[1].signal.aborted).toBe(false);
    calls[1].resolve(FINE);
    await flush();
    expect(onRefined).toHaveBeenCalledOnce();
    scheduler.schedule({ ...r }, false);
    await vi.advanceTimersByTimeAsync(1000);
    expect(calls).toHaveLength(2);
  });

  it("fine 失敗：保留 fast（不回呼 onOutcome／onRefined），狀態 failed，不自動重送；下一次操作清掉", async () => {
    const { scheduler, calls, onOutcome, onRefined, statuses } = setup();
    const r = req(1);
    scheduler.schedule(r, false);
    calls[0].resolve(FAST);
    await flush();
    calls[1].resolve(TIMEOUT);
    await flush();
    expect(onOutcome).toHaveBeenCalledOnce();
    expect(onRefined).not.toHaveBeenCalled();
    expect(statuses).toEqual(["refining", "failed"]);
    // 同一個擺放再排程（例如 render）：不重送 fine、提示不變
    scheduler.schedule({ ...r }, false);
    await vi.advanceTimersByTimeAsync(5000);
    expect(calls).toHaveLength(2);
    expect(statuses).toEqual(["refining", "failed"]);
    // 下一次操作（拖到新位置）：提示清掉
    scheduler.schedule(req(2), true);
    expect(statuses).toEqual(["refining", "failed", "idle"]);
  });

  it("fine 失敗後回到原位置再放開：重新走 fast → fine（新的操作才重送）", async () => {
    const { scheduler, calls, fineCalls } = setup();
    scheduler.schedule(req(1), false);
    calls[0].resolve(FAST);
    await flush();
    calls[1].resolve(TIMEOUT);
    await flush();
    scheduler.schedule(req(2), true);
    await vi.advanceTimersByTimeAsync(300);
    calls[2].resolve(FAST);
    await flush();
    scheduler.schedule(req(1), false);
    expect(calls[3].request).toEqual(req(1));
    calls[3].resolve(FAST);
    await flush();
    expect(fineCalls()).toHaveLength(2);
    expect(calls[4].request).toEqual({ ...req(1), quality: "fine" });
  });

  it("fine 成功後拖走又放回原位：fast 重新顯示，所以再送一次 fine", async () => {
    const { scheduler, calls, onRefined } = setup();
    scheduler.schedule(req(1), false);
    calls[0].resolve(FAST);
    await flush();
    calls[1].resolve(FINE);
    await flush();
    scheduler.schedule(req(2), true);
    await vi.advanceTimersByTimeAsync(300);
    calls[2].resolve(FAST);
    await flush();
    scheduler.schedule(req(1), false);
    calls[3].resolve(FAST);
    await flush();
    expect(calls[4].request).toEqual({ ...req(1), quality: "fine" });
    calls[4].resolve(FINE);
    await flush();
    expect(onRefined).toHaveBeenCalledTimes(2);
  });

  it("send 意外 reject 的 fine：當成失敗（保留 fast、狀態 failed）", async () => {
    const statuses: RefineStatus[] = [];
    let n = 0;
    const onOutcome = vi.fn();
    const scheduler = createMatchScheduler({
      send: async () => {
        n += 1;
        if (n === 1) return FAST;
        throw new Error("boom");
      },
      onOutcome,
      onPendingChange: vi.fn(),
      refine: { enabled: () => true, onRefined: vi.fn(), onStatusChange: (s) => statuses.push(s) },
    });
    scheduler.schedule(req(1), false);
    await flush();
    await flush();
    expect(n).toBe(2);
    expect(statuses).toEqual(["refining", "failed"]);
    expect(onOutcome).toHaveBeenCalledOnce();
  });

  it("cancel：abort 在途的 fine，狀態回到 idle；同一個請求再排程時重送 fine", async () => {
    const { scheduler, calls, statuses } = setup();
    const r = req(1);
    scheduler.schedule(r, false);
    calls[0].resolve(FAST);
    await flush();
    scheduler.cancel();
    expect(calls[1].signal.aborted).toBe(true);
    expect(statuses).toEqual(["refining", "idle"]);
    scheduler.schedule(r, false);
    expect(calls).toHaveLength(3);
    expect(calls[2].request).toEqual({ ...r, quality: "fine" });
  });

  it("cancel：fine 失敗的提示一併清掉", async () => {
    const { scheduler, calls, statuses } = setup();
    scheduler.schedule(req(1), false);
    calls[0].resolve(FAST);
    await flush();
    calls[1].resolve(TIMEOUT);
    await flush();
    scheduler.cancel();
    expect(statuses).toEqual(["refining", "failed", "idle"]);
  });

  it("dispose：abort 在途的 fine，之後不再回呼", async () => {
    const { scheduler, calls, onRefined, statuses } = setup();
    scheduler.schedule(req(1), false);
    calls[0].resolve(FAST);
    await flush();
    scheduler.dispose();
    expect(calls[1].signal.aborted).toBe(true);
    calls[1].resolve(FINE);
    await flush();
    expect(onRefined).not.toHaveBeenCalled();
    expect(statuses).toEqual(["refining"]);
  });

  it("planner_ready 在決定的當下讀：fast 回來時才變成 true 也會送", async () => {
    const { scheduler, calls, state } = setup({ ready: false });
    scheduler.schedule(req(1), false);
    state.ready = true;
    calls[0].resolve(FAST);
    await flush();
    expect(calls[1].request.quality).toBe("fine");
  });
});
