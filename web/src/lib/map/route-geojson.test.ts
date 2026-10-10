import { describe, expect, it } from "vitest";
import { SAMPLE_MATCH_RESPONSE } from "@/lib/engine/fixtures";
import { routeGeoJson } from "@/lib/map/route-geojson";

describe("routeGeoJson", () => {
  it("每個段落一條線，帶 kind", () => {
    const { segments } = routeGeoJson(SAMPLE_MATCH_RESPONSE);
    expect(segments.features.map((f) => f.properties.kind)).toEqual(["stroke", "connector", "detour"]);
    expect(segments.features[0].geometry.coordinates).toEqual(SAMPLE_MATCH_RESPONSE.segments[0].points);
  });

  it("起點、終點取路線的第一點與最後一點", () => {
    const { endpoints } = routeGeoJson(SAMPLE_MATCH_RESPONSE);
    expect(endpoints.features.map((f) => [f.properties.role, f.geometry.coordinates])).toEqual([
      ["start", [121.5, 25.03]],
      ["end", [121.503, 25.031]],
    ]);
  });

  it("沒有回應時是空的", () => {
    expect(routeGeoJson(null)).toEqual({
      segments: { type: "FeatureCollection", features: [] },
      endpoints: { type: "FeatureCollection", features: [] },
    });
  });
});
