import { describe, expect, it } from "vitest";
import type { MatchError } from "@/lib/engine/client";
import type { MatchResponse } from "@/lib/engine/contract";
import { SAMPLE_MATCH_RESPONSE } from "@/lib/engine/fixtures";
import { buildGpx, gpxFileName } from "@/lib/export/gpx";
import type { Point, Shape, Stroke } from "@/lib/shape/types";
import {
  NO_OUTCOME,
  canDownloadGpx,
  gpxName,
  isSavable,
  outcomeWarnings,
  shapeKey,
  trackOutcome,
  visibleOutcome,
  type Outcome,
} from "@/lib/studio/state";

function points(n: number): Point[] {
  return Array.from({ length: n }, (_, i) => [i / n, 0.5]);
}

const line: Stroke = { points: [[0, 0], [0.5, 0]], closed: false };
const triangle: Stroke = { points: [[-0.5, 0.5], [0.5, 0.5], [0, -0.5]], closed: true };

function withPoints(n: number): MatchResponse {
  return { ...SAMPLE_MATCH_RESPONSE, route: { points: SAMPLE_MATCH_RESPONSE.route.points.slice(0, n), length_m: 1 } };
}

const ERROR: MatchError = {
  status: 422,
  message: "所有部件都吸附失敗",
  warnings: [{ stroke_index: 0, code: "no_route", message: "附近沒有路" }],
  engineDown: false,
};

const NONE: Outcome = { response: null, error: null };

describe("gpxName", () => {
  it("文字來源用文字本身（看不見的 emoji 選擇符去掉，跟畫出來的一致）", () => {
    expect(gpxName({ kind: "text", text: "I ♥️ TAIPEI", font: "futural" })).toBe("I ♥ TAIPEI");
  });

  it("兩行文字：GPX 名稱收成一行，檔名用 - 連接", () => {
    const name = gpxName({ kind: "text", text: "HI\r\nTHERE", font: "scripts" });
    expect(buildGpx([], name)).toContain("<name>HI THERE</name>");
    expect(gpxFileName(name)).toBe("artroute-hi-there.gpx");
  });

  it("圖片來源叫「圖片路線」", () => {
    const name = gpxName({ kind: "image", strokes: [triangle] });
    expect(name).toBe("圖片路線");
    expect(buildGpx([], name)).toContain("<name>圖片路線</name>");
    expect(gpxFileName(name)).toBe("artroute-route.gpx");
  });
});

describe("isSavable", () => {
  it("合法的文字可以存", () => {
    expect(isSavable({ kind: "text", text: "I ♥ TAIPEI", font: "futural" })).toBe(true);
  });

  it("不合法的文字不存（空白、不支援的字元、超過行數）", () => {
    expect(isSavable({ kind: "text", text: "  ", font: "futural" })).toBe(false);
    expect(isSavable({ kind: "text", text: "台北", font: "futural" })).toBe(false);
    expect(isSavable({ kind: "text", text: "A\nB\nC", font: "futural" })).toBe(false);
  });

  it("圖片要有筆畫才存", () => {
    expect(isSavable({ kind: "image", strokes: [triangle] })).toBe(true);
    expect(isSavable({ kind: "image", strokes: [] })).toBe(false);
  });

  it("圖片筆畫超出 /match 上限時不存（讀檔也會拒絕）", () => {
    expect(isSavable({ kind: "image", strokes: Array.from({ length: 41 }, () => line) })).toBe(false);
    expect(isSavable({ kind: "image", strokes: [{ points: points(2001), closed: false }] })).toBe(false);
    expect(isSavable({ kind: "image", strokes: [{ points: points(1), closed: true }] })).toBe(false);
    expect(isSavable({ kind: "image", strokes: Array.from({ length: 40 }, () => line) })).toBe(true);
  });
});

