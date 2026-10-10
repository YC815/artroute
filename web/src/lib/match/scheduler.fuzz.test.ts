import { afterEach, beforeEach, it, vi } from "vitest";
import type { MatchOutcome } from "@/lib/engine/client";
import type { MatchRequest } from "@/lib/engine/contract";
import { SAMPLE_MATCH_RESPONSE } from "@/lib/engine/fixtures";
import { requestKey } from "@/lib/match/request";
import { createMatchScheduler, THROTTLE_MS } from "@/lib/match/scheduler";

/**
 * 排程器的不變式檢查：固定種子的隨機操作序列（拖曳、放開、在途請求以隨機順序成功或失敗地回來、推進時間、cancel），
 * 每一步之後檢查「最多一筆在途」「只有放開和 cancel 會 abort」「間隔」「pending」「結果套用順序」等保證。
 * 單元測試釘住的是具名情境；這裡抓的是沒人想到的交錯。失敗時錯誤訊息帶種子與操作紀錄，可直接重現。
 */

const SEQUENCES = 1500;
const STEPS = 40;
const POSITIONS = 4;
const TAGS = 2;

const OK: MatchOutcome = { ok: true, response: SAMPLE_MATCH_RESPONSE };
const FAIL: MatchOutcome = {
  ok: false,
  error: { status: 500, message: "失敗", warnings: [], engineDown: false },
};

type Call = {
  index: number;
  key: string;
  tag: string | null;
  signal: AbortSignal;
  resolve: (outcome: MatchOutcome) => void;
  resolved: boolean;
  sentAt: number;
  sentByRelease: boolean;
};

function req(lon: number): MatchRequest {
  return { strokes: [{ points: [[lon, 25], [lon + 0.01, 25]], closed: false }] };
}

/** 固定種子的線性同餘亂數：同一個種子永遠產生同一個序列。 */
function rng(seed: number) {
  let s = seed >>> 0;
  return () => {
    s = (s * 1664525 + 1013904223) >>> 0;
    return s / 2 ** 32;
  };
}

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

