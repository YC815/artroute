import { describe, expect, it, vi } from "vitest";
import type { FetchLike } from "@/lib/engine/client";
import type { SearchEvent, SearchRequest } from "@/lib/engine/contract";
import { sampleCandidate, sampleResult, toSse } from "@/lib/engine/fixtures";
import { fetchSearch, type SearchUpdate } from "@/lib/engine/search-client";

const REQUEST: SearchRequest = {
  strokes: [{ points: [[-0.5, 0], [0.5, 0]], closed: false }],
  center: [121.5654, 25.033],
  radius_m: 3000,
  min_km: 10,
  max_km: 25,
  rotation_min_deg: -15,
  rotation_max_deg: 15,
};
const SSE = { "content-type": "text/event-stream; charset=utf-8" };
const encoder = new TextEncoder();

/** 回應是一條 SSE 串流：chunks 依序送出（之後結束，或 hang 為 true 時一直不結束）。 */
function sseFetch(chunks: string[], { hang = false } = {}): { fetchImpl: FetchLike; cancelled: () => boolean } {
  let cancelled = false;
  const fetchImpl: FetchLike = vi.fn(async () => {
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
        if (!hang) controller.close();
      },
      cancel() {
        cancelled = true;
      },
    });
    return new Response(body, { headers: SSE });
  });
  return { fetchImpl, cancelled: () => cancelled };
}

async function run(fetchImpl: FetchLike, signal = new AbortController().signal) {
  const updates: SearchUpdate[] = [];
  const outcome = await fetchSearch(REQUEST, signal, (update) => updates.push(update), fetchImpl);
  return { outcome, updates };
}

const PROGRESS: SearchEvent = { type: "progress", stage: "coarse", done: 2, total: 10 };
const QUEUED: SearchEvent = { type: "queued", position: 2 };
const BEST: SearchEvent = { type: "best", candidate: sampleCandidate() };
const RESULT = sampleResult([sampleCandidate(), sampleCandidate({ length_ok: false })], "沒有完全符合公里數的候選");

