import { describe, expect, it } from "vitest";
import {
  buildEditOp,
  canRedoEdit,
  canUndoEdit,
  editReducer,
  startEdit,
  type EditAction,
  type EditedRoute,
  type EditState,
  type RoutePos,
} from "@/lib/edit/edit-state";
import { SAMPLE_MATCH_RESPONSE } from "@/lib/engine/fixtures";

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
const P: RoutePos = { segment: 0, offsetM: 12.5, point: [121.5001, 25.03] };
const Q: RoutePos = { segment: 2, offsetM: 40, point: [121.5025, 25.031] };

function run(state: EditState, ...actions: EditAction[]): EditState {
  return actions.reduce(editReducer, state);
}

describe("startEdit", () => {
  it("新的微調：沒有操作、沒有歷史、帶入草稿 id", () => {
    expect(startEdit(R0, "d1")).toEqual({
      active: true,
      history: { past: [], present: R0, future: [] },
      tool: null,
      picks: [],
      rerouteMode: null,
      dragTo: null,
      pending: false,
      message: null,
      errorCode: null,
      draftId: "d1",
    });
  });
});

describe("選操作與產生 op", () => {
  it("剪掉：點一下就能送出，op 與 spec §3.1 同形", () => {
    const s = run(startEdit(R0, null), { type: "selectTool", tool: "cut" }, { type: "pick", pos: P });
    expect(s.picks).toEqual([P]);
    expect(buildEditOp(s)).toEqual({ type: "cut", at: { segment: 0, offset_m: 12.5 } });
  });

  it("重畫：收兩個點後要再選模式才能送出；第三個點不收", () => {
    const one = run(startEdit(R0, null), { type: "selectTool", tool: "reroute" }, { type: "pick", pos: Q });
    expect(buildEditOp(one)).toBeNull();
    const two = editReducer(one, { type: "pick", pos: P });
    expect(two.picks).toEqual([Q, P]);
    expect(buildEditOp(two)).toBeNull();
    expect(editReducer(two, { type: "pick", pos: P })).toBe(two);
    const ready = editReducer(two, { type: "chooseMode", mode: "shape" });
    expect(buildEditOp(ready)).toEqual({
      type: "reroute",
      from: { segment: 2, offset_m: 40 },
      to: { segment: 0, offset_m: 12.5 },
      mode: "shape",
    });
  });

  it("重畫還沒收滿兩個點時不能選模式", () => {
    const one = run(startEdit(R0, null), { type: "selectTool", tool: "reroute" }, { type: "pick", pos: Q });
    expect(editReducer(one, { type: "chooseMode", mode: "shortest" })).toBe(one);
  });

  it("拖曳：抓點與放開的位置", () => {
    const s = run(startEdit(R0, null), { type: "selectTool", tool: "drag" }, { type: "drag", grab: P, to: [121.6, 25.1] });
    expect(buildEditOp(s)).toEqual({ type: "drag", grab: { segment: 0, offset_m: 12.5 }, to: [121.6, 25.1] });
  });

  it("沒選工具或工具不對時不收點、不收拖曳", () => {
    const none = startEdit(R0, null);
    expect(editReducer(none, { type: "pick", pos: P })).toBe(none);
    expect(editReducer(none, { type: "drag", grab: P, to: [121.6, 25.1] })).toBe(none);
    const drag = editReducer(none, { type: "selectTool", tool: "drag" });
    expect(editReducer(drag, { type: "pick", pos: P })).toBe(drag);
    const cut = editReducer(none, { type: "selectTool", tool: "cut" });
    expect(editReducer(cut, { type: "drag", grab: P, to: [121.6, 25.1] })).toBe(cut);
  });

  it("換工具清掉正在選的點與上一個錯誤", () => {
    const failed = run(
      startEdit(R0, null),
      { type: "selectTool", tool: "cut" },
      { type: "pick", pos: P },
      { type: "submit" },
      { type: "failed", code: "nothing_to_cut", message: "這裡沒有圈可以剪" },
    );
    const s = run(failed, { type: "selectTool", tool: "reroute" }, { type: "pick", pos: P }, { type: "selectTool", tool: "cut" });
    expect(s).toMatchObject({ tool: "cut", picks: [], rerouteMode: null, message: null, errorCode: null });
  });
});

