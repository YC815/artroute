import { contours } from "d3-contour";
import { simplifyToLimit } from "@/lib/geometry/simplify";
import { binaryMask } from "@/lib/image/threshold";
import { MAX_STROKE_POINTS, MIN_STROKE_POINTS } from "@/lib/limits";
import { normalizeStrokes } from "@/lib/shape/normalize";
import type { Point, Shape, Stroke } from "@/lib/shape/types";

/**
 * 一個輪廓環（像素座標，y 向下）。經過 extractRings 之後 points 不重複起點（首尾不相同）；
 * traceRings 的原始輸出首尾相同。outer 為 false 代表是洞。
 */
export type Ring = { points: Point[]; area: number; outer: boolean };

/** 面積小於影像面積這個比例的環視為雜點，丟掉。 */
const MIN_RING_AREA_RATIO = 0.001;
/** Douglas-Peucker 容許誤差 = 影像對角線 × 這個比例（至少 1 像素）。 */
const SIMPLIFY_RATIO = 0.002;

function polygonArea(points: Point[]): number {
  let twice = 0;
  for (let i = 0; i < points.length; i++) {
    const [x1, y1] = points[i];
    const [x2, y2] = points[(i + 1) % points.length];
    twice += x1 * y2 - x2 * y1;
  }
  return Math.abs(twice) / 2;
}

/** 遮罩（1 = 前景）→ 所有輪廓環。d3-contour 的每個 polygon：第一個環是外框，其餘是洞。環的首尾相同。 */
export function traceRings(mask: number[], width: number, height: number): Ring[] {
  const multiPolygon = contours().size([width, height]).contour(mask, 0.5);
  return multiPolygon.coordinates.flatMap((polygon) =>
    polygon.map((ring, i) => {
      const points = ring as Point[];
      return { points, area: polygonArea(points), outer: i === 0 };
    }),
  );
}

/** 灰階影像 → 可選的輪廓環：二值化、抽輪廓、去雜點、簡化（每環最多 MAX_STROKE_POINTS 點），依面積由大到小排序。 */
export function extractRings(
  gray: Uint8Array,
  width: number,
  height: number,
  threshold: number,
  invert: boolean,
): Ring[] {
  const minArea = width * height * MIN_RING_AREA_RATIO;
  const tolerance = Math.max(1, Math.hypot(width, height) * SIMPLIFY_RATIO);
  return traceRings(binaryMask(gray, threshold, invert), width, height)
    .filter((ring) => ring.area >= minArea)
    .map((ring) => ({
      ...ring,
      // d3 的環首尾相同：簡化時多留一點，再去掉重複的終點
      points: simplifyToLimit(ring.points, tolerance, MAX_STROKE_POINTS + 1).slice(0, -1),
    }))
    .filter((ring) => ring.points.length >= 3)
    .sort((a, b) => b.area - a.area);
}

/** 抽完輪廓卻一個也沒有時給使用者的說明（全白、全黑、透明背景配白色圖案等）。 */
export const NO_RINGS_MESSAGE = "找不到輪廓，試試調整門檻或反轉";

/** 影像已處理完（loaded）卻沒有任何輪廓時回傳說明，其他情況（還沒上傳、處理中、有輪廓）回傳 null。 */
export function noRingsProblem(loaded: boolean, rings: readonly Ring[]): string | null {
  return loaded && rings.length === 0 ? NO_RINGS_MESSAGE : null;
}

/** 預設選取：面積最大的外框（rings 已依面積排序，所以是第一個外框）。 */
export function defaultSelection(rings: Ring[]): number[] {
  const index = rings.findIndex((ring) => ring.outer);
  return index < 0 ? [] : [index];
}

/** 環 → 符合引擎契約的封閉筆畫：不重複起點，且 2–MAX_STROKE_POINTS 點。退化的環回傳 null。 */
function closedStroke(points: Point[]): Stroke | null {
  const n = points.length;
  const repeatsStart = n > 1 && points[0][0] === points[n - 1][0] && points[0][1] === points[n - 1][1];
  const open = repeatsStart ? points.slice(0, -1) : points;
  if (open.length < MIN_STROKE_POINTS) return null;
  // 點數超過上限時以 1 像素起跳加大容許誤差（Ring 是像素座標）
  const fitted = open.length > MAX_STROKE_POINTS ? simplifyToLimit(open, 1, MAX_STROKE_POINTS) : open;
  return { points: fitted, closed: true };
}

/**
 * 選取的環 → 正規化的 Shape，每個環是一個封閉筆畫。
 * 略過無效（越界、非整數）與重複的索引，重複的只保留第一次出現的位置；退化的環不產生筆畫，超過點數上限的環先簡化。
 */
export function imageShape(rings: Ring[], selected: number[]): Shape {
  const indices = [...new Set(selected)].filter((i) => Number.isInteger(i) && i >= 0 && i < rings.length);
  const strokes = indices.flatMap((i) => closedStroke(rings[i].points) ?? []);
  return { kind: "image", strokes: normalizeStrokes(strokes) };
}
