import { describe, expect, it } from "vitest";
import {
  defaultSelection,
  extractRings,
  imageShape,
  noRingsProblem,
  NO_RINGS_MESSAGE,
  traceRings,
  type Ring,
} from "@/lib/image/rings";
import { binaryMask } from "@/lib/image/threshold";
import { MAX_STROKE_POINTS, MIN_STROKE_POINTS } from "@/lib/limits";
import { shapeBounds } from "@/lib/shape/normalize";

/** 產生 width × height 的灰階影像：背景 255，rects 內塗 0。 */
function grayImage(width: number, height: number, rects: [number, number, number, number][]): Uint8Array {
  const gray = new Uint8Array(width * height).fill(255);
  for (const [x0, y0, w, h] of rects) {
    for (let y = y0; y < y0 + h; y++) gray.fill(0, y * width + x0, y * width + x0 + w);
  }
  return gray;
}

/** 邊緣有鋸齒的實心圓（黑底前景）：原始輪廓約 7500 點，超過引擎每筆 2000 點的上限。 */
function jaggedDisk(size: number): Uint8Array {
  const gray = new Uint8Array(size * size).fill(255);
  const radius = size * 0.4;
  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      const dx = x + 0.5 - size / 2;
      const dy = y + 0.5 - size / 2;
      if (Math.hypot(dx, dy) < radius + 6 * Math.sin(400 * Math.atan2(dy, dx))) gray[y * size + x] = 0;
    }
  }
  return gray;
}

describe("traceRings", () => {
  it("方塊外框與中間的洞各成一環", () => {
    // 8 × 6：x 2–6、y 1–4 的方塊，(4, 2) 挖一個洞
    const mask = new Array<number>(48).fill(0);
    for (let y = 1; y <= 4; y++) for (let x = 2; x <= 6; x++) mask[y * 8 + x] = 1;
    mask[2 * 8 + 4] = 0;
    const rings = traceRings(mask, 8, 6);
    expect(rings.map((r) => r.outer)).toEqual([true, false]);
    // 外框：5 × 4 的矩形切掉四個 0.5 × 0.5 的角（各 0.125）
    expect(rings[0].area).toBeCloseTo(19.5, 9);
    // 洞：半徑 0.5 的菱形
    expect(rings[1].area).toBeCloseTo(0.5, 9);
  });
});

describe("extractRings", () => {
  it("去掉雜點、簡化、依面積排序", () => {
    // 100 × 100：40 × 40 的大方塊、10 × 10 的小方塊、2 × 2 的雜點（面積 < 0.1%）
    const gray = grayImage(100, 100, [
      [10, 10, 40, 40],
      [70, 70, 10, 10],
      [90, 5, 2, 2],
    ]);
    const rings = extractRings(gray, 100, 100, 128, false);
    expect(rings).toHaveLength(2);
    expect(rings[0].area).toBeGreaterThan(rings[1].area);
    expect(rings.every((r) => r.outer)).toBe(true);
    // 方塊簡化後只剩切角的 8 個頂點
    expect(rings[0].points.length).toBeLessThanOrEqual(8);
    // 不重複起點
    const first = rings[0].points[0];
    const last = rings[0].points[rings[0].points.length - 1];
    expect(first).not.toEqual(last);
  });

  it("invert：白底變前景，外框是整張影像", () => {
    const gray = grayImage(50, 50, [[10, 10, 20, 20]]);
    const rings = extractRings(gray, 50, 50, 128, true);
    expect(rings[0].outer).toBe(true);
    expect(rings[0].area).toBeGreaterThan(2000);
    expect(rings.some((r) => !r.outer)).toBe(true);
  });
});

describe("noRingsProblem", () => {
  const ring: Ring = { points: [[0, 0], [1, 0], [0, 1]], area: 0.5, outer: true };

  it("處理完卻沒有輪廓：回傳說明", () => {
    expect(noRingsProblem(true, [])).toBe(NO_RINGS_MESSAGE);
    expect(NO_RINGS_MESSAGE).toBe("找不到輪廓，試試調整門檻或反轉");
  });

  it("還沒上傳或處理中（loaded 為 false）：不說沒有輪廓", () => {
    expect(noRingsProblem(false, [])).toBeNull();
  });

  it("有輪廓：沒有說明", () => {
    expect(noRingsProblem(true, [ring])).toBeNull();
  });

  it("沒有前景的影像抽不到輪廓（真的會走到這個提示）：全白不反轉、全黑反轉", () => {
    const white = grayImage(20, 20, []);
    const black = grayImage(20, 20, [[0, 0, 20, 20]]);
    expect(noRingsProblem(true, extractRings(white, 20, 20, 128, false))).toBe(NO_RINGS_MESSAGE);
    expect(noRingsProblem(true, extractRings(black, 20, 20, 128, true))).toBe(NO_RINGS_MESSAGE);
    // 反過來整張都是前景，有一個外框
    expect(noRingsProblem(true, extractRings(white, 20, 20, 128, true))).toBeNull();
  });
});