describe("Esc（cancel）", () => {
  it("先清掉正在選的點，再按一次才取消工具", () => {
    const picked = run(startEdit(R0, null), { type: "selectTool", tool: "reroute" }, { type: "pick", pos: P });
    const once = editReducer(picked, { type: "cancel" });
    expect(once).toMatchObject({ tool: "reroute", picks: [] });
    expect(editReducer(once, { type: "cancel" }).tool).toBeNull();
  });

  it("什麼都沒選時不變", () => {
    const s = startEdit(R0, null);
    expect(editReducer(s, { type: "cancel" })).toBe(s);
  });

  it("等待中按 Esc：放棄這次操作（呼叫端負責 abort），路線不變", () => {
    const pending = run(startEdit(R0, null), { type: "selectTool", tool: "cut" }, { type: "pick", pos: P }, { type: "submit" });
    const s = editReducer(pending, { type: "cancel" });
    expect(s).toMatchObject({ pending: false, picks: [], tool: "cut" });
    expect(s.history.present).toBe(R0);
  });
});

describe("送出、成功、失敗", () => {
  const ready = run(startEdit(R0, null), { type: "selectTool", tool: "cut" }, { type: "pick", pos: P });

  it("送出進入等待；還不能送的時候不變", () => {
    expect(editReducer(ready, { type: "submit" }).pending).toBe(true);
    const notReady = editReducer(startEdit(R0, null), { type: "selectTool", tool: "cut" });
    expect(editReducer(notReady, { type: "submit" })).toBe(notReady);
  });

  it("等待中不能 undo／redo、不能送新操作、不能換工具或選點", () => {
    const edited = run(ready, { type: "submit" }, { type: "succeeded", route: R1 }, { type: "pick", pos: P }, { type: "submit" });
    expect(edited.pending).toBe(true);
    expect(canUndoEdit(edited)).toBe(false);
    for (const action of [
      { type: "undo" },
      { type: "redo" },
      { type: "submit" },
      { type: "selectTool", tool: "reroute" },
      { type: "pick", pos: Q },
      { type: "chooseMode", mode: "shortest" },
      { type: "drag", grab: P, to: [121.6, 25.1] },
    ] as EditAction[]) {
      expect(editReducer(edited, action)).toBe(edited);
    }
  });

  it("成功：新路線進歷史、清掉選的點與訊息、工具留著可以接著用", () => {
    const s = run(ready, { type: "submit" }, { type: "succeeded", route: R1 });
    expect(s.history.present).toBe(R1);
    expect(s.history.past).toEqual([R0]);
    expect(s).toMatchObject({ pending: false, picks: [], tool: "cut", message: null, errorCode: null });
    expect(canUndoEdit(s)).toBe(true);
  });

  it("沒在等待時收到的成功或失敗不套用", () => {
    expect(editReducer(ready, { type: "succeeded", route: R1 })).toBe(ready);
    expect(editReducer(ready, { type: "failed", code: "no_route", message: "x" })).toBe(ready);
  });

  it("失敗：保留路線、顯示訊息與錯誤碼；剪掉的點清掉", () => {
    const s = run(ready, { type: "submit" }, { type: "failed", code: "nothing_to_cut", message: "這裡沒有圈可以剪，可以改用「重畫」" });
    expect(s.history.present).toBe(R0);
    expect(s).toMatchObject({ pending: false, picks: [], message: "這裡沒有圈可以剪，可以改用「重畫」", errorCode: "nothing_to_cut" });
  });

  it("重畫失敗：兩個點留著、模式清掉，可以直接改選另一個模式", () => {
    const s = run(
      startEdit(R0, null),
      { type: "selectTool", tool: "reroute" },
      { type: "pick", pos: P },
      { type: "pick", pos: Q },
      { type: "chooseMode", mode: "shape" },
      { type: "submit" },
      { type: "failed", code: "no_route", message: "改用「最短」" },
    );
    expect(s).toMatchObject({ picks: [P, Q], rerouteMode: null, message: "改用「最短」" });
    expect(buildEditOp(editReducer(s, { type: "chooseMode", mode: "shortest" }))).toMatchObject({ mode: "shortest" });
  });

  it("選新的點或下一次送出時清掉上一個錯誤", () => {
    const failed = run(ready, { type: "submit" }, { type: "failed", code: "no_route", message: "x" });
    expect(editReducer(failed, { type: "pick", pos: P })).toMatchObject({ message: null, errorCode: null });

    const rerouteFailed = run(
      startEdit(R0, null),
      { type: "selectTool", tool: "reroute" },
      { type: "pick", pos: P },
      { type: "pick", pos: Q },
      { type: "chooseMode", mode: "shape" },
      { type: "submit" },
      { type: "failed", code: "no_route", message: "x" },
      { type: "chooseMode", mode: "shortest" },
    );
    expect(rerouteFailed.message).toBe("x");
    expect(editReducer(rerouteFailed, { type: "submit" })).toMatchObject({ pending: true, message: null, errorCode: null });
  });
});

