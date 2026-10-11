import type { Map as MapLibreMap } from "maplibre-gl";
import { routeGeoJson } from "@/lib/map/route-geojson";
import { previewGeoJson, searchAreaGeoJson } from "@/lib/map/search-geojson";

export const SEGMENTS_SOURCE = "route-segments";
export const ENDPOINTS_SOURCE = "route-endpoints";
export const SEARCH_AREA_SOURCE = "search-area";
export const SEARCH_PREVIEW_SOURCE = "search-preview";
const ARROW_IMAGE = "route-arrow";

/** 路線方向箭頭：往右的「>」（沿線放置時 x 軸對齊行進方向）。畫兩次：深色外框＋白色內線。 */
function arrowImage(): ImageData {
  const size = 32;
  const canvas = document.createElement("canvas");
  canvas.width = size;
  canvas.height = size;
  const ctx = canvas.getContext("2d")!;
  ctx.lineCap = "round";
  ctx.lineJoin = "round";
  for (const [color, width] of [["#1e293b", 8], ["#ffffff", 4]] as const) {
    ctx.strokeStyle = color;
    ctx.lineWidth = width;
    ctx.beginPath();
    ctx.moveTo(11, 8);
    ctx.lineTo(21, 16);
    ctx.lineTo(11, 24);
    ctx.stroke();
  }
  return ctx.getImageData(0, 0, size, size);
}

/**
 * 自動搜尋的圖層，畫在路線底下：範圍（淡藍填色＋虛線外框）、搜尋中目前最佳候選的路線（青綠虛線，和即時路線的藍色實線分得開）。
 */
function addSearchLayers(map: MapLibreMap): void {
  map.addSource(SEARCH_AREA_SOURCE, { type: "geojson", data: searchAreaGeoJson(null) });
  map.addSource(SEARCH_PREVIEW_SOURCE, { type: "geojson", data: previewGeoJson(null) });
  map.addLayer({
    id: "search-area-fill",
    type: "fill",
    source: SEARCH_AREA_SOURCE,
    paint: { "fill-color": "#0ea5e9", "fill-opacity": 0.08 },
  });
  map.addLayer({
    id: "search-area-outline",
    type: "line",
    source: SEARCH_AREA_SOURCE,
    paint: { "line-color": "#0284c7", "line-width": 2, "line-opacity": 0.8, "line-dasharray": [3, 2] },
  });
  map.addLayer({
    id: "search-preview",
    type: "line",
    source: SEARCH_PREVIEW_SOURCE,
    layout: { "line-cap": "round", "line-join": "round" },
    paint: { "line-color": "#0d9488", "line-width": 5, "line-opacity": 0.7, "line-dasharray": [1.5, 1.5] },
  });
}

/**
 * 加上路線圖層：連接線（淡、虛線）、圖形段（藍）、繞路段（橘、粗，醒目標示）、方向箭頭、起點（綠）終點（紅）。
 * 自動搜尋的範圍與預覽先加，所以在路線底下。
 * 只用 icon、不用文字，所以不需要 style 的 glyphs（e2e 用空白 style 也能跑）。
 */
export function addRouteLayers(map: MapLibreMap): void {
  addSearchLayers(map);
  map.addImage(ARROW_IMAGE, arrowImage(), { pixelRatio: 2 });
  const empty = routeGeoJson(null);
  map.addSource(SEGMENTS_SOURCE, { type: "geojson", data: empty.segments });
  map.addSource(ENDPOINTS_SOURCE, { type: "geojson", data: empty.endpoints });
  const lineLayout = { "line-cap": "round", "line-join": "round" } as const;
  map.addLayer({
    id: "route-connector",
    type: "line",
    source: SEGMENTS_SOURCE,
    filter: ["==", ["get", "kind"], "connector"],
    layout: lineLayout,
    paint: { "line-color": "#64748b", "line-width": 3, "line-opacity": 0.45, "line-dasharray": [1, 2] },
  });
  map.addLayer({
    id: "route-stroke",
    type: "line",
    source: SEGMENTS_SOURCE,
    filter: ["==", ["get", "kind"], "stroke"],
    layout: lineLayout,
    paint: { "line-color": "#2563eb", "line-width": 4 },
  });
  map.addLayer({
    id: "route-detour",
    type: "line",
    source: SEGMENTS_SOURCE,
    filter: ["==", ["get", "kind"], "detour"],
    layout: lineLayout,
    paint: { "line-color": "#f97316", "line-width": 6 },
  });
  map.addLayer({
    id: "route-arrows",
    type: "symbol",
    source: SEGMENTS_SOURCE,
    layout: {
      "symbol-placement": "line",
      "symbol-spacing": 80,
      "icon-image": ARROW_IMAGE,
      "icon-allow-overlap": true,
      "icon-ignore-placement": true,
      "icon-rotation-alignment": "map",
    },
  });
  map.addLayer({
    id: "route-endpoints",
    type: "circle",
    source: ENDPOINTS_SOURCE,
    paint: {
      "circle-radius": 7,
      "circle-color": ["match", ["get", "role"], "start", "#16a34a", "#dc2626"],
      "circle-stroke-color": "#ffffff",
      "circle-stroke-width": 2,
    },
  });
}
