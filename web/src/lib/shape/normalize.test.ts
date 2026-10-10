import { describe, expect, it } from "vitest";
import { normalizeStrokes, shapeBounds } from "@/lib/shape/normalize";
import type { Stroke } from "@/lib/shape/types";

describe("normalizeStrokes", () => {
  it("中心移到原點、長邊變成 1、y 軸翻轉", () => {
    const raw: Stroke[] = [
      { points: [[10, 10], [30, 10]], closed: false },
      { points: [[10, 20], [30, 20]], closed: false },
    ];
    expect(normalizeStrokes(raw)).toEqual([
      { points: [[-0.5, 0.25], [0.5, 0.25]], closed: false },
      { points: [[-0.5, -0.25], [0.5, -0.25]], closed: false },
    ]);
  });

  it("保留 closed 與筆畫數", () => {
    const raw: Stroke[] = [{ points: [[0, 0], [4, 0], [4, 8]], closed: true }];
    const [s] = normalizeStrokes(raw);
    expect(s.closed).toBe(true);
    expect(s.points).toEqual([[-0.25, 0.5], [0.25, 0.5], [0.25, -0.5]]);
  });

  it("沒有點時回傳空陣列", () => {
    expect(normalizeStrokes([])).toEqual([]);
  });

  it("單點筆畫正規化成原點，不產生 NaN", () => {
    expect(normalizeStrokes([{ points: [[5, 5]], closed: false }])).toEqual([
      { points: [[0, 0]], closed: false },
    ]);
  });
});

describe("shapeBounds", () => {
  it("回傳半寬與半高", () => {
    const strokes: Stroke[] = [{ points: [[-0.5, 0.2], [0.5, -0.1]], closed: false }];
    expect(shapeBounds(strokes)).toEqual({ halfWidth: 0.5, halfHeight: 0.2 });
  });

  it("沒有任何筆畫時回傳零（不是 Infinity）", () => {
    expect(shapeBounds([])).toEqual({ halfWidth: 0, halfHeight: 0 });
  });

  it("只有空點列的筆畫也回傳零", () => {
    expect(shapeBounds([{ points: [], closed: false }])).toEqual({ halfWidth: 0, halfHeight: 0 });
  });
});
