import { describe, expect, it } from "vitest";
import { routeFromResponse } from "@/lib/edit/edit-bridge";
import { editReducer, startEdit, type EditedRoute } from "@/lib/edit/edit-state";
import { buildGpx } from "@/lib/export/gpx";
import { SAMPLE_EDIT_RESPONSE, SAMPLE_MATCH_RESPONSE } from "@/lib/engine/fixtures";
import type { Transform } from "@/lib/shape/transform";
import { canEnterEdit, editSupersededBy, liveMatchEnabled, resolveDisplayedRoute } from "@/lib/studio/edit-view";

const LIVE = { response: SAMPLE_MATCH_RESPONSE, downloadable: true };
const T: Transform = { center: [121.5, 25.03], sizeM: 2000, rotationDeg: 0 };

/** 微調後的路線：第 2 段換成另一條路（點與即時吸附的不同）。 */
const EDITED: EditedRoute = {
  ...routeFromResponse(SAMPLE_EDIT_RESPONSE),
  segments: SAMPLE_EDIT_RESPONSE.segments.map((s, i) =>
    i === 1 ? { ...s, points: [[121.501, 25.03], [121.5015, 25.0305], [121.502, 25.031]] } : s,
  ),
  routeSig: "sig-edited",
  lengthM: 2500,
};

const editing = (route = EDITED) => ({ ...startEdit(routeFromResponse(SAMPLE_MATCH_RESPONSE), null), history: { past: [], present: route, future: [] } });

describe("resolveDisplayedRoute", () => {
  it("沒有微調：用即時吸附的結果", () => {
    expect(resolveDisplayedRoute(null, LIVE, 2000)).toEqual({ ...LIVE, edited: false });
  });

  it("微調中：畫面、長度、分數與 GPX 的點都是微調後的路線，不是即時吸附的", () => {
    const shown = resolveDisplayedRoute(editing(), LIVE, 2000);
    expect(shown.edited).toBe(true);
    expect(shown.response?.route.length_m).toBe(2500);
    expect(shown.response?.route_sig).toBe("sig-edited");
    expect(shown.response?.route.points).toContainEqual([121.5015, 25.0305]);
    expect(shown.response?.route.points).not.toEqual(SAMPLE_MATCH_RESPONSE.route.points);
    const gpx = buildGpx(shown.response!.route.points, "x");
    expect(gpx).toContain('lat="25.030500" lon="121.501500"');
  });

  it("微調中：即時吸附已經過期（downloadable 為 false）也能下載微調後的路線", () => {
    expect(resolveDisplayedRoute(editing(), { response: null, downloadable: false }, null).downloadable).toBe(true);
  });

  it("按了「完成」之後（active: false）畫面仍是微調後的路線", () => {
    const finished = editReducer(editing(), { type: "finish" });
    expect(finished.active).toBe(false);
    expect(resolveDisplayedRoute(finished, LIVE, 2000).response?.route_sig).toBe("sig-edited");
  });

  it("復原後顯示歷史上一步的路線", () => {
    const first = routeFromResponse(SAMPLE_EDIT_RESPONSE);
    const state = editReducer({ ...editing(first), history: { past: [first], present: EDITED, future: [] } }, { type: "undo" });
    expect(resolveDisplayedRoute(state, LIVE, 2000).response?.route_sig).toBe(first.routeSig);
  });
});

describe("liveMatchEnabled", () => {
  it("有微調狀態（含按了「完成」）就不做即時吸附；沒有才做；引擎不可用一律不做", () => {
    expect(liveMatchEnabled(true, null)).toBe(true);
    expect(liveMatchEnabled(true, editing())).toBe(false);
    expect(liveMatchEnabled(true, editReducer(editing(), { type: "finish" }))).toBe(false);
    expect(liveMatchEnabled(false, null)).toBe(false);
  });
});

describe("editSupersededBy", () => {
  const base = { shapeKey: "k", transform: T };

  it("圖形與擺放都沒變：微調繼續", () => {
    expect(editSupersededBy(base, { shapeKey: "k", transform: T })).toBe(false);
  });

  it("移動、縮放、旋轉圖形（拖曳、鍵盤、套用候選）或改文字（key 變了）：微調結束", () => {
    expect(editSupersededBy(base, { shapeKey: "k", transform: { ...T, center: [121.6, 25.03] } })).toBe(true);
    expect(editSupersededBy(base, { shapeKey: "k", transform: { ...T, sizeM: 2100 } })).toBe(true);
    expect(editSupersededBy(base, { shapeKey: "k", transform: { ...T, rotationDeg: 5 } })).toBe(true);
    expect(editSupersededBy(base, { shapeKey: "other", transform: T })).toBe(true);
  });

  it("點一下圖形沒有移動（新物件、內容相同）不算移動", () => {
    expect(editSupersededBy(base, { shapeKey: "k", transform: { ...T, center: [...T.center] } })).toBe(false);
  });

  it("沒有微調時不算", () => {
    expect(editSupersededBy(null, { shapeKey: "k", transform: T })).toBe(false);
  });
});

describe("canEnterEdit", () => {
  const ok = { response: SAMPLE_MATCH_RESPONSE, downloadable: true, pending: false, edit: null };

  it("有屬於目前擺放的路線、沒有請求在途才能進入", () => {
    expect(canEnterEdit(ok)).toBe(true);
    expect(canEnterEdit({ ...ok, response: null })).toBe(false);
    expect(canEnterEdit({ ...ok, downloadable: false })).toBe(false);
    expect(canEnterEdit({ ...ok, pending: true })).toBe(false);
    expect(canEnterEdit({ ...ok, edit: editing() })).toBe(false);
  });
});
