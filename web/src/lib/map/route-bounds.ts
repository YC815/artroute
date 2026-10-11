import type { LonLat } from "@/lib/shape/types";

/** [西南, 東北]（MapLibre 的 LngLatBoundsLike）。 */
export type Bounds = [LonLat, LonLat];
export type Px = { x: number; y: number };
export type Padding = { top: number; right: number; bottom: number; left: number };

/** 路線所有點的外框；沒有點時是 null。 */
export function routeBounds(segments: readonly { points: readonly LonLat[] }[]): Bounds | null {
  let west = Infinity;
  let south = Infinity;
  let east = -Infinity;
  let north = -Infinity;
  for (const { points } of segments) {
    for (const [lon, lat] of points) {
      west = Math.min(west, lon);
      east = Math.max(east, lon);
      south = Math.min(south, lat);
      north = Math.max(north, lat);
    }
  }
  return west > east ? null : [[west, south], [east, north]];
}

/**
 * 打開草稿時 fitBounds 的留白（像素）：下方留給工具列（約 3 成高，最多 200 px），其餘四周留一點。
 * 留白合計不能把畫面吃光（MapLibre 會報錯），所以都依畫面大小設上限。
 */
export function fitPadding(width: number, height: number): Padding {
  const side = Math.min(48, width / 8);
  return { top: Math.min(48, height / 8), right: side, bottom: Math.min(200, height * 0.3), left: side };
}

/** 螢幕上的外框（兩個對角點）是否整個落在畫面的有效範圍內（扣掉留白）。 */
export function fitsInView(a: Px, b: Px, width: number, height: number, padding: Padding): boolean {
  return (
    Math.min(a.x, b.x) >= padding.left &&
    Math.max(a.x, b.x) <= width - padding.right &&
    Math.min(a.y, b.y) >= padding.top &&
    Math.max(a.y, b.y) <= height - padding.bottom
  );
}
