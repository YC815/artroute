import { describe, expect, it, vi } from "vitest";
import { fetchMatch, fetchStatus, toMatchError, type FetchLike } from "@/lib/engine/client";
import type { MatchRequest } from "@/lib/engine/contract";
import { SAMPLE_MATCH_RESPONSE } from "@/lib/engine/fixtures";

const REQUEST: MatchRequest = { strokes: [{ points: [[121.5, 25.03], [121.51, 25.03]], closed: false }] };

function respond(status: number, body: unknown): FetchLike {
  return vi.fn(async () => Response.json(body, { status }));
}

describe("fetchMatch", () => {
  it("POST JSON 到 /api/match，帶上 signal", async () => {
    const fetchImpl = respond(200, SAMPLE_MATCH_RESPONSE);
    const signal = new AbortController().signal;
    await fetchMatch(REQUEST, signal, fetchImpl);
    expect(fetchImpl).toHaveBeenCalledWith("/api/match", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(REQUEST),
      signal,
    });
  });

  it("200 且格式正確 → ok", async () => {
    const outcome = await fetchMatch(REQUEST, new AbortController().signal, respond(200, SAMPLE_MATCH_RESPONSE));
    expect(outcome).toEqual({ ok: true, response: SAMPLE_MATCH_RESPONSE });
  });

  it("200 但格式不符 → 502 錯誤", async () => {
    const outcome = await fetchMatch(REQUEST, new AbortController().signal, respond(200, { route: null }));
    expect(outcome).toEqual({
      ok: false,
      error: { status: 502, message: "引擎回應的格式不符合契約", warnings: [], engineDown: false },
    });
  });

  it("引擎的 detail.message 直接顯示", async () => {
    const outcome = await fetchMatch(
      REQUEST,
      new AbortController().signal,
      respond(400, { detail: { code: "outside_taiwan", message: "圖形超出台灣" } }),
    );
    expect(outcome).toEqual({
      ok: false,
      error: { status: 400, message: "圖形超出台灣", warnings: [], engineDown: false },
    });
  });

  it("422 no_route：warnings 在 detail 裡，一併帶出", async () => {
    const warnings = SAMPLE_MATCH_RESPONSE.warnings;
    const outcome = await fetchMatch(
      REQUEST,
      new AbortController().signal,
      respond(422, { detail: { code: "no_route", message: "所有部件都吸附失敗", warnings } }),
    );
    expect(outcome).toEqual({
      ok: false,
      error: { status: 422, message: "所有部件都吸附失敗", warnings, engineDown: false },
    });
  });

  it("pydantic 驗證錯誤（detail 是陣列）→ 依狀態碼的預設訊息", async () => {
    const outcome = await fetchMatch(
      REQUEST,
      new AbortController().signal,
      respond(422, { detail: [{ loc: ["body"], msg: "x" }] }),
    );
    expect(outcome).toEqual({
      ok: false,
      error: { status: 422, message: "輸入不合法，或所有部件都吸附失敗", warnings: [], engineDown: false },
    });
  });

  it("503 → engineDown", async () => {
    const outcome = await fetchMatch(
      REQUEST,
      new AbortController().signal,
      respond(503, { detail: { code: "no_data", message: "沒有可用路網" } }),
    );
    expect(outcome.ok === false && outcome.error.engineDown).toBe(true);
  });

  it("網路錯誤 → status 0、engineDown", async () => {
    const fetchImpl: FetchLike = vi.fn(async () => {
      throw new TypeError("fetch failed");
    });
    const outcome = await fetchMatch(REQUEST, new AbortController().signal, fetchImpl);
    expect(outcome).toEqual({
      ok: false,
      error: { status: 0, message: "連不上伺服器", warnings: [], engineDown: true },
    });
  });

  describe("被呼叫端取消（AbortController）不算引擎離線", () => {
    const CANCELLED = {
      ok: false,
      error: { status: 0, message: "已取消", warnings: [], engineDown: false },
    };

    it("回應前就被取消 → 已取消、engineDown false", async () => {
      const controller = new AbortController();
      const fetchImpl: FetchLike = vi.fn(
        (_input, init) =>
          new Promise<Response>((_, reject) => {
            init!.signal!.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
          }),
      );
      const pending = fetchMatch(REQUEST, controller.signal, fetchImpl);
      controller.abort();
      expect(await pending).toEqual(CANCELLED);
    });

    it("讀 body 時被取消（200）→ 已取消，不是 502", async () => {
      const controller = new AbortController();
      const res = {
        ok: true,
        status: 200,
        json: () =>
          new Promise<unknown>((_, reject) => {
            controller.signal.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
          }),
      } as unknown as Response;
      const pending = fetchMatch(REQUEST, controller.signal, vi.fn(async () => res));
      await Promise.resolve();
      controller.abort();
      expect(await pending).toEqual(CANCELLED);
    });

    it("讀 body 時被取消（HTTP 錯誤）→ 已取消，不是 503／engineDown", async () => {
      const controller = new AbortController();
      const res = {
        ok: false,
        status: 503,
        json: () =>
          new Promise<unknown>((_, reject) => {
            controller.signal.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
          }),
      } as unknown as Response;
      const pending = fetchMatch(REQUEST, controller.signal, vi.fn(async () => res));
      await Promise.resolve();
      controller.abort();
      expect(await pending).toEqual(CANCELLED);
    });

    it("沒被取消的網路錯誤仍然是 engineDown", async () => {
      const fetchImpl: FetchLike = vi.fn(async () => {
        throw new TypeError("fetch failed");
      });
      const outcome = await fetchMatch(REQUEST, new AbortController().signal, fetchImpl);
      expect(outcome.ok === false && outcome.error.engineDown).toBe(true);
    });
  });
});

