import type { Point } from "@/lib/shape/types";

/** 點到線段的距離；線段退化成一點時（例如封閉環的首尾）就是點到點的距離。 */
function distanceToSegment(p: Point, a: Point, b: Point): number {
  const dx = b[0] - a[0];
  const dy = b[1] - a[1];
  const lengthSquared = dx * dx + dy * dy;
  const t = lengthSquared === 0 ? 0 : ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / lengthSquared;
  const clamped = Math.max(0, Math.min(1, t));
  return Math.hypot(p[0] - (a[0] + clamped * dx), p[1] - (a[1] + clamped * dy));
}

function farthestPoint(points: Point[], start: number, end: number): { index: number; distance: number } {
  let best = { index: -1, distance: -1 };
  for (let i = start + 1; i < end; i++) {
    const distance = distanceToSegment(points[i], points[start], points[end]);
    if (distance > best.distance) best = { index: i, distance };
  }
  return best;
}

/** Douglas-Peucker 折線簡化（非遞迴，避免長輪廓爆堆疊）。首尾兩點一定保留。 */
export function simplifyPolyline(points: Point[], tolerance: number): Point[] {
  if (points.length <= 2) return points;
  const keep = new Uint8Array(points.length);
  keep[0] = 1;
  keep[points.length - 1] = 1;
  const stack: [number, number][] = [[0, points.length - 1]];
  while (stack.length > 0) {
    const [start, end] = stack.pop()!;
    const { index, distance } = farthestPoint(points, start, end);
    if (distance <= tolerance) continue;
    keep[index] = 1;
    stack.push([start, index], [index, end]);
  }
  return points.filter((_, i) => keep[i] === 1);
}

/** 簡化到點數不超過 maxPoints：容許誤差每次加倍。tolerance 必須大於 0。 */
export function simplifyToLimit(points: Point[], tolerance: number, maxPoints: number): Point[] {
  let result = simplifyPolyline(points, tolerance);
  for (let t = tolerance * 2; result.length > maxPoints; t *= 2) {
    result = simplifyPolyline(points, t);
  }
  return result;
}
