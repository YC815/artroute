import { IDBFactory } from "fake-indexeddb";
import { describe, expect, it, vi } from "vitest";
import { routeFromResponse } from "@/lib/edit/edit-bridge";
import { createEditSession, type EditSession } from "@/lib/edit/edit-session";
import type { EditOp } from "@/lib/edit/edit-state";
import type { EditOutcome } from "@/lib/engine/client";
import type { EditRequest } from "@/lib/engine/contract";
import { SAMPLE_EDIT_RESPONSE, SAMPLE_MATCH_RESPONSE } from "@/lib/engine/fixtures";
import { openDraftDb } from "@/lib/storage/drafts";

const R0 = routeFromResponse(SAMPLE_MATCH_RESPONSE);
const OP: EditOp = { type: "cut", at: { segment: 0, offset_m: 10 } };
const STROKES = [{ points: [[121.5, 25.03], [121.51, 25.03]] as [number, number][], closed: false }];
const SESSION: EditSession = {
  source: { kind: "text", text: "A", font: "futural" },
  transform: { center: [121.5, 25.03], sizeM: 2000, rotationDeg: 0 },
  strokes: STROKES,
  base: { shapeKey: "k", transform: { center: [121.5, 25.03], sizeM: 2000, rotationDeg: 0 } },
};

function setup() {
  const factory = new IDBFactory();
  const post = vi.fn<(request: EditRequest, signal: AbortSignal) => Promise<EditOutcome>>(async () => ({
    ok: true,
    response: SAMPLE_EDIT_RESPONSE,
  }));
  const session = createEditSession({
    openDb: () => openDraftDb(factory),
    requestPersist: async () => {},
    notifyChanged: () => {},
    onStatus: () => {},
    post,
  });
  return { session, post, factory };
}

describe("createEditSession", () => {
  it("postEdit：整條路線與簽章原封不動，加上 begin 當時使用者放的原圖", async () => {
    const { session, post } = setup();
    session.begin(SESSION);
    const outcome = await session.postEdit(OP, R0, new AbortController().signal);
    expect(outcome).toEqual({ ok: true, route: routeFromResponse(SAMPLE_EDIT_RESPONSE) });
    expect(post.mock.calls[0][0]).toEqual({
      segments: R0.segments,
      data_version: R0.dataVersion,
      route_sig: R0.routeSig,
      strokes: STROKES,
      op: OP,
    });
  });

  it("還沒 begin：不送請求，回失敗", async () => {
    const { session, post } = setup();
    expect(await session.postEdit(OP, R0, new AbortController().signal)).toMatchObject({ ok: false, code: "unexpected" });
    expect(post).not.toHaveBeenCalled();
  });

  it("saveDraft：建立的草稿記 begin 當時的來源與擺放；之後 update 同一份", async () => {
    const { session, factory } = setup();
    session.begin(SESSION);
    const id = await session.saveDraft(R0, null);
    expect(await session.saveDraft({ ...R0, routeSig: "next" }, id)).toBe(id);
    const drafts = await (await openDraftDb(factory)).list();
    expect(drafts).toHaveLength(1);
    expect(drafts[0]).toMatchObject({ source: SESSION.source, transform: SESSION.transform, route: { routeSig: "next" } });
  });

  it("base 是目前這次微調的基準；沒有 begin 是 null", () => {
    const { session } = setup();
    expect(session.base()).toBeNull();
    session.begin(SESSION);
    expect(session.base()).toBe(SESSION.base);
  });
});