describe("toMatchError", () => {
  it("detail 是字串（舊式 FastAPI HTTPException）也直接顯示", () => {
    expect(toMatchError(400, { detail: "圖形超出台灣" })).toEqual({
      status: 400,
      message: "圖形超出台灣",
      warnings: [],
      engineDown: false,
    });
  });

  it("body 不是物件或沒有 detail → 預設訊息；不認得的狀態碼帶 HTTP 狀態", () => {
    expect(toMatchError(504, null).message).toBe("吸附超過時間上限，請縮小圖形或減少筆畫");
    expect(toMatchError(400, {}).message).toBe("圖形不在台灣範圍內（含金門、馬祖）");
    expect(toMatchError(418, "teapot").message).toBe("吸附失敗（HTTP 418）");
  });

  it("detail.warnings 是 null（引擎 OpenAPI 宣告 array | null）→ 保留引擎的訊息，warnings 當空陣列", () => {
    const detail = { code: "timeout", message: "吸附超過 5 秒", warnings: null };
    expect(toMatchError(504, { detail })).toEqual({
      status: 504,
      message: "吸附超過 5 秒",
      warnings: [],
      engineDown: false,
    });
  });

  it("detail 物件缺 message 或 warnings 格式錯 → 預設訊息，不丟例外", () => {
    const fallback = {
      status: 422,
      message: "輸入不合法，或所有部件都吸附失敗",
      warnings: [],
      engineDown: false,
    };
    expect(toMatchError(422, { detail: { code: "no_route" } })).toEqual(fallback);
    const malformed = { code: "no_route", message: "壞掉的 warnings", warnings: [{ stroke_index: "x" }] };
    expect(toMatchError(422, { detail: malformed })).toEqual(fallback);
  });
});

describe("fetchStatus", () => {
  it("有路網 → build id", async () => {
    const body = { data: { build_id: "20261010T145928Z", osm_md5: "abc", created_at: "2026-10-10T15:00:09Z" } };
    expect(await fetchStatus(respond(200, body))).toBe("20261010T145928Z");
  });

  it("沒有路網、503、連不上都回傳 null", async () => {
    expect(await fetchStatus(respond(200, { data: null }))).toBeNull();
    expect(
      await fetchStatus(respond(503, { detail: { code: "engine_unreachable", message: "引擎無法連線" } })),
    ).toBeNull();
    const down: FetchLike = vi.fn(async () => {
      throw new TypeError("fetch failed");
    });
    expect(await fetchStatus(down)).toBeNull();
  });
});
