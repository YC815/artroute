import { fromLocal, toLocal } from "@/lib/geo/local-plane";
import type { ShapeBounds } from "@/lib/shape/normalize";
import type { LonLat, Point, Stroke } from "@/lib/shape/types";

/**
 * 圖形在地圖上的擺放。
 * sizeM：正規化座標 1 單位對應的公尺數（= 外框長邊的公尺數）。
 * rotationDeg：順時針角度，0 = 圖形的上方朝北。
 */
export type Transform = { center: LonLat; sizeM: number; rotationDeg: number };

export const MIN_SIZE_M = 200;
export const MAX_SIZE_M = 20_000;
/** 旋轉把手在圖形上緣再往外多遠（正規化單位）。 */
export const ROTATE_HANDLE_GAP = 0.12;

const RAD = Math.PI / 180;

/** 正規化座標 → 經緯度。 */
export function transformPoint([x, y]: Point, t: Transform): LonLat {
  const cos = Math.cos(t.rotationDeg * RAD);
  const sin = Math.sin(t.rotationDeg * RAD);
  const east = t.sizeM * (x * cos + y * sin);
  const north = t.sizeM * (-x * sin + y * cos);
  return fromLocal(t.center, east, north);
}

export function transformStrokes(strokes: Stroke[], t: Transform): { points: LonLat[]; closed: boolean }[] {
  return strokes.map((s) => ({ points: s.points.map((p) => transformPoint(p, t)), closed: s.closed }));
}

/** 目標圖形的總長（公尺），封閉筆畫含收尾那一段。旋轉不影響長度。 */
export function shapeLengthM(strokes: Stroke[], sizeM: number): number {
  let total = 0;
  for (const s of strokes) {
    const pts = s.closed ? [...s.points, s.points[0]] : s.points;
    for (let i = 1; i < pts.length; i++) {
      total += Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]);
    }
  }
  return total * sizeM;
}

/** 四個角落把手與旋轉把手的經緯度。角落順序：右上、左上、左下、右下（圖形座標）。 */
export function handlePositions(t: Transform, b: ShapeBounds): { corners: LonLat[]; rotate: LonLat } {
  const corners: Point[] = [
    [b.halfWidth, b.halfHeight],
    [-b.halfWidth, b.halfHeight],
    [-b.halfWidth, -b.halfHeight],
    [b.halfWidth, -b.halfHeight],
  ];
  return {
    corners: corners.map((p) => transformPoint(p, t)),
    rotate: transformPoint([0, b.halfHeight + ROTATE_HANDLE_GAP], t),
  };
}

/** 拖曳本體：圖形跟著指標平移。 */
export function moveBy(t: Transform, from: LonLat, to: LonLat): Transform {
  return { ...t, center: [t.center[0] + to[0] - from[0], t.center[1] + to[1] - from[1]] };
}

/** 拉角落把手：等比縮放，讓角落落在指標到中心的距離上。 */
export function scaleTo(t: Transform, b: ShapeBounds, pointer: LonLat): Transform {
  const [east, north] = toLocal(t.center, pointer);
  const cornerDistance = Math.hypot(b.halfWidth, b.halfHeight) || 1;
  const sizeM = Math.hypot(east, north) / cornerDistance;
  return { ...t, sizeM: Math.min(MAX_SIZE_M, Math.max(MIN_SIZE_M, sizeM)) };
}

/** 拉旋轉把手：圖形的上方轉向指標。 */
export function rotateTo(t: Transform, pointer: LonLat): Transform {
  const [east, north] = toLocal(t.center, pointer);
  // 指標正好在中心時方向未定義，atan2(0, 0) 會把角度歸零，這裡維持原角度
  if (east === 0 && north === 0) return t;
  const deg = Math.atan2(east, north) / RAD;
  return { ...t, rotationDeg: (deg + 360) % 360 };
}

/** 拖曳的種類：本體、角落把手、旋轉把手。 */
export type DragMode = "move" | "scale" | "rotate";

const DRAGS: Record<DragMode, (start: Transform, from: LonLat, to: LonLat, b: ShapeBounds) => Transform> = {
  move: (start, from, to) => moveBy(start, from, to),
  scale: (start, _from, to, b) => scaleTo(start, b, to),
  rotate: (start, _from, to) => rotateTo(start, to),
};

/** 拖曳中：從按下時的擺放 start、按下的位置 from，算出指標在 to 時的新擺放。 */
export function dragTransform(mode: DragMode, start: Transform, from: LonLat, to: LonLat, b: ShapeBounds): Transform {
  return DRAGS[mode](start, from, to, b);
}

// 用 Map 而不是物件：KeyboardEvent.key 可能是任意字串，物件會撞到 "constructor" 之類的原型屬性
const NUDGE = new Map<string, [number, number]>([
  ["ArrowUp", [0, 1]],
  ["ArrowDown", [0, -1]],
  ["ArrowLeft", [-1, 0]],
  ["ArrowRight", [1, 0]],
]);

export function isNudgeKey(key: string): boolean {
  return NUDGE.has(key);
}

/** 方向鍵微調：每次 10 m，按住 Shift 100 m。不是方向鍵時回傳 null。 */
export function nudge(t: Transform, key: string, big: boolean): Transform | null {
  const dir = NUDGE.get(key);
  if (!dir) return null;
  const step = big ? 100 : 10;
  return { ...t, center: fromLocal(t.center, dir[0] * step, dir[1] * step) };
}
