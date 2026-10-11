import { flattenSegments } from "@/lib/edit/edit-bridge";
import type { LonLat } from "@/lib/shape/types";

/** 縮圖最多畫幾個點（路線有上萬點，列表上畫不出差別）。 */
const MAX_THUMBNAIL_POINTS = 240;

function sample(points: LonLat[], max: number): LonLat[] {
  if (points.length <= max) return points;
  const step = (points.length - 1) / (max - 1);
  return Array.from({ length: max }, (_, i) => points[Math.round(i * step)]);
}

/**
 * 路線 → SVG path 的 d（一條 polyline，單位是像素）：等比縮放置中放進 width × height（四周留 pad），
 * 經度依緯度縮短（cos），北在上。沒有點時回傳空字串；只有一個位置（或所有點重合）時畫成一個點大小的短線。
 */
export function thumbnailPath(
  segments: readonly { points: readonly LonLat[] }[],
  width: number,
  height: number,
  pad = 4,
): string {
  const points = sample(flattenSegments(segments), MAX_THUMBNAIL_POINTS);
  if (points.length === 0) return "";
  const lons = points.map((p) => p[0]);
  const lats = points.map((p) => p[1]);
  const minLon = Math.min(...lons);
  const minLat = Math.min(...lats);
  const maxLat = Math.max(...lats);
  const k = Math.cos((((minLat + maxLat) / 2) * Math.PI) / 180);
  const spanX = (Math.max(...lons) - minLon) * k;
  const spanY = maxLat - minLat;
  const room = { x: width - 2 * pad, y: height - 2 * pad };
  const scale = Math.min(spanX > 0 ? room.x / spanX : Infinity, spanY > 0 ? room.y / spanY : Infinity);
  const unit = Number.isFinite(scale) ? scale : 0;
  const offsetX = (width - spanX * unit) / 2;
  const offsetY = (height - spanY * unit) / 2;
  const coords = points.map((p) => [offsetX + (p[0] - minLon) * k * unit, offsetY + (maxLat - p[1]) * unit] as const);
  // 所有點重合：補一個相鄰的點，polyline 才畫得出來
  if (unit === 0) coords.push([coords[0][0] + 0.01, coords[0][1]]);
  return coords.map(([x, y], i) => `${i === 0 ? "M" : "L"}${x.toFixed(1)} ${y.toFixed(1)}`).join(" ");
}
