import type { FeatureCollection, LineString, Point } from "geojson";
import type { MatchResponse, SegmentKind } from "@/lib/engine/contract";

export type SegmentProperties = { kind: SegmentKind };
export type EndpointProperties = { role: "start" | "end" };

export type RouteGeoJson = {
  segments: FeatureCollection<LineString, SegmentProperties>;
  endpoints: FeatureCollection<Point, EndpointProperties>;
};

/** /match 回應 → 地圖圖層資料。沒有回應時回傳空的 FeatureCollection（清掉地圖上的路線）。 */
export function routeGeoJson(response: MatchResponse | null): RouteGeoJson {
  const segments = response?.segments ?? [];
  const points = response?.route.points ?? [];
  const ends: [EndpointProperties["role"], number][] = points.length > 0 ? [["start", 0], ["end", points.length - 1]] : [];
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
      features: ends.map(([role, i]) => ({
        type: "Feature",
        properties: { role },
        geometry: { type: "Point", coordinates: points[i] },
      })),
    },
  };
}
