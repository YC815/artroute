import { haversineM } from "@/lib/geo/haversine";
import { EARTH_RADIUS_M, toLocal } from "@/lib/geo/local-plane";
import type { LonLat } from "@/lib/shape/types";

/** 點路線時，離路線幾個螢幕像素內才算點到。 */
export const PICK_TOLERANCE_PX = 12;

/** 路線上的一個位置：offsetM 從 segments[segment] 的起點沿折線量（引擎 /edit 的 {segment, offset_m}）。 */
export type RouteHit = { segment: number; offsetM: number; point: LonLat; distanceM: number };

/**
 * 地圖上的經緯度 → 路線上最近的位置；最近的距離 > toleranceM 時回 null。
 *
 * offsetM 與引擎 edit/position.py 同一把尺：逐邊 haversine 累積，再加上 t × 該邊的 haversine 長度；
 * point 是該邊兩端經緯度以 t 線性內插，引擎 resolve 用同一個 t 內插回同一點。
 * 距離在以點擊處為原點的局部平面量（作品尺度內與 haversine 差異可忽略）。
 * 路線自己重疊（來回騎）時距離相同，取較前面的段、同段較前面的邊：只求確定性，點哪一趟由使用者重點。
 */
export function pickRoutePosition(
  segments: readonly { points: readonly LonLat[] }[],
  lonLat: LonLat,
  toleranceM: number,
): RouteHit | null {
  let best: RouteHit | null = null;
  segments.forEach(({ points }, segment) => {
    let walked = 0;
    // 只有一個點的段當成一條零長度的邊
    for (let i = 0; i < Math.min(points.length, Math.max(points.length - 1, 1)); i++) {
      const a = points[i];
      const b = points[Math.min(i + 1, points.length - 1)];
      const [ax, ay] = toLocal(lonLat, a);
      const [bx, by] = toLocal(lonLat, b);
      const dx = bx - ax;
      const dy = by - ay;
      const len2 = dx * dx + dy * dy;
      const t = len2 === 0 ? 0 : Math.min(1, Math.max(0, -(ax * dx + ay * dy) / len2));
      const distanceM = Math.hypot(ax + t * dx, ay + t * dy);
      const step = haversineM(a, b);
      if (distanceM <= toleranceM && (best === null || distanceM < best.distanceM)) {
        const point: LonLat = t === 0 ? a : t === 1 ? b : [a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])];
        best = { segment, offsetM: walked + t * step, point, distanceM };
      }
      walked += step;
    }
  });
  return best;
}

/**
 * zoom 下、緯度 lat 處一個 CSS 像素是幾公尺。MapLibre 的 tile 是 512 px、地球半徑 6371008.8 m
 * （不是 256 px 的 156543.03392 那組），地圖鎖住傾斜與旋轉（map-view 的 maxPitch: 0），整個畫面同一個比例。
 */
export function metersPerPixel(lat: number, zoom: number): number {
  return (2 * Math.PI * EARTH_RADIUS_M * Math.cos((lat * Math.PI) / 180)) / (512 * 2 ** zoom);
}
