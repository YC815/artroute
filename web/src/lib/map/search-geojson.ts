import type { FeatureCollection, LineString, Polygon } from "geojson";
import type { MatchResponse } from "@/lib/engine/contract";
import { fromLocal } from "@/lib/geo/local-plane";
import type { SearchArea } from "@/lib/search/params";
import type { LonLat } from "@/lib/shape/types";

/** 圓周取幾個點：10 km 的圓每段約 1 km，畫出來仍是平滑的圓。 */
export const CIRCLE_STEPS = 64;

/**
 * 圓心＋半徑（公尺）→ 封閉的環（首尾相同），從正北開始逆時針（RFC 7946 的外環方向）。
 * 用局部切平面，緯度造成的經度縮放已算進去。
 */
export function circlePolygon(center: LonLat, radiusM: number, steps = CIRCLE_STEPS): LonLat[] {
  const ring: LonLat[] = [];
  for (let i = 0; i < steps; i++) {
    const angle = (2 * Math.PI * i) / steps;
    ring.push(fromLocal(center, -radiusM * Math.sin(angle), radiusM * Math.cos(angle)));
  }
  ring.push(ring[0]);
  return ring;
}

/** 搜尋範圍 → 地圖圖層資料；沒有範圍時是空的 FeatureCollection（清掉地圖上的圓）。 */
export function searchAreaGeoJson(area: SearchArea | null): FeatureCollection<Polygon> {
  return {
    type: "FeatureCollection",
    features: area
      ? [{ type: "Feature", properties: {}, geometry: { type: "Polygon", coordinates: [circlePolygon(area.center, area.radius_m)] } }]
      : [],
  };
}

/** 搜尋中目前最佳候選的路線（即時預覽）→ 一條 LineString；沒有預覽時是空的。 */
export function previewGeoJson(response: MatchResponse | null): FeatureCollection<LineString> {
  const points = response?.route.points ?? [];
  return {
    type: "FeatureCollection",
    features:
      points.length >= 2 ? [{ type: "Feature", properties: {}, geometry: { type: "LineString", coordinates: points } }] : [],
  };
}
