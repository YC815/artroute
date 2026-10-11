import { afterEach, describe, expect, it, vi } from "vitest";
import {
  SAMPLE_EDIT_REQUEST,
  SAMPLE_EDIT_RESPONSE,
  SAMPLE_MATCH_RESPONSE,
  SAMPLE_VERIFY_REQUEST,
  sampleCandidate,
  sampleResult,
  toSse,
  without,
} from "@/lib/engine/fixtures";
import {
  EDIT_TIMEOUT_MS,
  MATCH_TIMEOUT_MS,
  SEARCH_TIMEOUT_MS,
  VERIFY_TIMEOUT_MS,
  proxyEdit,
  proxyMatch,
  proxySearch,
  proxyStatus,
  proxyVerify,
  type ProxyDeps,
} from "@/lib/engine/proxy";

const VALID = { strokes: [{ points: [[121.5, 25.03], [121.51, 25.03]], closed: false }] };

function deps(fetchImpl: ProxyDeps["fetch"], overrides: Partial<ProxyDeps> = {}): ProxyDeps {
  return { engineUrl: "http://engine:8080", fetch: fetchImpl, timeoutMs: 1000, ...overrides };
}

function post(body: string): Request {
  return new Request("http://web/api/match", { method: "POST", body });
}

/** 沒有 content-length 的串流 body：共 chunks 塊、每塊 chunkBytes bytes。 */
function streamPost(chunks: number, chunkBytes: number): { request: Request; pulled: () => number } {
  let sent = 0;
  const body = new ReadableStream<Uint8Array>({
    pull(controller) {
      if (sent === chunks) return controller.close();
      sent += 1;
      controller.enqueue(new Uint8Array(chunkBytes).fill(0x20));
    },
  });
  const request = new Request("http://web/api/match", { method: "POST", body, duplex: "half" } as RequestInit);
  return { request, pulled: () => sent };
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("proxyMatch", () => {
  it("不合法的請求回 422（繁中訊息），不轉發", async () => {
    const fetchImpl = vi.fn();
    const res = await proxyMatch(post(JSON.stringify({ strokes: [] })), deps(fetchImpl));
    expect(res.status).toBe(422);
    const { detail } = await res.json();
    expect(detail.code).toBe("validation");
    expect(detail.message).toContain("strokes");
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it("不是 JSON 也回 422", async () => {
    const res = await proxyMatch(post("not json"), deps(vi.fn()));
    expect(res.status).toBe(422);
    expect((await res.json()).detail.code).toBe("validation");
  });

  it("合法請求轉發到 ENGINE_URL/match，只送 schema 內的欄位", async () => {
    const fetchImpl = vi.fn(async () => Response.json(SAMPLE_MATCH_RESPONSE));
    const res = await proxyMatch(post(JSON.stringify({ ...VALID, extra: 1 })), deps(fetchImpl));
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual(SAMPLE_MATCH_RESPONSE);
    expect(fetchImpl).toHaveBeenCalledOnce();
    const [url, init] = fetchImpl.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("http://engine:8080/match");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual(VALID);
  });

  it("quality（fast／fine／null）原樣轉給引擎；沒帶就不帶（與以前逐位元相同）", async () => {
    for (const quality of ["fine", "fast", null] as const) {
      const fetchImpl = vi.fn(async () => Response.json(SAMPLE_MATCH_RESPONSE));
      const res = await proxyMatch(post(JSON.stringify({ ...VALID, quality })), deps(fetchImpl));
      expect(res.status).toBe(200);
      const [, init] = fetchImpl.mock.calls[0] as unknown as [string, RequestInit];
      expect(JSON.parse(init.body as string)).toEqual({ ...VALID, quality });
    }
    const fetchImpl = vi.fn(async () => Response.json(SAMPLE_MATCH_RESPONSE));
    await proxyMatch(post(JSON.stringify(VALID)), deps(fetchImpl));
    const [, init] = fetchImpl.mock.calls[0] as unknown as [string, RequestInit];
    expect(init.body).toBe(JSON.stringify(VALID));
  });

  it("不認得的 quality 回 422，不轉發", async () => {
    const fetchImpl = vi.fn();
    const res = await proxyMatch(post(JSON.stringify({ ...VALID, quality: "best" })), deps(fetchImpl));
    expect(res.status).toBe(422);
    expect((await res.json()).detail.message).toContain("quality");
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it("fine 用同一個逾時：引擎 /match 上限仍是 5 秒（spec §5），代理的 10 秒已有餘裕", () => {
    expect(MATCH_TIMEOUT_MS).toBe(10_000);
  });

  it("引擎的錯誤狀態碼與 body 原樣轉回", async () => {
    const body = { detail: { code: "outside_taiwan", message: "圖形超出台灣" } };
    const fetchImpl = vi.fn(async () => Response.json(body, { status: 400 }));
    const res = await proxyMatch(post(JSON.stringify(VALID)), deps(fetchImpl));
    expect(res.status).toBe(400);
    expect(await res.json()).toEqual(body);
  });

  it("引擎 422 no_route 連同 detail.warnings 原樣轉回", async () => {
    const body = {
      detail: { code: "no_route", message: "所有部件都吸附失敗", warnings: SAMPLE_MATCH_RESPONSE.warnings },
    };
    const fetchImpl = vi.fn(async () => Response.json(body, { status: 422 }));
    const res = await proxyMatch(post(JSON.stringify(VALID)), deps(fetchImpl));
    expect(res.status).toBe(422);
    expect(await res.json()).toEqual(body);
  });

  it("連不上引擎回 503", async () => {
    const fetchImpl = vi.fn(async () => {
      throw new TypeError("fetch failed");
    });
    const res = await proxyMatch(post(JSON.stringify(VALID)), deps(fetchImpl));
    expect(res.status).toBe(503);
    expect(await res.json()).toEqual({ detail: { code: "engine_unreachable", message: "引擎無法連線" } });
  });

  it("引擎超過 timeoutMs 沒回應就回 504", async () => {
    const fetchImpl = vi.fn(
      (_url: string, init: RequestInit) =>
        new Promise<Response>((_, reject) => {
          init.signal!.addEventListener("abort", () => reject(init.signal!.reason));
        }),
    );
    const res = await proxyMatch(post(JSON.stringify(VALID)), deps(fetchImpl, { timeoutMs: 20 }));
    expect(res.status).toBe(504);
    expect(await res.json()).toEqual({ detail: { code: "timeout", message: "引擎回應逾時" } });
  });

  it("沒設定 ENGINE_URL 回 503", async () => {
    const res = await proxyMatch(post(JSON.stringify(VALID)), deps(vi.fn(), { engineUrl: undefined }));
    expect(res.status).toBe(503);
    expect(await res.json()).toEqual({
      detail: { code: "engine_unreachable", message: "伺服器沒有設定 ENGINE_URL" },
    });
  });
});

describe("回應標頭", () => {
  const NOSNIFF = "nosniff";

  it("轉回引擎的回應（成功與錯誤狀態碼）帶 x-content-type-options: nosniff，content-type 照舊", async () => {
    const ok = await proxyMatch(post(JSON.stringify(VALID)), deps(vi.fn(async () => Response.json(SAMPLE_MATCH_RESPONSE))));
    expect(ok.headers.get("x-content-type-options")).toBe(NOSNIFF);
    expect(ok.headers.get("content-type")).toContain("application/json");

    const failed = await proxyMatch(
      post(JSON.stringify(VALID)),
      deps(vi.fn(async () => Response.json({ detail: { code: "outside_taiwan", message: "x" } }, { status: 400 }))),
    );
    expect(failed.headers.get("x-content-type-options")).toBe(NOSNIFF);
  });

  it("引擎沒有給 content-type 時補 application/json，仍帶 nosniff", async () => {
    const bare = new Response("{}", { headers: {} });
    bare.headers.delete("content-type");
    const res = await proxyMatch(post(JSON.stringify(VALID)), deps(vi.fn(async () => bare)));
    expect(res.headers.get("content-type")).toBe("application/json");
    expect(res.headers.get("x-content-type-options")).toBe(NOSNIFF);
  });

  it("代理自己產生的錯誤（413、422、503、504）都帶 nosniff", async () => {
    const tooLarge = await proxyMatch(
      new Request("http://web/api/match", {
        method: "POST",
        body: "{}",
        headers: { "content-length": String(8 * 1024 * 1024 + 1) },
      }),
      deps(vi.fn()),
    );
    const invalid = await proxyMatch(post("not json"), deps(vi.fn()));
    const unreachable = await proxyMatch(post(JSON.stringify(VALID)), deps(vi.fn(), { engineUrl: undefined }));
    const timeout = await proxyMatch(
      post(JSON.stringify(VALID)),
      deps(
        vi.fn(
          (_url: string, init: RequestInit) =>
            new Promise<Response>((_, reject) => {
              init.signal!.addEventListener("abort", () => reject(init.signal!.reason));
            }),
        ),
        { timeoutMs: 20 },
      ),
    );
    expect([tooLarge.status, invalid.status, unreachable.status, timeout.status]).toEqual([413, 422, 503, 504]);
    for (const res of [tooLarge, invalid, unreachable, timeout]) {
      expect(res.headers.get("x-content-type-options")).toBe(NOSNIFF);
    }
  });

  it("GET /status 的回應也帶 nosniff", async () => {
    const res = await proxyStatus(
      new Request("http://web/api/status"),
      deps(vi.fn(async () => Response.json({ data: null }))),
    );
    expect(res.headers.get("x-content-type-options")).toBe(NOSNIFF);
  });
});

describe("proxyMatch 請求大小與錯誤訊息長度", () => {
  const TOO_LARGE = { detail: { code: "too_large", message: "請求太大" } };

  it("content-length 超過 8 MB → 413，不讀 body、不轉發", async () => {
    const fetchImpl = vi.fn();
    const request = new Request("http://web/api/match", {
      method: "POST",
      body: "{}",
      headers: { "content-length": String(8 * 1024 * 1024 + 1) },
    });
    const res = await proxyMatch(request, deps(fetchImpl));
    expect(res.status).toBe(413);
    expect(await res.json()).toEqual(TOO_LARGE);
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it("沒有 content-length 的串流超過 8 MB → 413，讀到上限就停", async () => {
    const fetchImpl = vi.fn();
    const { request, pulled } = streamPost(64, 1024 * 1024);
    expect(request.headers.get("content-length")).toBeNull();
    const res = await proxyMatch(request, deps(fetchImpl));
    expect(res.status).toBe(413);
    expect(await res.json()).toEqual(TOO_LARGE);
    expect(fetchImpl).not.toHaveBeenCalled();
    expect(pulled()).toBeLessThan(64);
  });

  it("沒有 content-length、剛好在上限內的串流照常處理（空白填充的非法 JSON → 422）", async () => {
    const { request } = streamPost(8, 1024 * 1024);
    const res = await proxyMatch(request, deps(vi.fn()));
    expect(res.status).toBe(422);
  });

  it.each([
    ["3 萬點 → 約 6 萬個 zod issue", 30_000],
    ["10 萬點 → zod 內部展開參數 RangeError", 100_000],
  ])("病態請求（%s）只回小小的 422", async (_name, count) => {
    const points = Array.from({ length: count }, () => ["a", "b"]);
    const body = JSON.stringify({ strokes: [{ points, closed: false }] });
    const fetchImpl = vi.fn();
    const res = await proxyMatch(post(body), deps(fetchImpl));
    expect(res.status).toBe(422);
    const text = await res.text();
    expect(text.length).toBeLessThan(1500);
    expect(JSON.parse(text).detail.code).toBe("validation");
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it("驗證訊息最多列 5 個問題、不超過 500 字", async () => {
    const strokes = Array.from({ length: 10 }, () => ({ points: [[121.5, 25.03]], closed: false }));
    const res = await proxyMatch(post(JSON.stringify({ strokes })), deps(vi.fn()));
    const { message } = (await res.json()).detail as { message: string };
    expect(message.split("\n").filter((line) => line.startsWith("✖")).length).toBeLessThanOrEqual(5);
    expect(message.length).toBeLessThanOrEqual(500);
  });
});

describe("forwardToEngine 的錯誤處理", () => {
  it("連不上時在伺服器 log 記下路徑與錯誤，回應 body 不含錯誤細節", async () => {
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    const boom = new TypeError("fetch failed: ECONNREFUSED 10.0.0.7:8080");
    const fetchImpl = vi.fn(async () => {
      throw boom;
    });
    const res = await proxyMatch(post(JSON.stringify(VALID)), deps(fetchImpl));
    expect(spy).toHaveBeenCalledWith("[engine proxy]", "/match", boom);
    expect(JSON.stringify(await res.json())).not.toContain("ECONNREFUSED");
  });

  it("瀏覽器自己取消請求時不記 log", async () => {
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    const controller = new AbortController();
    const fetchImpl = vi.fn(
      (_url: string, init: RequestInit) =>
        new Promise<Response>((_, reject) => {
          init.signal!.addEventListener("abort", () => reject(init.signal!.reason));
        }),
    );
    const request = new Request("http://web/api/match", {
      method: "POST",
      body: JSON.stringify(VALID),
      signal: controller.signal,
    });
    const pending = proxyMatch(request, deps(fetchImpl));
    await new Promise((resolve) => setTimeout(resolve, 5));
    controller.abort();
    await pending;
    expect(spy).not.toHaveBeenCalled();
  });

  it("ENGINE_URL 結尾有斜線也不會變成 //match", async () => {
    const fetchImpl = vi.fn(async () => Response.json(SAMPLE_MATCH_RESPONSE));
    await proxyMatch(post(JSON.stringify(VALID)), deps(fetchImpl, { engineUrl: "http://engine:8080/" }));
    await proxyStatus(new Request("http://web/api/status"), deps(fetchImpl, { engineUrl: "http://engine:8080//" }));
    const urls = fetchImpl.mock.calls.map((call) => (call as unknown as [string])[0]);
    expect(urls).toEqual(["http://engine:8080/match", "http://engine:8080/status"]);
  });

  it("收到 headers 之後 body 卡住逾時 → 504，不是截斷的串流", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    const fetchImpl = vi.fn(
      async (_url: string, init: RequestInit) =>
        new Response(
          new ReadableStream({
            start(controller) {
              init.signal!.addEventListener("abort", () => controller.error(init.signal!.reason));
            },
          }),
          { status: 200, headers: { "content-type": "application/json" } },
        ),
    );
    const res = await proxyMatch(post(JSON.stringify(VALID)), deps(fetchImpl, { timeoutMs: 20 }));
    expect(res.status).toBe(504);
    expect(await res.json()).toEqual({ detail: { code: "timeout", message: "引擎回應逾時" } });
  });

  it("轉回引擎的 status、content-type 與 body bytes", async () => {
    const fetchImpl = vi.fn(
      async () => new Response("純文字錯誤", { status: 502, headers: { "content-type": "text/plain; charset=utf-8" } }),
    );
    const res = await proxyMatch(post(JSON.stringify(VALID)), deps(fetchImpl));
    expect(res.status).toBe(502);
    expect(res.headers.get("content-type")).toBe("text/plain; charset=utf-8");
    expect(await res.text()).toBe("純文字錯誤");
  });
});

describe("proxyStatus", () => {
  it("GET ENGINE_URL/status 並原樣轉回", async () => {
    const body = { data: { build_id: "20261010T145928Z", osm_md5: "abc", created_at: "x" } };
    const fetchImpl = vi.fn(async () => Response.json(body));
    const res = await proxyStatus(new Request("http://web/api/status"), deps(fetchImpl));
    expect(await res.json()).toEqual(body);
    const [url, init] = fetchImpl.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("http://engine:8080/status");
    expect(init.method).toBe("GET");
  });
});

describe.each([
  ["proxyEdit", proxyEdit, "/edit", SAMPLE_EDIT_REQUEST],
  ["proxyVerify", proxyVerify, "/verify", SAMPLE_VERIFY_REQUEST],
] as const)("%s", (_name, proxy, enginePath, valid) => {
  const request = (body: string) => new Request(`http://web/api${enginePath}`, { method: "POST", body });

  it(`合法請求轉發到 ENGINE_URL${enginePath}，只送 schema 內的欄位，200 原樣轉回`, async () => {
    const fetchImpl = vi.fn(async () => Response.json(SAMPLE_EDIT_RESPONSE));
    const res = await proxy(request(JSON.stringify({ ...valid, extra: 1 })), deps(fetchImpl));
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual(SAMPLE_EDIT_RESPONSE);
    const [url, init] = fetchImpl.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe(`http://engine:8080${enginePath}`);
    expect(JSON.parse(init.body as string)).toEqual(valid);
  });

  it("不合法的請求（缺 route_sig）回 422 validation，不轉發", async () => {
    const fetchImpl = vi.fn();
    const res = await proxy(request(JSON.stringify(without(valid, "route_sig"))), deps(fetchImpl));
    expect(res.status).toBe(422);
    const { detail } = await res.json();
    expect(detail.code).toBe("validation");
    expect(detail.message).toContain("route_sig");
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it("引擎 409 stale_data 的狀態碼與 body 原樣轉回", async () => {
    const body = { detail: { code: "stale_data", message: "路網已更新，請先重新驗證", segments: null, warnings: null } };
    const res = await proxy(request(JSON.stringify(valid)), deps(vi.fn(async () => Response.json(body, { status: 409 }))));
    expect(res.status).toBe(409);
    expect(await res.json()).toEqual(body);
  });

  it("引擎 422 的 body（含 verify_failed 的 segments）原樣轉回", async () => {
    for (const detail of [
      { code: "invalid_route", message: "路線簽章不對" },
      { code: "verify_failed", message: "有 2 段不能騎了", segments: [3, 7] },
    ]) {
      const res = await proxy(
        request(JSON.stringify(valid)),
        deps(vi.fn(async () => Response.json({ detail }, { status: 422 }))),
      );
      expect(res.status).toBe(422);
      expect(await res.json()).toEqual({ detail });
    }
  });

  it("引擎 504 timeout 原樣轉回；逾時沒回應則回代理的 504", async () => {
    const body = { detail: { code: "timeout", message: "微調超過時間上限" } };
    const upstream = await proxy(request(JSON.stringify(valid)), deps(vi.fn(async () => Response.json(body, { status: 504 }))));
    expect(upstream.status).toBe(504);
    expect(await upstream.json()).toEqual(body);

    const hang = vi.fn(
      (_url: string, init: RequestInit) =>
        new Promise<Response>((_, reject) => {
          init.signal!.addEventListener("abort", () => reject(init.signal!.reason));
        }),
    );
    const res = await proxy(request(JSON.stringify(valid)), deps(hang, { timeoutMs: 20 }));
    expect(res.status).toBe(504);
    expect(await res.json()).toEqual({ detail: { code: "timeout", message: "引擎回應逾時" } });
  });

  it("連不上引擎回 503", async () => {
    const fetchImpl = vi.fn(async () => {
      throw new TypeError("fetch failed");
    });
    const res = await proxy(request(JSON.stringify(valid)), deps(fetchImpl));
    expect(res.status).toBe(503);
    expect((await res.json()).detail.code).toBe("engine_unreachable");
  });

  it("超過 8 MB 的請求回 413，不轉發", async () => {
    const fetchImpl = vi.fn();
    const big = new Request(`http://web/api${enginePath}`, {
      method: "POST",
      body: "x",
      headers: { "content-length": String(8 * 1024 * 1024 + 1) },
    });
    const res = await proxy(big, deps(fetchImpl));
    expect(res.status).toBe(413);
    expect(fetchImpl).not.toHaveBeenCalled();
  });
});

describe("逾時常數", () => {
  it("/edit 與 /match 同，/verify 16 秒（引擎 15 秒的上限再多 1 秒）", () => {
    expect(EDIT_TIMEOUT_MS).toBe(MATCH_TIMEOUT_MS);
    expect(VERIFY_TIMEOUT_MS).toBe(16_000);
  });
});

describe("proxySearch", () => {
  const SEARCH = {
    strokes: [{ points: [[-0.5, 0], [0.5, 0.2]], closed: false }],
    center: [121.5654, 25.033],
    radius_m: 3000,
    min_km: 10,
    max_km: 25,
    rotation_min_deg: -15,
    rotation_max_deg: 15,
  };
  const SSE_HEADERS = { "content-type": "text/event-stream; charset=utf-8" };
  const decoder = new TextDecoder();

  function searchPost(body: string, signal?: AbortSignal): Request {
    return new Request("http://web/api/search", { method: "POST", body, signal });
  }

  /** 引擎的 SSE 串流：測試自己決定什麼時候送下一塊、什麼時候結束；cancelled 記錄下游是否取消了它。 */
  function engineStream() {
    let controller!: ReadableStreamDefaultController<Uint8Array>;
    const state = { cancelled: false };
    const body = new ReadableStream<Uint8Array>({
      start(c) {
        controller = c;
      },
      cancel() {
        state.cancelled = true;
      },
    });
    const encoder = new TextEncoder();
    return {
      body,
      state,
      send: (text: string) => controller.enqueue(encoder.encode(text)),
      close: () => controller.close(),
      fail: (error: unknown) => controller.error(error),
    };
  }

  it("搜尋的總逾時涵蓋排隊 120 s ＋ 搜尋 60 s 再留餘裕", () => {
    expect(SEARCH_TIMEOUT_MS).toBe(200_000);
  });

  it("不合法的請求（最小公里數大於最大）回 422，不轉發", async () => {
    const fetchImpl = vi.fn();
    const res = await proxySearch(searchPost(JSON.stringify({ ...SEARCH, min_km: 30 })), deps(fetchImpl));
    expect(res.status).toBe(422);
    const { detail } = await res.json();
    expect(detail.code).toBe("validation");
    expect(detail.message).toContain("最小公里數不能大於最大公里數");
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it("請求太大回 413，不轉發", async () => {
    const fetchImpl = vi.fn();
    const request = new Request("http://web/api/search", {
      method: "POST",
      body: "{}",
      headers: { "content-length": String(8 * 1024 * 1024 + 1) },
    });
    const res = await proxySearch(request, deps(fetchImpl));
    expect(res.status).toBe(413);
    expect((await res.json()).detail.code).toBe("too_large");
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it("合法請求 POST 到 ENGINE_URL/search，只送 schema 內的欄位", async () => {
    const fetchImpl = vi.fn(async () => new Response(toSse(sampleResult([])), { headers: SSE_HEADERS }));
    await proxySearch(searchPost(JSON.stringify({ ...SEARCH, extra: true })), deps(fetchImpl));
    const [url, init] = fetchImpl.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("http://engine:8080/search");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual(SEARCH);
  });

  it("SSE 邊收邊轉：引擎串流還沒結束，第一個事件就讀得到；標頭關掉快取與緩衝", async () => {
    const engine = engineStream();
    const fetchImpl = vi.fn(async () => new Response(engine.body, { headers: SSE_HEADERS }));
    const res = await proxySearch(searchPost(JSON.stringify(SEARCH)), deps(fetchImpl));
    expect(res.status).toBe(200);
    expect(res.headers.get("content-type")).toBe("text/event-stream; charset=utf-8");
    expect(res.headers.get("cache-control")).toBe("no-cache, no-transform");
    expect(res.headers.get("x-accel-buffering")).toBe("no");
    expect(res.headers.get("x-content-type-options")).toBe("nosniff");

    const reader = res.body!.getReader();
    const first = toSse({ type: "progress", stage: "coarse", done: 1, total: 10 });
    engine.send(first);
    expect(decoder.decode((await reader.read()).value)).toBe(first);

    const best = toSse({ type: "best", candidate: sampleCandidate() });
    engine.send(best);
    expect(decoder.decode((await reader.read()).value)).toBe(best);

    const last = toSse(sampleResult([sampleCandidate()]));
    engine.send(last);
    engine.close();
    expect(decoder.decode((await reader.read()).value)).toBe(last);
    expect((await reader.read()).done).toBe(true);
  });

  it("引擎的錯誤（串流開始前的 JSON）狀態碼與 body 原樣轉回", async () => {
    for (const [status, code] of [
      [400, "outside_taiwan"],
      [422, "too_small"],
      [503, "no_search_data"],
    ] as const) {
      const body = { detail: { code, message: "x" } };
      const res = await proxySearch(
        searchPost(JSON.stringify(SEARCH)),
        deps(vi.fn(async () => Response.json(body, { status }))),
      );
      expect(res.status).toBe(status);
      expect(res.headers.get("content-type")).toContain("application/json");
      expect(res.headers.get("x-content-type-options")).toBe("nosniff");
      expect(await res.json()).toEqual(body);
    }
  });

  it("引擎 200 但不是 SSE：整個讀完原樣轉回（不當成串流）", async () => {
    const res = await proxySearch(
      searchPost(JSON.stringify(SEARCH)),
      deps(vi.fn(async () => Response.json({ hello: 1 }))),
    );
    expect(res.status).toBe(200);
    expect(res.headers.get("content-type")).toContain("application/json");
    expect(await res.json()).toEqual({ hello: 1 });
  });

  it("連不上引擎回 503；沒設定 ENGINE_URL 也是 503", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    const down = vi.fn(async () => {
      throw new TypeError("fetch failed");
    });
    expect((await proxySearch(searchPost(JSON.stringify(SEARCH)), deps(down))).status).toBe(503);
    const unset = await proxySearch(searchPost(JSON.stringify(SEARCH)), deps(vi.fn(), { engineUrl: undefined }));
    expect(unset.status).toBe(503);
  });

  it("收到標頭前逾時 → 504 JSON", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    const fetchImpl = vi.fn(
      (_url: string, init: RequestInit) =>
        new Promise<Response>((_, reject) => {
          init.signal!.addEventListener("abort", () => reject(init.signal!.reason));
        }),
    );
    const res = await proxySearch(searchPost(JSON.stringify(SEARCH)), deps(fetchImpl, { timeoutMs: 20 }));
    expect(res.status).toBe(504);
    expect(await res.json()).toEqual({ detail: { code: "timeout", message: "引擎回應逾時" } });
  });

  it("串流中途逾時：已送出的事件照常送達，之後串流正常結束（沒有結果事件，由前端當成中斷）", async () => {
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    const engine = engineStream();
    const fetchImpl = vi.fn(async (_url: string, init: RequestInit) => {
      init.signal!.addEventListener("abort", () => engine.fail(init.signal!.reason));
      return new Response(engine.body, { headers: SSE_HEADERS });
    });
    const res = await proxySearch(searchPost(JSON.stringify(SEARCH)), deps(fetchImpl, { timeoutMs: 30 }));
    const reader = res.body!.getReader();
    const first = toSse({ type: "queued", position: 1 });
    engine.send(first);
    expect(decoder.decode((await reader.read()).value)).toBe(first);
    expect((await reader.read()).done).toBe(true);
    expect(spy).toHaveBeenCalledWith("[engine proxy]", "/search", expect.anything());
  });

  it("瀏覽器斷線（request.signal）→ 取消對引擎的請求，不記 log", async () => {
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    const engine = engineStream();
    let upstream: AbortSignal | undefined;
    const fetchImpl = vi.fn(async (_url: string, init: RequestInit) => {
      upstream = init.signal!;
      upstream.addEventListener("abort", () => engine.fail(upstream!.reason));
      return new Response(engine.body, { headers: SSE_HEADERS });
    });
    const browser = new AbortController();
    const res = await proxySearch(searchPost(JSON.stringify(SEARCH), browser.signal), deps(fetchImpl));
    const reader = res.body!.getReader();
    expect(upstream?.aborted).toBe(false);
    browser.abort();
    expect(upstream?.aborted).toBe(true);
    expect((await reader.read()).done).toBe(true);
    expect(spy).not.toHaveBeenCalled();
  });

  it("下游取消回應串流 → 引擎的串流也被取消", async () => {
    const engine = engineStream();
    const fetchImpl = vi.fn(async () => new Response(engine.body, { headers: SSE_HEADERS }));
    const res = await proxySearch(searchPost(JSON.stringify(SEARCH)), deps(fetchImpl));
    await res.body!.cancel();
    expect(engine.state.cancelled).toBe(true);
  });
});
