import { describe, expect, it } from "vitest";
import { simplifyPolyline, simplifyToLimit } from "@/lib/geometry/simplify";
import type { Point } from "@/lib/shape/types";

describe("simplifyPolyline", () => {
  it("共線的中間點被移除", () => {
    expect(simplifyPolyline([[0, 0], [1, 0.01], [2, 0], [3, 0]], 0.1)).toEqual([[0, 0], [3, 0]]);
  });

  it("超過容許誤差的轉折點保留", () => {
    expect(simplifyPolyline([[0, 0], [1, 1], [2, 0]], 0.5)).toEqual([[0, 0], [1, 1], [2, 0]]);
  });

  it("首尾相同的封閉環也能簡化", () => {
    const square: Point[] = [[0, 0], [1, 0], [2, 0], [2, 2], [0, 2], [0, 1], [0, 0]];
    expect(simplifyPolyline(square, 0.1)).toEqual([[0, 0], [2, 0], [2, 2], [0, 2], [0, 0]]);
  });

  it("兩點以下原樣回傳", () => {
    expect(simplifyPolyline([[0, 0], [1, 1]], 1)).toEqual([[0, 0], [1, 1]]);
  });
});

describe("simplifyToLimit", () => {
  it("點數超過上限時加大容許誤差直到符合", () => {
    const zigzag: Point[] = Array.from({ length: 101 }, (_, i) => [i, i % 2 === 0 ? 0 : 1]);
    const result = simplifyToLimit(zigzag, 0.1, 10);
    expect(result.length).toBeLessThanOrEqual(10);
    expect(result[0]).toEqual([0, 0]);
    expect(result[result.length - 1]).toEqual([100, 0]);
  });

  it("沒超過上限時跟 simplifyPolyline 一樣", () => {
    const line: Point[] = [[0, 0], [1, 1], [2, 0]];
    expect(simplifyToLimit(line, 0.5, 10)).toEqual(simplifyPolyline(line, 0.5));
  });
});
