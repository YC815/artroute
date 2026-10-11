import { afterEach, beforeEach, it, vi } from "vitest";
import type { MatchOutcome } from "@/lib/engine/client";
import type { MatchRequest } from "@/lib/engine/contract";
import { SAMPLE_MATCH_RESPONSE } from "@/lib/engine/fixtures";
import { requestKey } from "@/lib/match/request";
import { createMatchScheduler, THROTTLE_MS, type RefineStatus } from "@/lib/match/scheduler";

/**
 * 排程器的不變式檢查：固定種子的隨機操作序列（拖曳、放開、在途請求以隨機順序成功或失敗地回來、推進時間、cancel），
 * 每一步之後檢查「最多一筆在途」「只有放開和 cancel 會 abort」「間隔」「pending」「結果套用順序」等保證。
 * 放開後的描形版（fine）也在裡面：planner_ready 隨機切換；fine 只在放開後、目前擺放的 fast 成功時送，
 * 失敗不自動重送，被新的操作取消，舊的 fine 永遠不覆蓋新的結果（含 fast），fine 也不能覆蓋之後送出的 fast。
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
  /** quality: "fine" 的請求。key 一律是不帶 quality 的 requestKey（= 擺放的 key）。 */
  fine: boolean;
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
  const responseCall = new Map<object, Call>(); // 同上，給 onRefined（只收到 response）用
  const applied: Call[] = [];
  let releasing = false;
  let disposed = false;
  let notifiedPending = false;
  let currentTag: string | null = null;
  // 在途那筆失敗、而最新排程的和它相同：下一次 schedule 之前不能自動重送（否則對壞掉的引擎無限重試）
  let failedWithoutNewSchedule: Call | null = null;
  let plannerReady = random() < 0.7;
  let lastScheduleDragging = true; // 還沒排程過：當成拖曳中（不可送 fine）
  let lastAppliedFast: { call: Call; ok: boolean } | null = null;
  // fine 失敗的擺放：擺放改變（或 cancel）之前，同一個擺放不能再送 fine，「描形版失敗」的提示也要留著
  let fineFailedKey: string | null = null;
  let refineStatus: RefineStatus = "idle";
  // 決定要不要送 fine 的時機（fast 套用、放開）當下的 planner_ready：之後才翻成 true 不會補送，要等下一次操作
  let readyAtDecision = false;
  let lastKey: string | null = null; // 最後一次 schedule 的請求；cancel 之後不再要求它被送出

  const scheduler = createMatchScheduler({
    now: () => Date.now(),
    send: (request, signal) =>
      new Promise<MatchOutcome>((resolve) => {
        if (disposed) note("dispose 之後還在送");
        const fine = request.quality === "fine";
        const { quality, ...base } = request;
        if (!fine && quality !== undefined) note("fast 的請求帶了 quality（必須與以前逐位元相同）");
        const key = requestKey(base);
        const sentAt = Date.now();
        if (fine) {
          if (!plannerReady) note("planner_ready 為 false 卻送了 fine");
          if (lastScheduleDragging) note("拖曳中送了 fine");
          if (key !== lastKey) note("fine 不是目前擺放的請求");
          if (!lastAppliedFast?.ok || lastAppliedFast.call.key !== key) note("目前擺放的 fast 還沒成功就送了 fine");
          if (key === fineFailedKey) note("fine 失敗後沒有新的操作就被自動重送");
          if (vi.getTimerCount() > 0) note("還有排定的 fast 計時器就送了 fine");
        } else {
          if (failedWithoutNewSchedule) note(`失敗的 #${failedWithoutNewSchedule.index} 沒有新的 schedule 就被自動重送`);
          const previous = calls.filter((c) => !c.fine).at(-1);
          if (!releasing && previous && sentAt - previous.sentAt < THROTTLE_MS) {
            note(`非放開的送出間隔不足 ${THROTTLE_MS} ms：${previous.sentAt} -> ${sentAt}`);
          }
        }
        log.push(`t=${sentAt} 送出 #${calls.length}${fine ? "（fine）" : ""}`);
        calls.push({
          index: calls.length,
          fine,
          key,
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
      if (call.fine) note(`fine 的結果 #${call.index} 走了 onOutcome`);
      if (call.signal.aborted) note(`被 abort 的結果被套用 #${call.index}`);
      if (call.key !== key || call.tag !== tag) note(`tag／key 沒有在送出當下綁定 #${call.index}`);
      const previous = applied[applied.length - 1];
      if (previous && previous.index >= call.index) note(`舊結果覆蓋新結果 #${previous.index} -> #${call.index}`);
      applied.push(call);
      lastAppliedFast = { call, ok: outcome.ok };
      readyAtDecision = plannerReady;
    },
    onPendingChange: (pending) => {
      if (pending === notifiedPending) note("pending 沒變卻通知");
      notifiedPending = pending;
    },
    refine: {
      enabled: () => plannerReady,
      onRefined: (response, tag, key) => {
        const call = responseCall.get(response);
        if (!call) return note("onRefined 收到不是任何一筆請求回的結果");
        if (!call.fine) note(`fast 的結果 #${call.index} 走了 onRefined`);
        if (call.signal.aborted) note(`被 abort 的 fine 被套用 #${call.index}`);
        if (call.key !== key || call.tag !== tag) note(`fine 的 tag／key 不是送出時的擺放 #${call.index}`);
        if (key !== lastKey) note(`fine #${call.index} 不屬於目前的擺放卻被套用`);
        if (call !== calls[calls.length - 1]) note(`fine #${call.index} 之後已經送出新的請求，結果卻還是被套用`);
        const previous = applied[applied.length - 1];
        if (previous && previous.index >= call.index) note(`舊結果覆蓋新結果 #${previous.index} -> #${call.index}`);
        applied.push(call);
      },
      onStatusChange: (status) => {
        if (status === refineStatus) note("描形版狀態沒變卻通知");
        refineStatus = status;
      },
    },
  });

  const live = () => calls.filter((c) => !c.resolved && !c.signal.aborted);
  const respond = (call: Call, outcome: MatchOutcome) => {
    call.resolved = true;
    // 同一個 OK／FAIL 常數不能當 Map 的鍵：每筆回應（與其中的 response）都用獨立物件
    const own = (outcome.ok ? { ok: true, response: { ...outcome.response } } : { ...outcome }) as MatchOutcome;
    outcomeCall.set(own, call);
    if (own.ok) responseCall.set(own.response, call);
    call.resolve(own);
  };
  const checkInvariants = (where: string) => {
    if (violation) fail(violation);
    // fine 也算：擺放改變時在途的 fine 一定先被 abort，所以同時在途的請求（fast＋fine）仍最多一筆
    if (live().length > 1) fail(`同時有 ${live().length} 筆在途（${where}）`);
    // pending（「吸附中…」）只代表 fast
    const expected = vi.getTimerCount() > 0 || live().some((c) => !c.fine);
    if (notifiedPending !== expected) fail(`pending 為 ${notifiedPending}，應為 ${expected}（${where}）`);
    const liveFine = live().some((c) => c.fine);
    if ((refineStatus === "refining") !== liveFine) fail(`描形版狀態為 ${refineStatus}，fine 在途為 ${liveFine}（${where}）`);
    const expectFailed = !liveFine && fineFailedKey !== null;
    if ((refineStatus === "failed") !== expectFailed) fail(`描形版狀態為 ${refineStatus}，應${expectFailed ? "" : "不"}是 failed（${where}）`);
  };
  /** 排程到不同的擺放或 cancel：fine 失敗的紀錄（與提示）清掉。 */
  const placementChanged = (key: string | null) => {
    if (key === null || key !== fineFailedKey) fineFailedKey = null;
  };

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
    let mayAbortFine = false; // 拖曳到不同的擺放可以 abort 在途的 fine（不能 abort fast）
    if (x < 0.4) {
      const request = placement();
      currentTag = tag();
      log.push(`t=${Date.now()} 拖曳 P${position} ${currentTag}`);
      failedWithoutNewSchedule = null;
      placementChanged(requestKey(request));
      lastScheduleDragging = true;
      lastKey = requestKey(request);
      scheduler.schedule(request, true, currentTag);
      mayAbortFine = true;
    } else if (x < 0.5) {
      const request = placement();
      currentTag = tag();
      log.push(`t=${Date.now()} 放開 P${position} ${currentTag}`);
      failedWithoutNewSchedule = null;
      placementChanged(requestKey(request));
      lastScheduleDragging = false;
      lastKey = requestKey(request);
      readyAtDecision = plannerReady;
      releasing = true;
      scheduler.schedule(request, false, currentTag);
      releasing = false;
      mayAbort = true;
    } else if (x < 0.53) {
      plannerReady = !plannerReady;
      log.push(`t=${Date.now()} planner_ready=${plannerReady}`);
    } else if (x < 0.75) {
      const unresolved = calls.filter((c) => !c.resolved);
      if (unresolved.length > 0) {
        const call = unresolved[Math.floor(random() * unresolved.length)];
        const ok = random() < 0.7;
        log.push(`t=${Date.now()} 回 #${call.index} ${ok ? "OK" : "FAIL"}（aborted=${call.signal.aborted}）`);
        const wasLive = !call.signal.aborted;
        if (!ok && wasLive && call.fine) fineFailedKey = call.key;
        respond(call, ok ? OK : FAIL);
        if (!ok && wasLive && !call.fine && call.key === lastKey) failedWithoutNewSchedule = call;
      }
    } else if (x < 0.95) {
      const dt = Math.floor(random() * 700);
      log.push(`t=${Date.now()} 推進 ${dt} ms`);
      await vi.advanceTimersByTimeAsync(dt);
    } else {
      log.push(`t=${Date.now()} cancel`);
      failedWithoutNewSchedule = null;
      placementChanged(null);
      scheduler.cancel();
      lastKey = null;
      mayAbort = true;
    }
    await vi.advanceTimersByTimeAsync(0);
    // 只看還沒回來的：送出新請求時會順手對已經回來的舊 controller 呼叫 abort，那不影響任何請求
    const newlyAborted = calls.filter((c) => !c.resolved && c.signal.aborted && !abortedBefore.has(c.index));
    const forbidden = newlyAborted.filter((c) => !mayAbort && !(c.fine && mayAbortFine));
    if (forbidden.length > 0) {
      fail(`拖曳、計時器或結果套用 abort 了在途的 #${forbidden[0].index}（fast 只有放開和 cancel 可以，fine 另加拖曳）`);
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
  // 這兩個變數只在回呼裡被改：TS 的流程分析看不到，照宣告時的值收窄，這裡明確標回原本的型別
  if ((refineStatus as RefineStatus) === "refining") fail("收尾後描形版狀態卡在 refining");
  if (lastKey !== null) {
    const lastFast = calls.filter((c) => !c.fine).at(-1);
    if (!lastFast || lastFast.key !== lastKey) throw fail("最後一個位置從沒送出");
    const lastApplied = applied[applied.length - 1];
    const lastFastApplied = applied.filter((c) => !c.fine).at(-1);
    if (!lastFast.signal.aborted && lastFastApplied !== lastFast) fail("最後送出的那筆結果沒有被套用");
    // 放開後、planner_ready、這個擺放的 fast 成功、fine 沒失敗過：最後顯示的一定是這個擺放的 fine
    const appliedFast = lastAppliedFast as { call: Call; ok: boolean } | null;
    const fastOk = appliedFast?.call === lastFast && appliedFast.ok;
    const eligible = !lastScheduleDragging && readyAtDecision && fastOk && fineFailedKey === null;
    if (eligible && !(lastApplied?.fine && lastApplied.key === lastKey)) {
      fail("放開後 fast 成功、planner_ready，最後卻沒有換上這個擺放的 fine");
    }
  }

  // dispose 之後不能再送、不能再回呼
  failedWithoutNewSchedule = null;
  lastKey = requestKey(req(POSITIONS + 5));
  lastScheduleDragging = true;
  scheduler.schedule(req(POSITIONS + 5), true, "Z");
  for (const call of calls.filter((c) => !c.resolved)) respond(call, OK);
  disposed = true;
  const appliedBeforeDispose = applied.length;
  scheduler.dispose();
  await vi.advanceTimersByTimeAsync(2000);
  if (violation) fail(violation);
  if (applied.length !== appliedBeforeDispose) fail("dispose 之後還在回呼 onOutcome／onRefined");
}

it(`不變式：${SEQUENCES} 個固定種子的隨機操作序列（每個 ${STEPS} 步）`, async () => {
  for (let seed = 1; seed <= SEQUENCES; seed += 1) {
    vi.setSystemTime(0);
    await runSequence(seed);
  }
});
