import type { FeatureCollection, LineString, Point } from "geojson";
import type { LonLat } from "@/lib/shape/types";
import type { Segment } from "@/lib/engine/contract";

export type SegmentProperties = { kind: Segment["kind"] };
export type EndpointProperties = { role: "start" | "end" };

export type RouteGeoJson = {
  segments: FeatureCollection<LineString, SegmentProperties>;
  endpoints: FeatureCollection<Point, EndpointProperties>;
};

/** /match 回應或微調後的路線都有的部分。 */
export type RouteSegments = { segments: readonly { kind: Segment["kind"]; points: LonLat[] }[] };

/**
 * 路線 → 地圖圖層資料。沒有路線時回傳空的 FeatureCollection（清掉地圖上的路線）。
 * 起終點取第一段的頭與最後一段的尾：引擎的 route.points 就是相接的 segments 攤平，微調後的路線只存 segments。
 */
export function routeGeoJson(route: RouteSegments | null): RouteGeoJson {
  const segments = route?.segments ?? [];
  const first = segments[0]?.points[0];
  const last = segments.at(-1)?.points.at(-1);
  const ends: [EndpointProperties["role"], LonLat][] = first && last ? [["start", first], ["end", last]] : [];
  return {
    segments: {
      type: "FeatureCollection",
      features: segments.map((s) => ({
        type: "Feature",
        properties: { kind: s.kind },
        geometry: { type: "LineString", coordinates: s.points },
      })),
    },
    endpoints: {
      type: "FeatureCollection",
      features: ends.map(([role, point]) => ({
        type: "Feature",
        properties: { role },
        geometry: { type: "Point", coordinates: point },
      })),
    },
  };
}
