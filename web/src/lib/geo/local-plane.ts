import type { LonLat } from "@/lib/shape/types";

/** 地球平均半徑（公尺）。 */
const EARTH_RADIUS_M = 6_371_008.8;
const RAD = Math.PI / 180;

/**
 * 以 origin 為中心的局部切平面（等距圓柱，緯度取 origin 的緯度）。
 * 在作品尺度（≤ 20 km）內誤差可忽略，跟引擎評分用的局部平面一致。
 */
export function fromLocal(origin: LonLat, east: number, north: number): LonLat {
  const metersPerRadLon = EARTH_RADIUS_M * Math.cos(origin[1] * RAD);
  return [origin[0] + east / metersPerRadLon / RAD, origin[1] + north / EARTH_RADIUS_M / RAD];
}

/** fromLocal 的反函數：經緯度 → 相對 origin 的 [東, 北]（公尺）。 */
export function toLocal(origin: LonLat, p: LonLat): [number, number] {
  const metersPerRadLon = EARTH_RADIUS_M * Math.cos(origin[1] * RAD);
  return [(p[0] - origin[0]) * RAD * metersPerRadLon, (p[1] - origin[1]) * RAD * EARTH_RADIUS_M];
}
