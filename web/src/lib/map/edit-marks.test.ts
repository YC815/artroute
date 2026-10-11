import { describe, expect, it } from "vitest";
import { dragPreviewGeoJson, picksGeoJson } from "@/lib/map/edit-marks";

describe("picksGeoJson", () => {
  it("沒有選點是空的；兩個點依序標 order 1、2", () => {
    expect(picksGeoJson([]).features).toEqual([]);
    expect(picksGeoJson([[1, 2], [3, 4]]).features.map((f) => [f.properties.order, f.geometry.coordinates])).toEqual([
      [1, [1, 2]],
      [2, [3, 4]],
    ]);
  });
});

describe("dragPreviewGeoJson", () => {
  it("沒在拖曳是空的", () => {
    expect(dragPreviewGeoJson(null, null).features).toEqual([]);
    expect(dragPreviewGeoJson([1, 2], null).features).toEqual([]);
  });

  it("拖曳中：抓點到目前位置的提示線、抓點、目前位置", () => {
    const { features } = dragPreviewGeoJson([1, 2], [3, 4]);
    expect(features.map((f) => f.geometry.type)).toEqual(["LineString", "Point", "Point"]);
    expect(features[0].geometry).toEqual({ type: "LineString", coordinates: [[1, 2], [3, 4]] });
    expect(features.slice(1).map((f) => f.properties)).toEqual([{ role: "grab" }, { role: "to" }]);
  });
});
