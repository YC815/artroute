import { describe, expect, it } from "vitest";
import { buildMatchRequest, requestKey, shapeProblem } from "@/lib/match/request";
import { transformPoint, type Transform } from "@/lib/shape/transform";
import type { Shape, Stroke } from "@/lib/shape/types";

const line: Stroke = { points: [[0, 0], [0.5, 0]], closed: false };
const shape = (strokes: Stroke[]): Shape => ({ kind: "text", strokes });

describe("shapeProblem", () => {
  it("合法圖形回傳 null", () => {
    expect(shapeProblem(shape([line]))).toBeNull();
  });

  it("沒有筆畫", () => {
    expect(shapeProblem(shape([]))).toBe("沒有任何筆畫");
  });

  it("超過 40 筆", () => {
    expect(shapeProblem(shape(Array.from({ length: 41 }, () => line)))).toBe(
      "筆畫有 41 筆，超過上限 40 筆：請減少字數或選取的輪廓",
    );
  });

  it("單筆超過 2000 點", () => {
    const long: Stroke = { points: Array.from({ length: 2001 }, (_, i) => [i, 0]), closed: false };
    expect(shapeProblem(shape([long]))).toBe("有筆畫超過 2000 點");
  });

  it("單筆不足 2 點（引擎要求每筆 2–2000 點）", () => {
    const dot: Stroke = { points: [[0, 0]], closed: false };
    const empty: Stroke = { points: [], closed: false };
    expect(shapeProblem(shape([line, dot]))).toBe("有筆畫少於 2 點");
    expect(shapeProblem(shape([empty]))).toBe("有筆畫少於 2 點");
  });

  it("剛好 2 點與 2000 點都合法", () => {
    const max: Stroke = { points: Array.from({ length: 2000 }, (_, i) => [i, 0]), closed: false };
    expect(shapeProblem(shape([line, max]))).toBeNull();
  });
});

describe("buildMatchRequest", () => {
  it("每筆轉成經緯度並保留 closed", () => {
    const t: Transform = { center: [121.5654, 25.033], sizeM: 1000, rotationDeg: 30 };
    const closed: Stroke = { points: [[0, 0], [0.5, 0], [0.5, 0.5]], closed: true };
    expect(buildMatchRequest(shape([closed]), t)).toEqual({
      strokes: [{ points: closed.points.map((p) => transformPoint(p, t)), closed: true }],
    });
  });
});

describe("buildMatchRequest 的 start_circle", () => {
  const t: Transform = { center: [121.5654, 25.033], sizeM: 1000, rotationDeg: 0 };

  it("沒有搜尋範圍時不帶 start_circle（與沒有自動搜尋時完全相同）", () => {
    expect(buildMatchRequest(shape([line]), t)).not.toHaveProperty("start_circle");
    expect(buildMatchRequest(shape([line]), t, null)).not.toHaveProperty("start_circle");
  });

  it("有搜尋範圍時帶上 start_circle；範圍不同 key 就不同（要重新吸附）", () => {
    const area = { center: [121.56, 25.04] as [number, number], radius_m: 3000 };
    const request = buildMatchRequest(shape([line]), t, area);
    expect(request.start_circle).toEqual(area);
    expect(requestKey(request)).not.toBe(requestKey(buildMatchRequest(shape([line]), t)));
    expect(requestKey(request)).not.toBe(requestKey(buildMatchRequest(shape([line]), t, { ...area, radius_m: 4000 })));
  });
});

describe("requestKey", () => {
  const t: Transform = { center: [121.5654, 25.033], sizeM: 1000, rotationDeg: 30 };

  it("內容相同的請求（各自建立的物件）key 相同", () => {
    expect(requestKey(buildMatchRequest(shape([line]), t))).toBe(requestKey(buildMatchRequest(shape([line]), { ...t })));
  });

  it("同一個圖形換了擺放位置（平移、縮放、旋轉）key 就不同", () => {
    const base = requestKey(buildMatchRequest(shape([line]), t));
    expect(requestKey(buildMatchRequest(shape([line]), { ...t, center: [121.57, 25.033] }))).not.toBe(base);
    expect(requestKey(buildMatchRequest(shape([line]), { ...t, sizeM: 1200 }))).not.toBe(base);
    expect(requestKey(buildMatchRequest(shape([line]), { ...t, rotationDeg: 31 }))).not.toBe(base);
  });
});