describe("defaultSelection", () => {
  const ring = (area: number, outer: boolean): Ring => ({ points: [[0, 0], [1, 0], [0, 1]], area, outer });

  it("選面積最大的外框，跳過洞", () => {
    expect(defaultSelection([ring(50, false), ring(20, true), ring(10, true)])).toEqual([1]);
  });

  it("沒有外框時不選", () => {
    expect(defaultSelection([])).toEqual([]);
  });
});

describe("imageShape", () => {
  it("每個選取的環是一個封閉筆畫，並正規化", () => {
    const rings: Ring[] = [
      { points: [[0, 0], [10, 0], [10, 10], [0, 10]], area: 100, outer: true },
      { points: [[20, 20], [24, 20], [24, 24]], area: 8, outer: true },
    ];
    const shape = imageShape(rings, [0]);
    expect(shape.kind).toBe("image");
    expect(shape.strokes).toEqual([
      { points: [[-0.5, 0.5], [0.5, 0.5], [0.5, -0.5], [-0.5, -0.5]], closed: true },
    ]);
    expect(shapeBounds(shape.strokes)).toEqual({ halfWidth: 0.5, halfHeight: 0.5 });
  });

  it("退化的環（少於 2 點）不產生筆畫", () => {
    const rings: Ring[] = [
      { points: [[5, 5]], area: 0, outer: true },
      { points: [[0, 0], [10, 0]], area: 0, outer: true },
    ];
    expect(imageShape(rings, [0]).strokes).toEqual([]);
    expect(imageShape(rings, [0, 1]).strokes.map((s) => s.points.length)).toEqual([2]);
  });

  it("首尾重複的環（traceRings 原始輸出）不重複起點", () => {
    const rings: Ring[] = [
      { points: [[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]], area: 100, outer: true },
    ];
    expect(imageShape(rings, [0]).strokes[0].points).toHaveLength(4);
  });

  it("略過無效的索引（越界、負數、非整數、NaN），不丟例外", () => {
    const rings: Ring[] = [{ points: [[0, 0], [10, 0], [10, 10], [0, 10]], area: 100, outer: true }];
    const selected = [5, -1, 0.5, Number.NaN, 0];
    expect(() => imageShape(rings, selected)).not.toThrow();
    expect(imageShape(rings, selected).strokes).toEqual(imageShape(rings, [0]).strokes);
  });

  it("重複的索引只產生一筆，保留第一次出現的順序", () => {
    const rings: Ring[] = [
      { points: [[0, 0], [10, 0], [10, 10], [0, 10]], area: 100, outer: true },
      { points: [[20, 20], [30, 20], [30, 30]], area: 50, outer: true },
    ];
    const shape = imageShape(rings, [1, 0, 1, 0]);
    expect(shape.strokes).toHaveLength(2);
    expect(shape.strokes).toEqual(imageShape(rings, [1, 0]).strokes);
    expect(shape.strokes).not.toEqual(imageShape(rings, [0, 1]).strokes);
  });

  it("超過 2000 點的原始環會簡化到上限內（用真實的 traceRings 輸出）", () => {
    const size = 400;
    // 鋸齒邊緣會切出小碎片，取面積最大的那一環（主體）
    const outer = traceRings(binaryMask(jaggedDisk(size), 128, false), size, size).sort((a, b) => b.area - a.area)[0];
    expect(outer.points.length).toBeGreaterThan(MAX_STROKE_POINTS);
    const { points } = imageShape([outer], [0]).strokes[0];
    expect(points.length).toBeGreaterThanOrEqual(MIN_STROKE_POINTS);
    expect(points.length).toBeLessThanOrEqual(MAX_STROKE_POINTS);
  });
});

describe("extractRings 的點數上限", () => {
  it("每個環都在 3–2000 點之間", () => {
    const size = 400;
    const rings = extractRings(jaggedDisk(size), size, size, 128, false);
    expect(rings.length).toBeGreaterThan(0);
    for (const ring of rings) {
      expect(ring.points.length).toBeGreaterThanOrEqual(3);
      expect(ring.points.length).toBeLessThanOrEqual(MAX_STROKE_POINTS);
    }
  });
});
