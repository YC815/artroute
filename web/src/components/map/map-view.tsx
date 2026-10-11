"use client";

import "maplibre-gl/dist/maplibre-gl.css";
import type { GeoJSON } from "geojson";
import type { GeoJSONSource, Map as MapLibreMap } from "maplibre-gl";
import { useEffect, useEffectEvent, useRef, useState, type ReactNode } from "react";
import {
  ENDPOINTS_SOURCE,
  SEARCH_AREA_SOURCE,
  SEARCH_PREVIEW_SOURCE,
  SEGMENTS_SOURCE,
  addRouteLayers,
} from "@/components/map/route-layers";
import type { MatchResponse } from "@/lib/engine/contract";
import { routeGeoJson } from "@/lib/map/route-geojson";
import { previewGeoJson, searchAreaGeoJson } from "@/lib/map/search-geojson";
import type { SearchArea } from "@/lib/search/params";
import type { LonLat } from "@/lib/shape/types";

export const MAP_STYLE_URL = "https://tiles.openfreemap.org/styles/liberty";
const INITIAL_ZOOM = 14;
const LOAD_ERROR_MESSAGE = "地圖載入失敗，請稍後重試";

type Props = {
  /** 只在建立地圖時使用。 */
  initialCenter: LonLat;
  response: MatchResponse | null;
  /** 自動搜尋的範圍（圓）；null 時不畫。 */
  searchArea: SearchArea | null;
  /** 搜尋中目前最佳候選的吸附結果（即時預覽）；null 時不畫。 */
  preview: MatchResponse | null;
  /** 地圖建立好（可以加圖層、讀中心點）時傳出地圖，卸載時傳 null。 */
  onMapChange?: (map: MapLibreMap | null) => void;
  /** 地圖樣式或資料來源載入失敗時通知一次（圖磚個別失敗不算）。 */
  onError?: (message: string) => void;
  /** 地圖載入後才呼叫，用來疊上編輯器。 */
  children: (map: MapLibreMap) => ReactNode;
};

/**
 * 圖磚錯誤（帶 tile）可能一次大量出現，只記一次警告；
 * 其他錯誤（style、sprite、來源的 TileJSON）代表地圖畫不出來，記錄並通知一次。
 */
function watchErrors(map: MapLibreMap, alive: () => boolean, report: (message: string) => void): void {
  let reported = false;
  let tileWarned = false;
  map.on("error", (e) => {
    if (!alive()) return;
    if ("tile" in e) {
      if (!tileWarned) console.warn("地圖圖磚載入失敗", e.error);
      tileWarned = true;
      return;
    }
    console.error("地圖載入失敗", e.error);
    if (!reported) report(LOAD_ERROR_MESSAGE);
    reported = true;
  });
}

/** setData 失敗時 MapLibre 會自己發 error 事件，這裡只避免未處理的 rejection。 */
function setSourceData(map: MapLibreMap, id: string, data: GeoJSON): void {
  void map.getSource<GeoJSONSource>(id)?.setData(data).catch(() => {});
}

export function MapView({ initialCenter, response, searchArea, preview, onMapChange, onError, children }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [center] = useState(initialCenter);
  const [map, setMap] = useState<MapLibreMap | null>(null);
  // onError 每次 render 都可能是新函式，包成 effect event 才不會因此重建地圖
  const notifyError = useEffectEvent((message: string) => onError?.(message));
  const notifyMap = useEffectEvent((instance: MapLibreMap | null) => onMapChange?.(instance));

  useEffect(() => {
    let disposed = false;
    let created: MapLibreMap | null = null;
    // maplibre-gl 只能在瀏覽器載入，所以動態 import
    void import("maplibre-gl").then(({ Map, setWorkerUrl }) => {
      if (disposed || !containerRef.current) return;
      setWorkerUrl("/maplibre-gl-worker.mjs");
      const instance = new Map({
        container: containerRef.current,
        style: MAP_STYLE_URL,
        center,
        zoom: INITIAL_ZOOM,
        // 不旋轉、不傾斜：螢幕上方永遠是北，編輯器的把手才直觀
        dragRotate: false,
        pitchWithRotate: false,
        touchPitch: false,
        maxPitch: 0,
      });
      instance.touchZoomRotate.disableRotation();
      instance.keyboard.disableRotation();
      watchErrors(instance, () => !disposed, (message) => notifyError(message));
      // load 要等 style 加上第一批圖磚才會觸發，style 失敗時永遠不會來；style.load 一到就能加圖層
      instance.once("style.load", () => {
        if (disposed) return;
        addRouteLayers(instance);
        setMap(instance);
      });
      created = instance;
    }).catch((error: unknown) => {
      // 模組載入失敗或 WebGL 初始化丟例外（new Map 同步 throw）時，至少讓使用者看到提示
      if (disposed) return;
      console.error("地圖載入失敗", error);
      notifyError(LOAD_ERROR_MESSAGE);
    });
    return () => {
      disposed = true;
      setMap(null);
      created?.remove();
    };
  }, [center]);

  useEffect(() => {
    notifyMap(map);
  }, [map]);

  useEffect(() => {
    if (!map) return;
    const data = routeGeoJson(response);
    setSourceData(map, SEGMENTS_SOURCE, data.segments);
    setSourceData(map, ENDPOINTS_SOURCE, data.endpoints);
  }, [map, response]);

  useEffect(() => {
    if (map) setSourceData(map, SEARCH_AREA_SOURCE, searchAreaGeoJson(searchArea));
  }, [map, searchArea]);

  useEffect(() => {
    if (map) setSourceData(map, SEARCH_PREVIEW_SOURCE, previewGeoJson(preview));
  }, [map, preview]);

  return (
    <div
      className="relative h-full w-full"
      data-testid="map"
      data-route-segments={response?.segments.length ?? 0}
      data-search-area={searchArea ? `${searchArea.center.join(",")},${searchArea.radius_m}` : ""}
      data-search-preview={preview ? preview.route.points.length : 0}
    >
      {/* 用 inline style：maplibre-gl.css 的 .maplibregl-map { position: relative } 沒有包在 @layer，會蓋過 Tailwind 的 absolute */}
      <div ref={containerRef} style={{ position: "absolute", inset: 0 }} />
      {map && children(map)}
    </div>
  );
}
