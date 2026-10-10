import { describe, expect, it, vi } from "vitest";
import { SAMPLE_MATCH_RESPONSE } from "@/lib/engine/fixtures";
import { proxyMatch, proxyStatus, type ProxyDeps } from "@/lib/engine/proxy";

const VALID = { strokes: [{ points: [[121.5, 25.03], [121.51, 25.03]], closed: false }] };

function deps(fetchImpl: ProxyDeps["fetch"], overrides: Partial<ProxyDeps> = {}): ProxyDeps {
  return { engineUrl: "http://engine:8080", fetch: fetchImpl, timeoutMs: 1000, ...overrides };
}

function post(body: string): Request {
  return new Request("http://web/api/match", { method: "POST", body });
}

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
