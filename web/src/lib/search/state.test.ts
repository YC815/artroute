import { describe, expect, it } from "vitest";
import { sampleCandidate, sampleResult } from "@/lib/engine/fixtures";
import { INITIAL_SEARCH, searchReducer, visibleSearch, type SearchState } from "@/lib/search/state";

const started = searchReducer(INITIAL_SEARCH, { type: "start", run: 1, tag: "A" });

describe("searchReducer", () => {
  it("開始：狀態 starting，記下 run 與圖形的 tag，清掉上一次的預覽", () => {
    const withBest: SearchState = { ...started, best: sampleCandidate() };
    expect(searchReducer(withBest, { type: "start", run: 2, tag: "B" })).toEqual({
      run: 2,
      tag: "B",
      status: { phase: "starting" },
      best: null,
    });
  });

  it("queued → 排隊位置；progress → 階段與進度；best → 即時預覽（不改變階段）", () => {
    const queued = searchReducer(started, { type: "update", run: 1, update: { type: "queued", position: 3 } });
    expect(queued.status).toEqual({ phase: "queued", position: 3 });
    const running = searchReducer(queued, {
      type: "update",
      run: 1,
      update: { type: "progress", stage: "refine", done: 4, total: 30 },
    });
    expect(running.status).toEqual({ phase: "running", stage: "refine", done: 4, total: 30 });
    const candidate = sampleCandidate();
    const best = searchReducer(running, { type: "update", run: 1, update: { type: "best", candidate } });
    expect(best.status).toBe(running.status);
    expect(best.best).toBe(candidate);
  });

  it("結果 → done，預覽清掉（改由候選清單選）", () => {
    const withBest = { ...started, best: sampleCandidate() };
    const result = sampleResult([sampleCandidate()]);
    const done = searchReducer(withBest, { type: "finish", run: 1, outcome: { kind: "result", result } });
    expect(done.status).toEqual({ phase: "done", result });
    expect(done.best).toBeNull();
  });

  it("失敗 → failed 與訊息", () => {
    const error = { status: 200, code: "busy", message: "排隊太久", engineDown: false };
    const failed = searchReducer(started, { type: "finish", run: 1, outcome: { kind: "failed", error } });
    expect(failed.status).toEqual({ phase: "failed", message: "排隊太久" });
  });

  it("被取消（例如圖形換了）→ 回到 idle", () => {
    expect(searchReducer(started, { type: "finish", run: 1, outcome: { kind: "aborted" } })).toEqual(INITIAL_SEARCH);
  });

  it("cancel → idle；之後舊 run 的事件與結果都被丟掉", () => {
    const cancelled = searchReducer(started, { type: "cancel" });
    expect(cancelled).toEqual(INITIAL_SEARCH);
    const late = searchReducer(cancelled, { type: "update", run: 1, update: { type: "queued", position: 1 } });
    expect(late).toBe(cancelled);
    const lateResult = searchReducer(cancelled, {
      type: "finish",
      run: 1,
      outcome: { kind: "result", result: sampleResult([]) },
    });
    expect(lateResult).toBe(cancelled);
  });

  it("新的搜尋開始後，上一次搜尋晚到的事件與結果不會蓋掉新的", () => {
    const second = searchReducer(started, { type: "start", run: 2, tag: "A" });
    const stale = searchReducer(second, {
      type: "finish",
      run: 1,
      outcome: { kind: "result", result: sampleResult([]) },
    });
    expect(stale).toBe(second);
    expect(searchReducer(second, { type: "update", run: 1, update: { type: "queued", position: 9 } })).toBe(second);
  });

  it("已結束的搜尋不再接受同一個 run 的事件", () => {
    const done = searchReducer(started, { type: "finish", run: 1, outcome: { kind: "result", result: sampleResult([]) } });
    expect(searchReducer(done, { type: "update", run: 1, update: { type: "queued", position: 1 } })).toBe(done);
  });
});

describe("visibleSearch", () => {
  const result = sampleResult([sampleCandidate()]);
  const done = searchReducer(started, { type: "finish", run: 1, outcome: { kind: "result", result } });

  it("搜尋屬於目前的圖形 → 原樣顯示", () => {
    expect(visibleSearch(done, "A")).toEqual({ status: done.status, best: null, active: false });
    const running = { ...started, best: sampleCandidate() };
    expect(visibleSearch(running, "A")).toEqual({ status: { phase: "starting" }, best: running.best, active: true });
  });

  it("圖形換了 → 不顯示舊圖形的結果與預覽（候選的擺放屬於舊圖形）", () => {
    expect(visibleSearch(done, "B")).toEqual({ status: { phase: "idle" }, best: null, active: false });
    expect(visibleSearch({ ...started, best: sampleCandidate() }, null)).toEqual({
      status: { phase: "idle" },
      best: null,
      active: false,
    });
  });

  it("isActive：starting、queued、running 才算進行中", () => {
    expect(visibleSearch(INITIAL_SEARCH, "A").active).toBe(false);
    const queued = searchReducer(started, { type: "update", run: 1, update: { type: "queued", position: 1 } });
    expect(visibleSearch(queued, "A").active).toBe(true);
  });
});
