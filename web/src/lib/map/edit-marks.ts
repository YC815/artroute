import type { Feature, FeatureCollection, LineString, Point } from "geojson";
import type { LonLat } from "@/lib/shape/types";

export type PickProperties = { order: 1 | 2 };
export type DragProperties = { role: "grab" | "to" };

/** 微調中選到的點（重畫的第一、第二個點，剪掉與拖曳的那一個）；order 讓兩個點看得出先後。 */
export function picksGeoJson(picks: readonly LonLat[]): FeatureCollection<Point, PickProperties> {
  return {
    type: "FeatureCollection",
    features: picks.slice(0, 2).map((p, i) => ({
      type: "Feature",
      properties: { order: i === 0 ? 1 : 2 },
      geometry: { type: "Point", coordinates: p },
    })),
  };
}

/**
 * 拖曳中的預覽：抓點、目前位置，與一條提示線。提示線只是 UI（放開才叫引擎算真正的路），不是路線輸出。
 * to 為 null（沒在拖曳）時是空的。
 */
export function dragPreviewGeoJson(
  grab: LonLat | null,
  to: LonLat | null,
): FeatureCollection<Point | LineString, DragProperties | Record<string, never>> {
  if (!grab || !to) return { type: "FeatureCollection", features: [] };
  const point = (role: DragProperties["role"], coordinates: LonLat): Feature<Point, DragProperties> => ({
    type: "Feature",
    properties: { role },
    geometry: { type: "Point", coordinates },
  });
  return {
    type: "FeatureCollection",
    features: [
      { type: "Feature", properties: {}, geometry: { type: "LineString", coordinates: [grab, to] } },
      point("grab", grab),
      point("to", to),
    ],
  };
}

/** 重新驗證沒過的段落（序號 0 起算）：整段用紅色疊在路線上。序號超出範圍的略過。 */
export function badSegmentsGeoJson(
  route: { segments: readonly { points: readonly LonLat[] }[] } | null,
  bad: readonly number[],
): FeatureCollection<LineString, Record<string, never>> {
  const segments = route?.segments ?? [];
  return {
    type: "FeatureCollection",
    features: [...new Set(bad)]
      .filter((i) => i >= 0 && i < segments.length)
      .map((i) => ({
        type: "Feature",
        properties: {},
        geometry: { type: "LineString", coordinates: segments[i].points.map((p) => [p[0], p[1]]) },
      })),
  };
}
