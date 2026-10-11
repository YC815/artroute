import { EARTH_RADIUS_M } from "@/lib/geo/local-plane";
import type { LonLat } from "@/lib/shape/types";

const RAD = Math.PI / 180;

/** 兩個 [lon, lat] 之間的大圓距離（公尺），與引擎 routing/geo.haversine_m 同一個公式。 */
export function haversineM(a: LonLat, b: LonLat): number {
  const phi1 = a[1] * RAD;
  const phi2 = b[1] * RAD;
  const dPhi = phi2 - phi1;
  const dLambda = (b[0] - a[0]) * RAD;
  const h = Math.sin(dPhi / 2) ** 2 + Math.cos(phi1) * Math.cos(phi2) * Math.sin(dLambda / 2) ** 2;
  return 2 * EARTH_RADIUS_M * Math.asin(Math.sqrt(h));
}

/** 折線總長（公尺）；少於 2 個點為 0。 */
export function polylineLengthM(points: readonly LonLat[]): number {
  let total = 0;
  for (let i = 1; i < points.length; i++) total += haversineM(points[i - 1], points[i]);
  return total;
}
