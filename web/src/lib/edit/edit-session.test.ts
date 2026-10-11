import { IDBFactory } from "fake-indexeddb";
import { describe, expect, it, vi } from "vitest";
import { routeFromResponse } from "@/lib/edit/edit-bridge";
import { createRouteEditController } from "@/hooks/use-route-edit";
import { createEditSession, placementOf, sessionFromDraft, type EditSession } from "@/lib/edit/edit-session";
import type { EditOp } from "@/lib/edit/edit-state";
import type { EditOutcome } from "@/lib/engine/client";
import type { EditRequest } from "@/lib/engine/contract";
import { SAMPLE_EDIT_RESPONSE, SAMPLE_MATCH_RESPONSE } from "@/lib/engine/fixtures";
import { openDraftDb } from "@/lib/storage/drafts";
import { editSupersededBy } from "@/lib/studio/edit-view";
import { shapeKey, shapeOfSource } from "@/lib/studio/state";
import { transformStrokes } from "@/lib/shape/transform";

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
    const id = await session.saveDraft(R0, null, placementOf(SESSION));
    expect(await session.saveDraft({ ...R0, routeSig: "next" }, id, placementOf(SESSION))).toBe(id);
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

describe("sessionFromDraft", () => {
  const draft = { source: SESSION.source, transform: SESSION.transform };

  it("基準與 Studio 開著這個來源、擺放時算出來的一致：打開草稿不會被「圖形被移動」的判斷結束", () => {
    const session = sessionFromDraft(draft)!;
    const shape = shapeOfSource(draft.source)!;
    expect(editSupersededBy(session.base, { shapeKey: shapeKey(shape), transform: { ...draft.transform } })).toBe(false);
    expect(editSupersededBy(session.base, { shapeKey: shapeKey(shape), transform: { ...draft.transform, sizeM: 2100 } })).toBe(true);
  });

  it("原圖是草稿的圖形依草稿的擺放轉成經緯度（/edit 的「貼圖形」與分數用）", () => {
    const shape = shapeOfSource(draft.source)!;
    expect(sessionFromDraft(draft)!.strokes).toEqual(transformStrokes(shape.strokes, draft.transform));
  });

  it("來源不能成圖形：null", () => {
    expect(sessionFromDraft({ ...draft, source: { kind: "image", strokes: [] } })).toBeNull();
  });
});

describe("排隊中的草稿存檔不會用到下一次微調的圖形與擺放", () => {
  const SESSION_B: EditSession = {
    ...SESSION,
    source: { kind: "text", text: "B", font: "futural" },
    transform: { center: [121.6, 25.1], sizeM: 3000, rotationDeg: 10 },
  };
  const flush = () => new Promise((resolve) => setTimeout(resolve, 10));

  it("編輯成功後建立還在排隊（資料庫還沒開好），打開另一份草稿：建出來的是 A 的路線與 A 的圖形、擺放", async () => {
    const factory = new IDBFactory();
    let release!: () => void;
    const gate = new Promise<void>((resolve) => (release = resolve));
    const session = createEditSession({
      openDb: async () => {
        await gate;
        return openDraftDb(factory);
      },
      requestPersist: async () => {},
      notifyChanged: () => {},
      onStatus: () => {},
      post: async () => ({ ok: true, response: SAMPLE_EDIT_RESPONSE }),
    });
    const controller = createRouteEditController({
      postEdit: session.postEdit,
      saveDraft: session.saveDraft,
      onChange: () => {},
    });
    session.begin(SESSION);
    controller.enter(R0, null, placementOf(SESSION));
    controller.selectTool("cut");
    controller.pick({ segment: 0, offsetM: 10, point: [121.5, 25.03] });
    await flush(); // 編輯成功，create 排隊中（卡在 openDb）
    // 使用者在「我的路線」打開 B：begin 同步執行
    session.begin(SESSION_B);
    controller.enter(R0, "other", placementOf(SESSION_B));
    release();
    await flush();
    const drafts = await (await openDraftDb(factory)).list();
    expect(drafts).toHaveLength(1);
    expect(drafts[0].route.routeSig).toBe(SAMPLE_EDIT_RESPONSE.route_sig);
    expect(drafts[0].source).toEqual(SESSION.source);
    expect(drafts[0].transform).toEqual(SESSION.transform);
  });
});
