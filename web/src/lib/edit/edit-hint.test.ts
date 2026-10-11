import { describe, expect, it } from "vitest";
import { draftNoticeMessage, editHint } from "@/lib/edit/edit-hint";
import { editReducer, startEdit, type EditAction, type EditedRoute, type RoutePos } from "@/lib/edit/edit-state";
import { routeFromResponse } from "@/lib/edit/edit-bridge";
import { SAMPLE_MATCH_RESPONSE } from "@/lib/engine/fixtures";

const R0: EditedRoute = routeFromResponse(SAMPLE_MATCH_RESPONSE);
const P: RoutePos = { segment: 0, offsetM: 1, point: [121.5, 25.03] };
const run = (...actions: EditAction[]) => actions.reduce(editReducer, startEdit(R0, null));

describe("editHint", () => {
  it("依目前的步驟說明下一步", () => {
    expect(editHint(run())).toContain("選一個操作");
    expect(editHint(run({ type: "selectTool", tool: "cut" }))).toContain("剪掉");
    expect(editHint(run({ type: "selectTool", tool: "reroute" }))).toContain("第一個點");
    expect(editHint(run({ type: "selectTool", tool: "reroute" }, { type: "pick", pos: P }))).toContain("第二個點");
    expect(editHint(run({ type: "selectTool", tool: "reroute" }, { type: "pick", pos: P }, { type: "pick", pos: P }))).toContain("最短");
    expect(editHint(run({ type: "selectTool", tool: "drag" }))).toContain("拖到新位置");
  });

  it("等待引擎時顯示處理中；按了「完成」之後沒有說明", () => {
    expect(editHint(run({ type: "selectTool", tool: "cut" }, { type: "pick", pos: P }, { type: "submit" }))).toBe("引擎處理中…");
    expect(editHint(run({ type: "finish" }))).toBe("");
  });
});

describe("draftNoticeMessage", () => {
  it("超過上限講上限與去哪裡刪；存不了講清楚後果", () => {
    expect(draftNoticeMessage("over_limit")).toContain("20");
    expect(draftNoticeMessage("over_limit")).toContain("我的路線");
    expect(draftNoticeMessage("failed")).toContain("GPX");
  });
});
