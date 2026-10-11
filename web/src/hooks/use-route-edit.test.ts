import { describe, expect, it } from "vitest";
import {
  createRouteEditController,
  type EditOutcome,
  type PostEdit,
  type SaveDraft,
} from "@/hooks/use-route-edit";
import { STALE_DATA_MESSAGE, toEditOutcome } from "@/lib/edit/edit-bridge";
import type { EditedRoute, EditOp, EditState, RoutePos } from "@/lib/edit/edit-state";
import { postEdit as postEditRequest, type FetchLike } from "@/lib/engine/client";
import { SAMPLE_EDIT_RESPONSE, SAMPLE_MATCH_RESPONSE } from "@/lib/engine/fixtures";

function route(sig: string): EditedRoute {
  return {
    segments: SAMPLE_MATCH_RESPONSE.segments,
    dataVersion: SAMPLE_MATCH_RESPONSE.data_version,
    routeSig: sig,
    score: SAMPLE_MATCH_RESPONSE.score,
    lengthM: SAMPLE_MATCH_RESPONSE.route.length_m,
  };
}

const R0 = route("sig-0");
const R1 = route("sig-1");
const R2 = route("sig-2");
const P: RoutePos = { segment: 0, offsetM: 12.5, point: [121.5001, 25.03] };
const Q: RoutePos = { segment: 2, offsetM: 40, point: [121.5025, 25.031] };

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

function setup() {
  const edits: { op: EditOp; route: EditedRoute; signal: AbortSignal; reply: ReturnType<typeof deferred<EditOutcome>> }[] = [];
  const saves: { route: EditedRoute; draftId: string | null; reply: ReturnType<typeof deferred<string | null>> }[] = [];
  const changes: (EditState | null)[] = [];
  const postEdit: PostEdit = (op, r, signal) => {
    const reply = deferred<EditOutcome>();
    edits.push({ op, route: r, signal, reply });
    return reply.promise;
  };
  const saveDraft: SaveDraft = (r, draftId) => {
    const reply = deferred<string | null>();
    saves.push({ route: r, draftId, reply });
    return reply.promise;
  };
  const controller = createRouteEditController({ postEdit, saveDraft, onChange: (s) => changes.push(s) });
  return { controller, edits, saves, changes, state: () => controller.getState()! };
}

/** 進入微調、選剪掉、點一下（送出）。 */
function cutOnce(t: ReturnType<typeof setup>, pos: RoutePos = P) {
  t.controller.selectTool("cut");
  t.controller.pick(pos);
}