describe("canDownloadGpx", () => {
  it("有路線、至少 2 點、沒有在吸附中才能下載", () => {
    expect(canDownloadGpx(SAMPLE_MATCH_RESPONSE, false)).toBe(true);
    expect(canDownloadGpx(withPoints(2), false)).toBe(true);
  });

  it("沒有路線不能下載", () => {
    expect(canDownloadGpx(null, false)).toBe(false);
  });

  it("路線不到 2 點不能下載", () => {
    expect(canDownloadGpx(withPoints(1), false)).toBe(false);
    expect(canDownloadGpx(withPoints(0), false)).toBe(false);
  });

  it("吸附中（顯示的路線可能還是舊擺放的）不能下載", () => {
    expect(canDownloadGpx(SAMPLE_MATCH_RESPONSE, true)).toBe(false);
  });
});

describe("shapeKey", () => {
  const shape = (strokes: Stroke[]): Shape => ({ kind: "text", strokes });

  it("沒有圖形是 null", () => {
    expect(shapeKey(null)).toBeNull();
  });

  it("內容相同的圖形（不同物件）key 相同", () => {
    expect(shapeKey(shape([{ ...line }]))).toBe(shapeKey(shape([line])));
  });

  it("內容不同 key 就不同", () => {
    expect(shapeKey(shape([line]))).not.toBe(shapeKey(shape([triangle])));
    expect(shapeKey(shape([{ ...line, closed: true }]))).not.toBe(shapeKey(shape([line])));
  });
});

describe("trackOutcome / visibleOutcome", () => {
  const first: Outcome = { response: SAMPLE_MATCH_RESPONSE, error: null };

  it("新的結果記在目前的圖形底下，並且顯示", () => {
    const owned = trackOutcome(NO_OUTCOME, first, "A");
    expect(owned).toEqual({ ...first, shapeKey: "A" });
    expect(visibleOutcome(owned, first, "A")).toEqual(first);
  });

  it("同一個結果再出現時不改歸屬（回傳同一個物件）", () => {
    const owned = trackOutcome(NO_OUTCOME, first, "A");
    expect(trackOutcome(owned, first, "B")).toBe(owned);
  });

  it("結果被遮掉（request 為 null）時保留歸屬", () => {
    const owned = trackOutcome(NO_OUTCOME, first, "A");
    expect(trackOutcome(owned, NONE, null)).toBe(owned);
    expect(visibleOutcome(owned, NONE, null)).toEqual(NONE);
  });

  it("圖形從不合法變回另一個圖形：吸附中仍回傳舊路線時不顯示", () => {
    let owned = trackOutcome(NO_OUTCOME, first, "A");
    owned = trackOutcome(owned, NONE, null);
    owned = trackOutcome(owned, first, "B");
    expect(visibleOutcome(owned, first, "B")).toEqual(NONE);
  });

  it("變回同一個圖形（請求內容相同、不會重送）時照常顯示", () => {
    let owned = trackOutcome(NO_OUTCOME, first, "A");
    owned = trackOutcome(owned, NONE, null);
    owned = trackOutcome(owned, first, "A");
    expect(visibleOutcome(owned, first, "A")).toEqual(first);
  });

  it("新圖形的結果回來後顯示", () => {
    let owned = trackOutcome(NO_OUTCOME, first, "A");
    const second: Outcome = { response: { ...SAMPLE_MATCH_RESPONSE }, error: null };
    owned = trackOutcome(owned, second, "B");
    expect(visibleOutcome(owned, second, "B")).toEqual(second);
  });

  it("錯誤也一樣：圖形換了就不顯示舊錯誤", () => {
    const failed: Outcome = { response: null, error: ERROR };
    let owned = trackOutcome(NO_OUTCOME, failed, "A");
    expect(visibleOutcome(owned, failed, "A")).toEqual(failed);
    owned = trackOutcome(owned, failed, "B");
    expect(visibleOutcome(owned, failed, "B")).toEqual(NONE);
  });
});

describe("outcomeWarnings", () => {
  it("取 200 回應的 warnings", () => {
    expect(outcomeWarnings({ response: SAMPLE_MATCH_RESPONSE, error: null })).toBe(SAMPLE_MATCH_RESPONSE.warnings);
  });

  it("取 422 錯誤 body 的 warnings", () => {
    expect(outcomeWarnings({ response: null, error: ERROR })).toBe(ERROR.warnings);
  });

  it("都沒有時是空陣列", () => {
    expect(outcomeWarnings(NONE)).toEqual([]);
  });
});
