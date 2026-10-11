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

  it("微調後的路線（只有 segments、含 kind edit）：edit 段帶出 kind，起終點取第一段頭、最後一段尾", () => {
    const edited = {
      segments: [
        SAMPLE_MATCH_RESPONSE.segments[0],
        { kind: "edit" as const, stroke_index: 0, points: [[121.501, 25.03], [121.504, 25.032]] as [number, number][], length_m: 380 },
      ],
    };
    const { segments, endpoints } = routeGeoJson(edited);
    expect(segments.features.map((f) => f.properties.kind)).toEqual(["stroke", "edit"]);
    expect(endpoints.features.map((f) => [f.properties.role, f.geometry.coordinates])).toEqual([
      ["start", [121.5, 25.03]],
      ["end", [121.504, 25.032]],
    ]);
  });

  it("沒有回應或沒有段落時是空的", () => {
    const empty = {
      segments: { type: "FeatureCollection", features: [] },
      endpoints: { type: "FeatureCollection", features: [] },
    };
    expect(routeGeoJson(null)).toEqual(empty);
    expect(routeGeoJson({ segments: [] })).toEqual(empty);
  });
});
