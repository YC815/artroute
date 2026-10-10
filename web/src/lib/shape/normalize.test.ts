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
});

describe("shapeBounds", () => {
  it("回傳半寬與半高", () => {
    const strokes: Stroke[] = [{ points: [[-0.5, 0.2], [0.5, -0.1]], closed: false }];
    expect(shapeBounds(strokes)).toEqual({ halfWidth: 0.5, halfHeight: 0.2 });
  });
});
