import { describe, expect, it } from "vitest";
import { thumbnailPath } from "@/lib/drafts/thumbnail";
import { fromLocal } from "@/lib/geo/local-plane";
import type { LonLat } from "@/lib/shape/types";

const O: LonLat = [121.5, 25.03];
const at = (east: number, north: number) => fromLocal(O, east, north);
const coords = (d: string) => d.split(/[ML]/).filter(Boolean).map((pair) => pair.trim().split(" ").map(Number));

describe("thumbnailPath", () => {
  it("沒有路線是空字串", () => {
    expect(thumbnailPath([], 100, 60)).toBe("");
  });

  it("北在上、等比縮放置中、四周留白：100 m 寬 × 50 m 高放進 100 × 60（pad 4）", () => {
    // 從西南角走到東南、東北：寬是高的兩倍
    const d = thumbnailPath([{ points: [at(0, 0), at(100, 0), at(100, 50)] }], 100, 60, 4);
    const [a, b, c] = coords(d);
    expect(a[0]).toBeCloseTo(4, 0);
    expect(b[0]).toBeCloseTo(96, 0);
    expect(b[0] - a[0]).toBeCloseTo(2 * (b[1] - c[1]), 0); // 等比（不被拉成填滿）
    expect(c[1]).toBeLessThan(b[1]); // 往北 = y 變小
    expect(a[1]).toBeCloseTo(b[1], 1);
    // 垂直置中：上下留白一樣
    expect(c[1]).toBeCloseTo(60 - b[1], 0);
  });

  it("相接的段落不重複畫接點", () => {
    const d = thumbnailPath([{ points: [at(0, 0), at(50, 0)] }, { points: [at(50, 0), at(100, 0)] }], 100, 60);
    expect(coords(d)).toHaveLength(3);
  });

  it("點很多時只取樣一部分，頭尾保留", () => {
    const many = Array.from({ length: 5000 }, (_, i) => at(i, Math.sin(i / 50) * 20));
    const d = thumbnailPath([{ points: many }], 100, 60);
    const pts = coords(d);
    expect(pts.length).toBeLessThanOrEqual(240);
    expect(pts[0][0]).toBeCloseTo(4, 0);
    expect(pts.at(-1)![0]).toBeCloseTo(96, 0);
  });

  it("所有點重合也畫得出（不是 NaN）", () => {
    expect(thumbnailPath([{ points: [O, O] }], 100, 60)).not.toMatch(/NaN|Infinity/);
  });

  it("一條完全水平（或垂直）的路線不會除以零", () => {
    expect(thumbnailPath([{ points: [at(0, 0), at(100, 0)] }], 100, 60)).not.toMatch(/NaN|Infinity/);
    expect(thumbnailPath([{ points: [at(0, 0), at(0, 100)] }], 100, 60)).not.toMatch(/NaN|Infinity/);
  });
});
