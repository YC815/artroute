import { describe, expect, it } from "vitest";
import { SAMPLE_MATCH_RESPONSE } from "@/lib/engine/fixtures";
import { toLocal } from "@/lib/geo/local-plane";
import { CIRCLE_STEPS, circlePolygon, previewGeoJson, searchAreaGeoJson } from "@/lib/map/search-geojson";
import type { LonLat } from "@/lib/shape/types";

const CENTER: LonLat = [121.5654, 25.033];

describe("circlePolygon", () => {
  const ring = circlePolygon(CENTER, 3000);

  it("封閉的環：CIRCLE_STEPS + 1 個點，首尾相同", () => {
    expect(ring).toHaveLength(CIRCLE_STEPS + 1);
    expect(ring[ring.length - 1]).toEqual(ring[0]);
  });

  it("每個點離圓心都是半徑（局部平面距離）", () => {
    for (const p of ring) {
      const [east, north] = toLocal(CENTER, p);
      expect(Math.hypot(east, north)).toBeCloseTo(3000, 6);
    }
  });

  it("從正北開始、逆時針（RFC 7946 的外環方向）", () => {
    const [east0, north0] = toLocal(CENTER, ring[0]);
    expect(east0).toBeCloseTo(0, 6);
    expect(north0).toBeCloseTo(3000, 6);
    const [east1] = toLocal(CENTER, ring[1]);
    expect(east1).toBeLessThan(0);
  });

  it("緯度越高，經度方向的度數越大（不是經緯度上的正圓）", () => {
    const north = circlePolygon([121, 25], 1000);
    const [lonWest] = north[CIRCLE_STEPS / 4];
    const [, latNorth] = north[0];
    expect(121 - lonWest).toBeGreaterThan(latNorth - 25);
  });
});

describe("searchAreaGeoJson", () => {
  it("有範圍 → 一個 Polygon；沒有 → 空的 FeatureCollection", () => {
    const data = searchAreaGeoJson({ center: CENTER, radius_m: 500 });
    expect(data.features).toHaveLength(1);
    expect(data.features[0].geometry).toEqual({ type: "Polygon", coordinates: [circlePolygon(CENTER, 500)] });
    expect(searchAreaGeoJson(null)).toEqual({ type: "FeatureCollection", features: [] });
  });
});

describe("previewGeoJson", () => {
  it("預覽是整條路線的一條 LineString；沒有預覽 → 空的", () => {
    expect(previewGeoJson(SAMPLE_MATCH_RESPONSE).features).toEqual([
      {
        type: "Feature",
        properties: {},
        geometry: { type: "LineString", coordinates: SAMPLE_MATCH_RESPONSE.route.points },
      },
    ]);
    expect(previewGeoJson(null).features).toEqual([]);
  });

  it("路線不足 2 點時不畫（LineString 至少 2 點）", () => {
    const single = { ...SAMPLE_MATCH_RESPONSE, route: { points: [CENTER], length_m: 0 } };
    expect(previewGeoJson(single).features).toEqual([]);
  });
});
