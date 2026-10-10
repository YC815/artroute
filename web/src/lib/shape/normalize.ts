import type { Point, Stroke } from "@/lib/shape/types";

type Box = { minX: number; maxX: number; minY: number; maxY: number };

// 用迴圈而不是 Math.min(...xs)：圖片輪廓可能有上萬個點，展開參數會爆堆疊
function boundingBox(points: Point[]): Box {
  const box = { minX: Infinity, maxX: -Infinity, minY: Infinity, maxY: -Infinity };
  for (const [x, y] of points) {
    box.minX = Math.min(box.minX, x);
    box.maxX = Math.max(box.maxX, x);
    box.minY = Math.min(box.minY, y);
    box.maxY = Math.max(box.maxY, y);
  }
  return box;
}

/**
 * 來源座標（y 向下）→ 正規化座標（y 向上）：外框中心移到原點，長邊縮放成 1。
 * 文字和圖片共用這一個函式。
 */
export function normalizeStrokes(strokes: Stroke[]): Stroke[] {
  const points = strokes.flatMap((s) => s.points);
  if (points.length === 0) return [];
  const box = boundingBox(points);
  const scale = Math.max(box.maxX - box.minX, box.maxY - box.minY) || 1;
  const cx = (box.minX + box.maxX) / 2;
  const cy = (box.minY + box.maxY) / 2;
  return strokes.map((s) => ({
    closed: s.closed,
    points: s.points.map(([x, y]): Point => [(x - cx) / scale, (cy - y) / scale]),
  }));
}

/** 正規化圖形外框的半寬、半高（外框以原點為中心）。 */
export type ShapeBounds = { halfWidth: number; halfHeight: number };

export function shapeBounds(strokes: Stroke[]): ShapeBounds {
  const box = boundingBox(strokes.flatMap((s) => s.points));
  return {
    halfWidth: Math.max(0, Math.abs(box.minX), Math.abs(box.maxX)),
    halfHeight: Math.max(0, Math.abs(box.minY), Math.abs(box.maxY)),
  };
}
