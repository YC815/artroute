import { describe, expect, it } from "vitest";
import {
  canRedo,
  canUndo,
  EDIT_HISTORY_LIMIT,
  pushHistory,
  redo,
  startHistory,
  undo,
  type History,
} from "@/lib/edit/history";

function pushAll(h: History<number>, values: number[]): History<number> {
  return values.reduce(pushHistory, h);
}

describe("微調歷史", () => {
  it("一開始沒得復原也沒得重做", () => {
    const h = startHistory(0);
    expect(h).toEqual({ past: [], present: 0, future: [] });
    expect(canUndo(h)).toBe(false);
    expect(canRedo(h)).toBe(false);
  });

  it("復原、重做依序走回去再走回來", () => {
    const h = pushAll(startHistory(0), [1, 2]);
    const back1 = undo(h);
    const back2 = undo(back1);
    expect(back1.present).toBe(1);
    expect(back2).toEqual({ past: [], present: 0, future: [1, 2] });
    expect(canUndo(back2)).toBe(false);
    expect(canRedo(back2)).toBe(true);
    expect(redo(redo(back2))).toEqual(h);
  });

  it("復原後再做新的編輯，重做的紀錄清空", () => {
    const h = pushHistory(undo(pushAll(startHistory(0), [1, 2])), 9);
    expect(h).toEqual({ past: [0, 1], present: 9, future: [] });
    expect(canRedo(h)).toBe(false);
  });

  it(`最多能復原 ${EDIT_HISTORY_LIMIT} 步，超過丟掉最舊的`, () => {
    const values = Array.from({ length: EDIT_HISTORY_LIMIT + 1 }, (_, i) => i + 1);
    const h = pushAll(startHistory(0), values);
    expect(h.past).toHaveLength(EDIT_HISTORY_LIMIT);
    expect(h.past[0]).toBe(1);
    expect(h.present).toBe(EDIT_HISTORY_LIMIT + 1);

    let back = h;
    for (let i = 0; i < EDIT_HISTORY_LIMIT; i++) back = undo(back);
    expect(back.present).toBe(1);
    expect(canUndo(back)).toBe(false);
  });

  it("剛好 50 步時一步都不丟", () => {
    const h = pushAll(
      startHistory(0),
      Array.from({ length: EDIT_HISTORY_LIMIT }, (_, i) => i + 1),
    );
    expect(h.past).toHaveLength(EDIT_HISTORY_LIMIT);
    expect(h.past[0]).toBe(0);
  });

  it("沒得退或沒得進時回傳同一個物件", () => {
    const start = startHistory(0);
    expect(undo(start)).toBe(start);
    expect(redo(start)).toBe(start);
    const latest = pushHistory(start, 1);
    expect(redo(latest)).toBe(latest);
  });

  it("不改動傳入的歷史", () => {
    const h = pushAll(startHistory(0), [1, 2]);
    const snapshot = structuredClone(h);
    pushHistory(h, 3);
    undo(h);
    redo(undo(h));
    expect(h).toEqual(snapshot);
  });
});