describe("復原／重做", () => {
  const edited = run(
    startEdit(R0, null),
    { type: "selectTool", tool: "cut" },
    { type: "pick", pos: P },
    { type: "submit" },
    { type: "succeeded", route: R1 },
  );

  it("切回前一個／後一個路線，清掉正在選的點（位置是量在舊路線上的）", () => {
    const undone = run(edited, { type: "pick", pos: Q }, { type: "undo" });
    expect(undone.history.present).toBe(R0);
    expect(undone.picks).toEqual([]);
    expect(canRedoEdit(undone)).toBe(true);
    expect(editReducer(undone, { type: "redo" }).history.present).toBe(R1);
  });

  it("沒得退／進時回傳同一個物件", () => {
    expect(editReducer(edited, { type: "redo" })).toBe(edited);
    const start = startEdit(R0, null);
    expect(editReducer(start, { type: "undo" })).toBe(start);
  });
});

describe("離開與回到微調、草稿 id", () => {
  it("完成：離開微調（路線留著），之後的操作都不收；回到微調可以接著復原", () => {
    const edited = run(
      startEdit(R0, null),
      { type: "selectTool", tool: "cut" },
      { type: "pick", pos: P },
      { type: "submit" },
      { type: "succeeded", route: R1 },
    );
    const done = editReducer(edited, { type: "finish" });
    expect(done).toMatchObject({ active: false, tool: null, picks: [], pending: false });
    expect(done.history.present).toBe(R1);
    for (const action of [{ type: "undo" }, { type: "selectTool", tool: "cut" }, { type: "cancel" }] as EditAction[]) {
      expect(editReducer(done, action)).toBe(done);
    }
    const back = editReducer(done, { type: "resume" });
    expect(back.active).toBe(true);
    expect(editReducer(back, { type: "undo" }).history.present).toBe(R0);
  });

  it("等待中完成：放棄在途的操作", () => {
    const pending = run(startEdit(R0, null), { type: "selectTool", tool: "cut" }, { type: "pick", pos: P }, { type: "submit" });
    const done = editReducer(pending, { type: "finish" });
    expect(done.pending).toBe(false);
    expect(editReducer(done, { type: "succeeded", route: R1 })).toBe(done);
  });

  it("設定草稿 id；相同時不變", () => {
    const s = editReducer(startEdit(R0, null), { type: "setDraftId", draftId: "d1" });
    expect(s.draftId).toBe("d1");
    expect(editReducer(s, { type: "setDraftId", draftId: "d1" })).toBe(s);
  });
});
