import { describe, expect, it } from "vitest";
import { fromLocal, toLocal } from "@/lib/geo/local-plane";
import {
  MAX_SIZE_M,
  dragTransform,
  isNudgeKey,
  MIN_SIZE_M,
  ROTATE_HANDLE_GAP,
  handlePositions,
  moveBy,
  nudge,
  rotateTo,
  scaleTo,
  shapeLengthM,
  transformPoint,
  transformStrokes,
  type Transform,
} from "@/lib/shape/transform";
import type { LonLat, Stroke } from "@/lib/shape/types";

const CENTER: LonLat = [121.5654, 25.033];
const T0: Transform = { center: CENTER, sizeM: 1000, rotationDeg: 0 };

function local(p: LonLat): [number, number] {
  return toLocal(CENTER, p);
}

describe("transformPoint", () => {
  it("沒旋轉：x 往東、y 往北，乘上 sizeM", () => {
    const [e, n] = local(transformPoint([0.5, 0.25], T0));
    expect(e).toBeCloseTo(500, 6);
    expect(n).toBeCloseTo(250, 6);
  });

  it("順時針轉 90°：圖形的上方朝東", () => {
    const [e, n] = local(transformPoint([0, 0.5], { ...T0, rotationDeg: 90 }));
    expect(e).toBeCloseTo(500, 6);
    expect(n).toBeCloseTo(0, 6);
  });

  it("原點對到 center", () => {
    expect(transformPoint([0, 0], T0)).toEqual(CENTER);
  });
});

describe("transformStrokes", () => {
  it("保留 closed，逐點轉換", () => {
    const strokes: Stroke[] = [{ points: [[0, 0], [0.5, 0]], closed: true }];
    const [s] = transformStrokes(strokes, T0);
    expect(s.closed).toBe(true);
    expect(s.points[0]).toEqual(CENTER);
    expect(local(s.points[1])[0]).toBeCloseTo(500, 6);
  });
});

describe("shapeLengthM", () => {
  it("開放筆畫只算線段；封閉筆畫加上收尾段", () => {
    const open: Stroke = { points: [[0, 0], [0.3, 0], [0.3, 0.4]], closed: false };
    const closed: Stroke = { ...open, closed: true };
    expect(shapeLengthM([open], 1000)).toBeCloseTo(700, 9);
    expect(shapeLengthM([closed], 1000)).toBeCloseTo(1200, 9);
  });
});

describe("把手與拖曳", () => {
  const bounds = { halfWidth: 0.5, halfHeight: 0.25 };

  it("角落與旋轉把手的位置", () => {
    const h = handlePositions(T0, bounds);
    const [e0, n0] = local(h.corners[0]);
    expect(e0).toBeCloseTo(500, 6);
    expect(n0).toBeCloseTo(250, 6);
    const [er, nr] = local(h.rotate);
    expect(er).toBeCloseTo(0, 6);
    expect(nr).toBeCloseTo((0.25 + ROTATE_HANDLE_GAP) * 1000, 6);
  });

  it("moveBy 依經緯度差平移中心", () => {
    const moved = moveBy(T0, [121.5, 25], [121.51, 25.02]);
    expect(moved.center[0]).toBeCloseTo(CENTER[0] + 0.01, 12);
    expect(moved.center[1]).toBeCloseTo(CENTER[1] + 0.02, 12);
    expect(moved.sizeM).toBe(1000);
  });

  it("scaleTo 讓角落落在指標的距離上", () => {
    const corner = Math.hypot(0.5, 0.25);
    const pointer = fromLocal(CENTER, 0, corner * 3000);
    expect(scaleTo(T0, bounds, pointer).sizeM).toBeCloseTo(3000, 3);
  });

  it("scaleTo 夾在上下限之間", () => {
    expect(scaleTo(T0, bounds, CENTER).sizeM).toBe(MIN_SIZE_M);
    expect(scaleTo(T0, bounds, fromLocal(CENTER, 1e6, 0)).sizeM).toBe(MAX_SIZE_M);
  });

  it("rotateTo：指標在正東 → 90°，正西 → 270°", () => {
    expect(rotateTo(T0, fromLocal(CENTER, 100, 0)).rotationDeg).toBeCloseTo(90, 9);
    expect(rotateTo(T0, fromLocal(CENTER, -100, 0)).rotationDeg).toBeCloseTo(270, 9);
  });

  it("nudge：方向鍵 10 m、Shift 100 m，其他按鍵回傳 null", () => {
    const [e, n] = local(nudge(T0, "ArrowUp", false)!.center);
    expect(e).toBeCloseTo(0, 6);
    expect(n).toBeCloseTo(10, 6);
    expect(local(nudge(T0, "ArrowLeft", true)!.center)[0]).toBeCloseTo(-100, 6);
    expect(nudge(T0, "Enter", false)).toBeNull();
  });

  it("isNudgeKey 只認方向鍵", () => {
    expect(isNudgeKey("ArrowDown")).toBe(true);
    expect(isNudgeKey("a")).toBe(false);
    expect(isNudgeKey("constructor")).toBe(false);
  });

  it("dragTransform 依種類分派到 moveBy／scaleTo／rotateTo", () => {
    const from = fromLocal(CENTER, 0, 0);
    const to = fromLocal(CENTER, 300, 0);
    expect(dragTransform("move", T0, from, to, bounds)).toEqual(moveBy(T0, from, to));
    expect(dragTransform("scale", T0, from, to, bounds)).toEqual(scaleTo(T0, bounds, to));
    expect(dragTransform("rotate", T0, from, to, bounds)).toEqual(rotateTo(T0, to));
  });
});