describe("createRouteEditController", () => {
  it("剪掉：點一下就送出目前的路線與 op；成功後換上新路線並建立草稿", async () => {
    const t = setup();
    t.controller.enter(R0, null);
    cutOnce(t);
    expect(t.edits).toHaveLength(1);
    expect(t.edits[0].op).toEqual({ type: "cut", at: { segment: 0, offset_m: 12.5 } });
    expect(t.edits[0].route).toBe(R0);
    expect(t.state().pending).toBe(true);

    t.edits[0].reply.resolve({ ok: true, route: R1 });
    await flush();
    expect(t.state().history.present).toBe(R1);
    expect(t.state().pending).toBe(false);
    expect(t.saves.map((s) => [s.route, s.draftId])).toEqual([[R1, null]]);

    t.saves[0].reply.resolve("d1");
    await flush();
    expect(t.state().draftId).toBe("d1");
  });

  it("之後的編輯更新同一份草稿；前一次存檔還沒回來時等它（不會建出兩份）", async () => {
    const t = setup();
    t.controller.enter(R0, null);
    cutOnce(t);
    t.edits[0].reply.resolve({ ok: true, route: R1 });
    await flush();
    cutOnce(t, Q);
    t.edits[1].reply.resolve({ ok: true, route: R2 });
    await flush();
    expect(t.saves).toHaveLength(1);

    t.saves[0].reply.resolve("d1");
    await flush();
    expect(t.saves.map((s) => [s.route, s.draftId])).toEqual([
      [R1, null],
      [R2, "d1"],
    ]);
  });

  it("打開的草稿：第一次編輯就更新它", async () => {
    const t = setup();
    t.controller.enter(R0, "d9");
    cutOnce(t);
    t.edits[0].reply.resolve({ ok: true, route: R1 });
    await flush();
    expect(t.saves[0].draftId).toBe("d9");
  });

  it("存檔失敗（丟例外或回 null）不影響路線，下一次照樣存", async () => {
    const t = setup();
    t.controller.enter(R0, null);
    cutOnce(t);
    t.edits[0].reply.resolve({ ok: true, route: R1 });
    await flush();
    t.saves[0].reply.reject(new Error("QuotaExceededError"));
    await flush();
    cutOnce(t);
    t.edits[1].reply.resolve({ ok: true, route: R2 });
    await flush();
    expect(t.state().history.present).toBe(R2);
    expect(t.saves.map((s) => s.draftId)).toEqual([null, null]);
  });

  it("復原、重做後也存（草稿跟畫面上的路線一致）", async () => {
    const t = setup();
    t.controller.enter(R0, null);
    cutOnce(t);
    t.edits[0].reply.resolve({ ok: true, route: R1 });
    await flush();
    t.saves[0].reply.resolve("d1");
    await flush();
    t.controller.undo();
    await flush();
    t.controller.redo();
    await flush();
    expect(t.saves.slice(1).map((s) => [s.route, s.draftId])).toEqual([[R0, "d1"]]);
    t.saves[1].reply.resolve("d1");
    await flush();
    expect(t.saves.slice(1).map((s) => [s.route, s.draftId])).toEqual([
      [R0, "d1"],
      [R1, "d1"],
    ]);
  });

  it("引擎拒絕：保留原路線、顯示訊息、不存草稿", async () => {
    const t = setup();
    t.controller.enter(R0, null);
    cutOnce(t);
    t.edits[0].reply.resolve({ ok: false, code: "nothing_to_cut", message: "這裡沒有圈可以剪" });
    await flush();
    expect(t.state()).toMatchObject({ pending: false, message: "這裡沒有圈可以剪", errorCode: "nothing_to_cut" });
    expect(t.state().history.present).toBe(R0);
    expect(t.saves).toHaveLength(0);
  });

  it("409 stale_data：保留原路線、顯示「路網已更新」、帶著錯誤碼讓介面提供重新驗證；不存草稿", async () => {
    const t = setup();
    t.controller.enter(R0, null);
    cutOnce(t);
    t.edits[0].reply.resolve({ ok: false, code: "stale_data", message: STALE_DATA_MESSAGE });
    await flush();
    expect(t.state()).toMatchObject({ pending: false, errorCode: "stale_data", message: STALE_DATA_MESSAGE });
    expect(t.state().history.present).toBe(R0);
    expect(t.saves).toHaveLength(0);
    // 用完還能接著操作（例如先重新驗證，或改成別的操作）
    cutOnce(t, Q);
    expect(t.edits).toHaveLength(2);
    expect(t.state()).toMatchObject({ pending: true, errorCode: null, message: null });
  });

  it("接上真的 client：HTTP 409／422 與成功的回應各自走到對的狀態（整條 toEditOutcome 鏈）", async () => {
    const replies = [
      new Response(JSON.stringify({ detail: { code: "stale_data", message: "引擎的說法" } }), { status: 409 }),
      new Response(JSON.stringify({ detail: { code: "turn_not_rideable", message: "這個接點不能直騎" } }), { status: 422 }),
      new Response(JSON.stringify(SAMPLE_EDIT_RESPONSE), { status: 200 }),
    ];
    const bodies: unknown[] = [];
    const fetchImpl: FetchLike = async (_url, init) => {
      bodies.push(JSON.parse(String(init?.body)));
      return replies.shift()!;
    };
    const controller = createRouteEditController({
      postEdit: async (op, r, signal) =>
        toEditOutcome(
          await postEditRequest(
            { segments: r.segments, data_version: r.dataVersion, route_sig: r.routeSig, strokes: [{ points: [[0, 0], [1, 1]], closed: false }], op },
            signal,
            fetchImpl,
          ),
        ),
      saveDraft: async () => "d1",
      onChange: () => {},
    });
    controller.enter(R0, null);
    for (const expected of [
      { errorCode: "stale_data", message: STALE_DATA_MESSAGE },
      { errorCode: "turn_not_rideable", message: "這個接點不能直騎" },
    ]) {
      controller.selectTool("cut");
      controller.pick(P);
      await flush();
      expect(controller.getState()).toMatchObject({ pending: false, ...expected });
      expect(controller.getState()!.history.present).toBe(R0);
    }
    controller.selectTool("cut");
    controller.pick(P);
    await flush();
    expect(controller.getState()!.history.present.routeSig).toBe(SAMPLE_EDIT_RESPONSE.route_sig);
    expect(bodies[0]).toMatchObject({ route_sig: "sig-0", data_version: R0.dataVersion, op: { type: "cut" } });
  });

  it("replaceRoute：換成新路線、abort 在途的請求、更新打開的草稿", async () => {
    const t = setup();
    t.controller.enter(R0, "d9");
    cutOnce(t);
    t.controller.replaceRoute(R2);
    expect(t.edits[0].signal.aborted).toBe(true);
    expect(t.state()).toMatchObject({ pending: false });
    expect(t.state().history).toEqual({ past: [], present: R2, future: [] });
    await flush();
    expect(t.saves.map((s) => [s.route, s.draftId])).toEqual([[R2, "d9"]]);
    t.edits[0].reply.resolve({ ok: true, route: R1 });
    await flush();
    expect(t.state().history.present).toBe(R2);
  });

  it("replaceRoute：還沒存過草稿的微調不會因為驗證而建立草稿；存過了（id 還沒回來）就接著存", async () => {
    const t = setup();
    t.controller.enter(R0, null);
    t.controller.replaceRoute(R1);
    await flush();
    expect(t.saves).toHaveLength(0);

    cutOnce(t);
    t.edits[0].reply.resolve({ ok: true, route: R2 });
    await flush();
    t.controller.replaceRoute(R1);
    t.saves[0].reply.resolve("d1");
    await flush();
    expect(t.saves.map((s) => [s.route, s.draftId])).toEqual([[R2, null], [R1, "d1"]]);
  });

  it("replaceRoute：按了「完成」之後不做事", async () => {
    const t = setup();
    t.controller.enter(R0, "d9");
    t.controller.finish();
    t.controller.replaceRoute(R2);
    await flush();
    expect(t.state().history.present).toBe(R0);
    expect(t.saves).toHaveLength(0);
  });

  it("postEdit 丟例外時不會一直卡在等待中", async () => {
    const t = setup();
    t.controller.enter(R0, null);
    cutOnce(t);
    t.edits[0].reply.reject(new TypeError("Failed to fetch"));
    await flush();
    expect(t.state().pending).toBe(false);
    expect(t.state().message).toBeTruthy();
    expect(t.state().history.present).toBe(R0);
  });

  it("等待中不送新的操作、不能復原", async () => {
    const t = setup();
    t.controller.enter(R0, null);
    cutOnce(t);
    t.edits[0].reply.resolve({ ok: true, route: R1 });
    await flush();
    cutOnce(t, Q);
    t.controller.pick(P);
    t.controller.undo();
    expect(t.edits).toHaveLength(2);
    expect(t.state().history.present).toBe(R1);
  });

  it("重畫：兩個點之後選了模式才送出", () => {
    const t = setup();
    t.controller.enter(R0, null);
    t.controller.selectTool("reroute");
    t.controller.pick(Q);
    t.controller.pick(P);
    expect(t.edits).toHaveLength(0);
    t.controller.chooseMode("shortest");
    expect(t.edits.map((e) => e.op)).toEqual([
      { type: "reroute", from: { segment: 2, offset_m: 40 }, to: { segment: 0, offset_m: 12.5 }, mode: "shortest" },
    ]);
  });

  it("拖曳：放開就送出", () => {
    const t = setup();
    t.controller.enter(R0, null);
    t.controller.selectTool("drag");
    t.controller.drag(P, [121.6, 25.1]);
    expect(t.edits.map((e) => e.op)).toEqual([{ type: "drag", grab: { segment: 0, offset_m: 12.5 }, to: [121.6, 25.1] }]);
  });

  it("完成（離開微調）時 abort 在途的請求，晚到的結果不套用；路線留著，回到微調可以接著做", async () => {
    const t = setup();
    t.controller.enter(R0, null);
    cutOnce(t);
    t.controller.finish();
    expect(t.edits[0].signal.aborted).toBe(true);
    t.edits[0].reply.resolve({ ok: true, route: R1 });
    await flush();
    expect(t.state()).toMatchObject({ active: false, pending: false });
    expect(t.state().history.present).toBe(R0);
    expect(t.saves).toHaveLength(0);

    t.controller.resume();
    cutOnce(t);
    expect(t.edits).toHaveLength(2);
    expect(t.edits[1].signal.aborted).toBe(false);
  });

  it("等待中按 Esc：abort 請求，晚到的結果不套用", async () => {
    const t = setup();
    t.controller.enter(R0, null);
    cutOnce(t);
    t.controller.cancel();
    expect(t.edits[0].signal.aborted).toBe(true);
    expect(t.state().pending).toBe(false);
    t.edits[0].reply.resolve({ ok: true, route: R1 });
    await flush();
    expect(t.state().history.present).toBe(R0);
  });

  it("Esc 後馬上送新的操作：舊請求在新請求等待中才回來，也不會被當成新請求的結果", async () => {
    const t = setup();
    t.controller.enter(R0, null);
    cutOnce(t);
    t.controller.cancel();
    t.controller.pick(Q);
    expect(t.state().pending).toBe(true);
    t.edits[0].reply.resolve({ ok: true, route: R1 });
    await flush();
    expect(t.state().pending).toBe(true);
    expect(t.state().history.present).toBe(R0);
    expect(t.saves).toHaveLength(0);
    t.edits[1].reply.resolve({ ok: true, route: R2 });
    await flush();
    expect(t.state().history.present).toBe(R2);
  });

  it("reset（圖形移動、回到自動吸附）：abort 在途的請求、清掉狀態", async () => {
    const t = setup();
    t.controller.enter(R0, null);
    cutOnce(t);
    t.controller.reset();
    expect(t.edits[0].signal.aborted).toBe(true);
    expect(t.controller.getState()).toBeNull();
    t.edits[0].reply.resolve({ ok: true, route: R1 });
    await flush();
    expect(t.controller.getState()).toBeNull();
  });

  it("重新進入別的路線：上一次的草稿 id 晚到也不會套到這一次", async () => {
    const t = setup();
    t.controller.enter(R0, null);
    cutOnce(t);
    t.edits[0].reply.resolve({ ok: true, route: R1 });
    await flush();
    t.controller.enter(R2, null);
    t.saves[0].reply.resolve("d1");
    await flush();
    expect(t.state().draftId).toBeNull();
    expect(t.state().history.present).toBe(R2);
  });

  it("卸載（dispose）：abort 在途的請求，之後的結果與存檔都不再通知", async () => {
    const t = setup();
    t.controller.enter(R0, null);
    cutOnce(t);
    t.edits[0].reply.resolve({ ok: true, route: R1 });
    await flush();
    cutOnce(t, Q);
    t.controller.dispose();
    expect(t.edits[1].signal.aborted).toBe(true);
    const seen = t.changes.length;
    t.edits[1].reply.resolve({ ok: true, route: R2 });
    t.saves[0].reply.resolve("d1");
    await flush();
    expect(t.changes).toHaveLength(seen);
    t.controller.selectTool("drag");
    expect(t.changes).toHaveLength(seen);
  });

  it("沒有在微調時的操作都不做事", () => {
    const t = setup();
    t.controller.selectTool("cut");
    t.controller.pick(P);
    t.controller.undo();
    t.controller.resume();
    expect(t.controller.getState()).toBeNull();
    expect(t.edits).toHaveLength(0);
    expect(t.changes).toHaveLength(0);
  });
});