async function runSequence(seed: number) {
  const random = rng(seed);
  const log: string[] = [];
  const fail = (message: string): never => {
    throw new Error(`種子 ${seed}：${message}\n${log.join("\n")}`);
  };
  // 排程器的回呼裡丟例外會被它吞掉（send 的 reject 當成失敗結果、其餘變成 unhandled rejection），
  // 所以回呼只記下第一個違規，由測試本體在每一步之後丟出。
  let violation: string | null = null;
  const note = (message: string) => {
    violation ??= message;
  };

  const calls: Call[] = [];
  const outcomeCall = new Map<MatchOutcome, Call>(); // 每筆回應用獨立物件，套用時才找得回是哪一筆
  const applied: Call[] = [];
  let releasing = false;
  let disposed = false;
  let notifiedPending = false;
  let currentTag: string | null = null;
  // 在途那筆失敗、而最新排程的和它相同：下一次 schedule 之前不能自動重送（否則對壞掉的引擎無限重試）
  let failedWithoutNewSchedule: Call | null = null;

  const scheduler = createMatchScheduler({
    now: () => Date.now(),
    send: (request, signal) =>
      new Promise<MatchOutcome>((resolve) => {
        if (disposed) note("dispose 之後還在送");
        if (failedWithoutNewSchedule) note(`失敗的 #${failedWithoutNewSchedule.index} 沒有新的 schedule 就被自動重送`);
        const previous = calls[calls.length - 1];
        const sentAt = Date.now();
        if (!releasing && previous && sentAt - previous.sentAt < THROTTLE_MS) {
          note(`非放開的送出間隔不足 ${THROTTLE_MS} ms：${previous.sentAt} -> ${sentAt}`);
        }
        log.push(`t=${sentAt} 送出 #${calls.length}`);
        calls.push({
          index: calls.length,
          key: requestKey(request),
          tag: currentTag,
          signal,
          resolve,
          resolved: false,
          sentAt,
          sentByRelease: releasing,
        });
      }),
    onOutcome: (outcome, tag, key) => {
      const call = outcomeCall.get(outcome);
      if (!call) return note("onOutcome 收到不是任何一筆請求回的結果");
      if (call.signal.aborted) note(`被 abort 的結果被套用 #${call.index}`);
      if (call.key !== key || call.tag !== tag) note(`tag／key 沒有在送出當下綁定 #${call.index}`);
      const previous = applied[applied.length - 1];
      if (previous && previous.index >= call.index) note(`舊結果覆蓋新結果 #${previous.index} -> #${call.index}`);
      applied.push(call);
    },
    onPendingChange: (pending) => {
      if (pending === notifiedPending) note("pending 沒變卻通知");
      notifiedPending = pending;
    },
  });

  const live = () => calls.filter((c) => !c.resolved && !c.signal.aborted);
  const respond = (call: Call, outcome: MatchOutcome) => {
    call.resolved = true;
    const own = { ...outcome } as MatchOutcome; // 同一個 OK／FAIL 常數不能當 Map 的鍵
    outcomeCall.set(own, call);
    call.resolve(own);
  };
  const checkInvariants = (where: string) => {
    if (violation) fail(violation);
    if (live().length > 1) fail(`同時有 ${live().length} 筆在途（${where}）`);
    const expected = vi.getTimerCount() > 0 || live().length > 0;
    if (notifiedPending !== expected) fail(`pending 為 ${notifiedPending}，應為 ${expected}（${where}）`);
  };

  let lastKey: string | null = null; // 最後一次 schedule 的請求；cancel 之後不再要求它被送出
  let position = 0; // 最近一次 placement 的位置編號，只給操作紀錄用
  const placement = () => {
    position = Math.floor(random() * POSITIONS);
    return req(position);
  };
  const tag = () => `T${Math.floor(random() * TAGS)}`;

  for (let step = 0; step < STEPS; step += 1) {
    const x = random();
    const abortedBefore = new Set(calls.filter((c) => !c.resolved && c.signal.aborted).map((c) => c.index));
    let mayAbort = false;
    if (x < 0.4) {
      const request = placement();
      currentTag = tag();
      log.push(`t=${Date.now()} 拖曳 P${position} ${currentTag}`);
      failedWithoutNewSchedule = null;
      scheduler.schedule(request, true, currentTag);
      lastKey = requestKey(request);
    } else if (x < 0.5) {
      const request = placement();
      currentTag = tag();
      log.push(`t=${Date.now()} 放開 P${position} ${currentTag}`);
      failedWithoutNewSchedule = null;
      releasing = true;
      scheduler.schedule(request, false, currentTag);
      releasing = false;
      lastKey = requestKey(request);
      mayAbort = true;
    } else if (x < 0.75) {
      const unresolved = calls.filter((c) => !c.resolved);
      if (unresolved.length > 0) {
        const call = unresolved[Math.floor(random() * unresolved.length)];
        const ok = random() < 0.7;
        log.push(`t=${Date.now()} 回 #${call.index} ${ok ? "OK" : "FAIL"}（aborted=${call.signal.aborted}）`);
        const wasLive = !call.signal.aborted;
        respond(call, ok ? OK : FAIL);
        if (!ok && wasLive && call.key === lastKey) failedWithoutNewSchedule = call;
      }
    } else if (x < 0.95) {
      const dt = Math.floor(random() * 700);
      log.push(`t=${Date.now()} 推進 ${dt} ms`);
      await vi.advanceTimersByTimeAsync(dt);
    } else {
      log.push(`t=${Date.now()} cancel`);
      failedWithoutNewSchedule = null;
      scheduler.cancel();
      lastKey = null;
      mayAbort = true;
    }
    await vi.advanceTimersByTimeAsync(0);
    // 只看還沒回來的：送出新請求時會順手對已經回來的舊 controller 呼叫 abort，那不影響任何請求
    const newlyAborted = calls.filter((c) => !c.resolved && c.signal.aborted && !abortedBefore.has(c.index));
    if (!mayAbort && newlyAborted.length > 0) {
      fail(`拖曳、計時器或結果套用 abort 了在途的 #${newlyAborted[0].index}（只有放開和 cancel 可以）`);
    }
    checkInvariants(`第 ${step} 步`);
  }

  // 收尾：不放開，讓在途的都成功回來、計時器跑完。拖曳中的最後位置不能遺失，pending 不能卡住。
  for (let round = 0; round < 20; round += 1) {
    for (const call of calls.filter((c) => !c.resolved)) respond(call, OK);
    await vi.advanceTimersByTimeAsync(1000);
  }
  checkInvariants("收尾");
  if (notifiedPending) fail("收尾後 pending 卡在 true");
  if (lastKey !== null) {
    const lastSend = calls[calls.length - 1];
    if (!lastSend || lastSend.key !== lastKey) fail("最後一個位置從沒送出");
    const lastApplied = applied[applied.length - 1];
    if (!lastSend.signal.aborted && lastApplied !== lastSend) fail("最後送出的那筆結果沒有被套用");
  }

  // dispose 之後不能再送、不能再回呼
  failedWithoutNewSchedule = null;
  scheduler.schedule(req(POSITIONS + 5), true, "Z");
  for (const call of calls.filter((c) => !c.resolved)) respond(call, OK);
  disposed = true;
  const appliedBeforeDispose = applied.length;
  scheduler.dispose();
  await vi.advanceTimersByTimeAsync(2000);
  if (violation) fail(violation);
  if (applied.length !== appliedBeforeDispose) fail("dispose 之後還在回呼 onOutcome");
}

it(`不變式：${SEQUENCES} 個固定種子的隨機操作序列（每個 ${STEPS} 步）`, async () => {
  for (let seed = 1; seed <= SEQUENCES; seed += 1) {
    vi.setSystemTime(0);
    await runSequence(seed);
  }
});