describe("fetchSearch", () => {
  it("POST JSON 到 /api/search，帶上 signal", async () => {
    const { fetchImpl } = sseFetch([toSse(RESULT)]);
    const signal = new AbortController().signal;
    await fetchSearch(REQUEST, signal, () => {}, fetchImpl);
    expect(fetchImpl).toHaveBeenCalledWith("/api/search", {
      method: "POST",
      headers: { "content-type": "application/json", accept: "text/event-stream" },
      body: JSON.stringify(REQUEST),
      signal,
    });
  });

  it("依序回報 queued／progress／best，最後回傳 result", async () => {
    const { fetchImpl } = sseFetch([toSse(QUEUED), ": ping\n\n", toSse(PROGRESS), toSse(BEST), toSse(RESULT)]);
    const { outcome, updates } = await run(fetchImpl);
    expect(updates).toEqual([QUEUED, PROGRESS, BEST]);
    expect(outcome).toEqual({ kind: "result", result: RESULT });
  });

  it("事件被切成任意小塊也一樣", async () => {
    const text = [QUEUED, PROGRESS, BEST, RESULT].map(toSse).join("");
    const chunks = text.match(/[\s\S]{1,7}/g) ?? [];
    const { outcome, updates } = await run(sseFetch(chunks).fetchImpl);
    expect(updates).toEqual([QUEUED, PROGRESS, BEST]);
    expect(outcome).toEqual({ kind: "result", result: RESULT });
  });

  it("引擎的 error 事件 → failed，帶引擎的 code 與訊息", async () => {
    const failed: SearchEvent = { type: "error", code: "timeout", message: "搜尋超過 60 秒" };
    const { outcome } = await run(sseFetch([toSse(PROGRESS), toSse(failed)]).fetchImpl);
    expect(outcome).toEqual({
      kind: "failed",
      error: { status: 200, code: "timeout", message: "搜尋超過 60 秒", engineDown: false },
    });
  });

  it("收到最後一個事件就停止讀取並取消串流（不等連線關閉）", async () => {
    const { fetchImpl, cancelled } = sseFetch([toSse(RESULT)], { hang: true });
    const { outcome } = await run(fetchImpl);
    expect(outcome.kind).toBe("result");
    expect(cancelled()).toBe(true);
  });

  it("不認得的事件名稱（含沒有 event 的 message）略過，不算錯誤", async () => {
    const unknown = 'event: hint\ndata: {"type":"hint"}\n\ndata: {"anything":1}\n\n';
    const { outcome, updates } = await run(sseFetch([unknown, toSse(PROGRESS), toSse(RESULT)]).fetchImpl);
    expect(updates).toEqual([PROGRESS]);
    expect(outcome.kind).toBe("result");
  });

  it("事件名稱是 Object 原型上的屬性（constructor）也只是不認得的事件", async () => {
    const { outcome } = await run(sseFetch(["event: constructor\ndata: {}\n\n", toSse(RESULT)]).fetchImpl);
    expect(outcome.kind).toBe("result");
  });

  it.each([
    ["data 不是 JSON", "event: progress\ndata: {oops\n\n"],
    ["欄位不符", 'event: progress\ndata: {"type":"progress","stage":"coarse","done":-1,"total":3}\n\n'],
    ["事件名稱與 data.type 不同", `event: progress\ndata: ${JSON.stringify(QUEUED)}\n\n`],
    ["候選格式錯", `event: best\ndata: ${JSON.stringify({ type: "best", candidate: { placement: null } })}\n\n`],
  ])("已知事件格式不符（%s）→ 502，停止讀取", async (_name, bad) => {
    const { fetchImpl, cancelled } = sseFetch([bad, toSse(RESULT)], { hang: true });
    const { outcome, updates } = await run(fetchImpl);
    expect(updates).toEqual([]);
    expect(outcome).toEqual({
      kind: "failed",
      error: { status: 502, code: null, message: "引擎回應的格式不符合契約", engineDown: false },
    });
    expect(cancelled()).toBe(true);
  });

  it("串流結束卻沒有 result／error → 中斷", async () => {
    const { outcome, updates } = await run(sseFetch([toSse(PROGRESS)]).fetchImpl);
    expect(updates).toEqual([PROGRESS]);
    expect(outcome).toEqual({
      kind: "failed",
      error: { status: 0, code: "incomplete", message: "搜尋的連線中斷了，沒有收到結果，請重試", engineDown: false },
    });
  });

  it("最後一個事件沒有空行結尾（被截斷）也算中斷", async () => {
    const truncated = toSse(RESULT).slice(0, -1);
    const { outcome } = await run(sseFetch([truncated]).fetchImpl);
    expect(outcome.kind === "failed" && outcome.error.code).toBe("incomplete");
  });

  it("讀串流時連線斷掉 → 中斷（不是引擎離線）", async () => {
    const fetchImpl: FetchLike = vi.fn(async () => {
      const body = new ReadableStream<Uint8Array>({
        start(controller) {
          controller.enqueue(encoder.encode(toSse(PROGRESS)));
          setTimeout(() => controller.error(new TypeError("network error")), 0);
        },
      });
      return new Response(body, { headers: SSE });
    });
    const { outcome, updates } = await run(fetchImpl);
    expect(updates).toEqual([PROGRESS]);
    expect(outcome).toEqual({
      kind: "failed",
      error: { status: 0, code: "incomplete", message: "搜尋的連線中斷了，沒有收到結果，請重試", engineDown: false },
    });
  });

  it("200 但不是 SSE → 502", async () => {
    const fetchImpl: FetchLike = vi.fn(async () => Response.json({ hello: 1 }));
    const { outcome } = await run(fetchImpl);
    expect(outcome.kind === "failed" && outcome.error.status).toBe(502);
  });

  describe("HTTP 錯誤（串流開始前）", () => {
    const respond = (status: number, body: unknown): FetchLike => vi.fn(async () => Response.json(body, { status }));

    it("引擎的 detail 訊息直接顯示，code 一併帶出", async () => {
      const { outcome } = await run(
        respond(400, { detail: { code: "outside_taiwan", message: "圓心不在台灣範圍內" } }),
      );
      expect(outcome).toEqual({
        kind: "failed",
        error: { status: 400, code: "outside_taiwan", message: "圓心不在台灣範圍內", engineDown: false },
      });
    });

    it("503 no_data → 引擎離線；503 no_search_data → 只是不能搜尋", async () => {
      const noData = await run(respond(503, { detail: { code: "no_data", message: "沒有路網" } }));
      expect(noData.outcome.kind === "failed" && noData.outcome.error.engineDown).toBe(true);
      const noSearch = await run(respond(503, { detail: { code: "no_search_data", message: "要重建路網" } }));
      expect(noSearch.outcome).toEqual({
        kind: "failed",
        error: { status: 503, code: "no_search_data", message: "要重建路網", engineDown: false },
      });
    });

    it("沒有可用訊息時用依狀態碼的預設訊息", async () => {
      const validation = await run(respond(422, { detail: [{ loc: ["body"], msg: "x" }] }));
      expect(validation.outcome).toEqual({
        kind: "failed",
        error: { status: 422, code: null, message: "輸入不合法，或圖形太小", engineDown: false },
      });
      const timeout = await run(respond(504, null));
      expect(timeout.outcome.kind === "failed" && timeout.outcome.error.message).toBe(
        "搜尋超過時間上限，請縮小範圍後重試",
      );
      const teapot = await run(respond(418, null));
      expect(teapot.outcome.kind === "failed" && teapot.outcome.error.message).toBe("搜尋失敗（HTTP 418）");
    });

    it("連不上 → status 0、引擎離線", async () => {
      const fetchImpl: FetchLike = vi.fn(async () => {
        throw new TypeError("fetch failed");
      });
      const { outcome } = await run(fetchImpl);
      expect(outcome).toEqual({
        kind: "failed",
        error: { status: 0, code: null, message: "連不上伺服器", engineDown: true },
      });
    });
  });

  describe("呼叫端取消", () => {
    it("回應前取消 → aborted", async () => {
      const controller = new AbortController();
      const fetchImpl: FetchLike = vi.fn(
        (_input, init) =>
          new Promise<Response>((_, reject) => {
            init!.signal!.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
          }),
      );
      const pending = run(fetchImpl, controller.signal);
      controller.abort();
      expect((await pending).outcome).toEqual({ kind: "aborted" });
    });

    it("串流中取消 → aborted，之後不再回報事件", async () => {
      const controller = new AbortController();
      let push!: (text: string) => void;
      const fetchImpl: FetchLike = vi.fn(async (_input, init) => {
        const body = new ReadableStream<Uint8Array>({
          start(c) {
            push = (text) => c.enqueue(encoder.encode(text));
            init!.signal!.addEventListener("abort", () => c.error(new DOMException("aborted", "AbortError")));
          },
        });
        return new Response(body, { headers: SSE });
      });
      const updates: SearchUpdate[] = [];
      const pending = fetchSearch(REQUEST, controller.signal, (u) => updates.push(u), fetchImpl);
      await vi.waitFor(() => expect(push).toBeDefined());
      push(toSse(PROGRESS));
      await vi.waitFor(() => expect(updates).toEqual([PROGRESS]));
      controller.abort();
      expect(await pending).toEqual({ kind: "aborted" });
      expect(updates).toEqual([PROGRESS]);
    });

    it("HTTP 錯誤的 body 讀到一半取消 → aborted，不是引擎離線", async () => {
      const controller = new AbortController();
      const res = {
        ok: false,
        status: 503,
        headers: new Headers(),
        json: () =>
          new Promise<unknown>((_, reject) => {
            controller.signal.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
          }),
      } as unknown as Response;
      const pending = run(vi.fn(async () => res), controller.signal);
      await Promise.resolve();
      controller.abort();
      expect((await pending).outcome).toEqual({ kind: "aborted" });
    });
  });
});
