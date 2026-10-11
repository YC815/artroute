import { describe, expect, it } from "vitest";
import { fitPadding, fitsInView, routeBounds } from "@/lib/map/route-bounds";

describe("routeBounds", () => {
  it("所有段落所有點的外框（西南、東北）", () => {
    expect(
      routeBounds([
        { points: [[121.5, 25.03], [121.52, 25.04]] },
        { points: [[121.49, 25.05], [121.51, 25.02]] },
      ]),
    ).toEqual([[121.49, 25.02], [121.52, 25.05]]);
  });

  it("單一點是零大小的外框；沒有點是 null", () => {
    expect(routeBounds([{ points: [[121.5, 25.03]] }])).toEqual([[121.5, 25.03], [121.5, 25.03]]);
    expect(routeBounds([])).toBeNull();
    expect(routeBounds([{ points: [] }])).toBeNull();
  });
});

describe("fitPadding", () => {
  it("大畫面：四周 48 px、下方最多 200 px", () => {
    expect(fitPadding(1200, 900)).toEqual({ top: 48, right: 48, bottom: 200, left: 48 });
  });

  it("小畫面（手機）：留白依畫面大小縮小，合計吃不光畫面", () => {
    const p = fitPadding(390, 400);
    expect(p).toEqual({ top: 48, right: 48, bottom: 120, left: 48 });
    expect(p.left + p.right).toBeLessThan(390);
    expect(p.top + p.bottom).toBeLessThan(400);
    const tiny = fitPadding(80, 80);
    expect(tiny.left + tiny.right).toBeLessThan(80);
    expect(tiny.top + tiny.bottom).toBeLessThan(80);
  });
});

describe("fitsInView", () => {
  const padding = { top: 10, right: 20, bottom: 100, left: 20 };

  it("整個外框在有效範圍內：true", () => {
    expect(fitsInView({ x: 30, y: 20 }, { x: 300, y: 250 }, 400, 400, padding)).toBe(true);
  });

  it("任何一邊超出（含壓到下方工具列的留白）：false；兩個對角點的順序不影響", () => {
    expect(fitsInView({ x: 300, y: 250 }, { x: 30, y: 20 }, 400, 400, padding)).toBe(true);
    expect(fitsInView({ x: 5, y: 20 }, { x: 300, y: 250 }, 400, 400, padding)).toBe(false);
    expect(fitsInView({ x: 30, y: 20 }, { x: 390, y: 250 }, 400, 400, padding)).toBe(false);
    expect(fitsInView({ x: 30, y: 20 }, { x: 300, y: 320 }, 400, 400, padding)).toBe(false);
    expect(fitsInView({ x: 30, y: 2 }, { x: 300, y: 250 }, 400, 400, padding)).toBe(false);
  });
});
