import { describe, expect, it } from "vitest";
import { buildMatchRequest, shapeProblem } from "@/lib/match/request";
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
