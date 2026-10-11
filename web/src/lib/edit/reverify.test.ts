import { describe, expect, it, vi } from "vitest";
import { routeFromResponse } from "@/lib/edit/edit-bridge";
import { IDLE_REVERIFY, reverifyFor, reverifyRoute, stateAfterReverify, verifyingState } from "@/lib/edit/reverify";
import type { EditError, EditOutcome, FetchLike } from "@/lib/engine/client";
import { postVerify } from "@/lib/engine/client";
import type { VerifyRequest } from "@/lib/engine/contract";
import { SAMPLE_EDIT_RESPONSE, SAMPLE_MATCH_RESPONSE } from "@/lib/engine/fixtures";

const OLD = routeFromResponse({ ...SAMPLE_MATCH_RESPONSE, data_version: "20260901T000000Z" });
const STROKES = [{ points: [[121.5, 25.03], [121.51, 25.03]] as [number, number][], closed: false }];
const signal = () => new AbortController().signal;
const fail = (overrides: Partial<EditError>): EditOutcome => ({
  ok: false,
  error: { status: 422, code: undefined, message: "說明", segments: [], engineDown: false, ...overrides },
});

describe("reverifyRoute", () => {
  it("送出舊版本的整條路線與簽章；通過回新版本的路線", async () => {
    const post = vi.fn<(r: VerifyRequest, s: AbortSignal) => Promise<EditOutcome>>(async () => ({ ok: true, response: SAMPLE_EDIT_RESPONSE }));
    const result = await reverifyRoute(OLD, STROKES, signal(), post);
    expect(result).toEqual({ kind: "ok", route: routeFromResponse(SAMPLE_EDIT_RESPONSE) });
    expect(post.mock.calls[0][0]).toEqual({
      segments: OLD.segments,
      data_version: "20260901T000000Z",
      route_sig: OLD.routeSig,
      strokes: STROKES,
    });
  });

  it("verify_failed：列出有問題的段落序號與說明", async () => {
    const result = await reverifyRoute(OLD, STROKES, signal(), async () =>
      fail({ code: "verify_failed", message: "有 2 段不能騎", segments: [1, 2] }),
    );
    expect(result).toEqual({ kind: "failed", segments: [1, 2], message: "有 2 段不能騎" });
  });

  it("逾時、503、簽章不合等其他錯誤：只回說明，不當成 verify_failed", async () => {
    for (const error of [{ status: 504, code: "timeout" }, { status: 503, code: "no_data" }, { status: 422, code: "invalid_route" }]) {
      expect(await reverifyRoute(OLD, STROKES, signal(), async () => fail({ ...error, segments: [3] }))).toEqual({
        kind: "error",
        message: "說明",
      });
    }
  });

  it("被取消：回 null，不論結果是什麼", async () => {
    const controller = new AbortController();
    const result = reverifyRoute(OLD, STROKES, controller.signal, async () => ({ ok: true, response: SAMPLE_EDIT_RESPONSE }));
    controller.abort();
    expect(await result).toBeNull();
  });

  it("接真的 client：HTTP 422 verify_failed 的 body 走到 failed", async () => {
    const fetchImpl: FetchLike = async () =>
      new Response(JSON.stringify({ detail: { code: "verify_failed", message: "不能騎", segments: [0] } }), { status: 422 });
    const result = await reverifyRoute(OLD, STROKES, signal(), (req, sig) => postVerify(req, sig, fetchImpl));
    expect(result).toEqual({ kind: "failed", segments: [0], message: "不能騎" });
  });
});

describe("reverify 狀態", () => {
  it("結果對應的狀態：通過回 idle；沒過帶說明與標紅的段；其他錯誤只有說明", () => {
    expect(stateAfterReverify("s", { kind: "ok", route: OLD })).toEqual(IDLE_REVERIFY);
    expect(stateAfterReverify("s", { kind: "failed", segments: [2], message: "不能騎" })).toEqual({
      sig: "s",
      status: "idle",
      message: "不能騎",
      bad: [2],
    });
    expect(stateAfterReverify("s", { kind: "error", message: "逾時" })).toEqual({ sig: "s", status: "idle", message: "逾時", bad: [] });
  });

  it("狀態只對它驗證的那條路線有效：路線換了（打開別的草稿、通過後換成新版）就看不到", () => {
    const failed = stateAfterReverify("s", { kind: "failed", segments: [2], message: "不能騎" });
    expect(reverifyFor(failed, "s")).toBe(failed);
    expect(reverifyFor(failed, "other")).toBe(IDLE_REVERIFY);
    expect(reverifyFor(failed, null)).toBe(IDLE_REVERIFY);
    expect(reverifyFor(verifyingState("s"), "s").status).toBe("verifying");
  });
});
