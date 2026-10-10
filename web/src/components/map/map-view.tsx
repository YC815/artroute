"use client";

import "maplibre-gl/dist/maplibre-gl.css";
import type { GeoJSONSource, Map as MapLibreMap } from "maplibre-gl";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { ENDPOINTS_SOURCE, SEGMENTS_SOURCE, addRouteLayers } from "@/components/map/route-layers";
import type { MatchResponse } from "@/lib/engine/contract";
import { routeGeoJson } from "@/lib/map/route-geojson";
import type { LonLat } from "@/lib/shape/types";

export const MAP_STYLE_URL = "https://tiles.openfreemap.org/styles/liberty";
const INITIAL_ZOOM = 14;

type Props = {
  /** 只在建立地圖時使用。 */
  initialCenter: LonLat;
  response: MatchResponse | null;
  /** 地圖載入後才呼叫，用來疊上編輯器。 */
  children: (map: MapLibreMap) => ReactNode;
};

export function MapView({ initialCenter, response, children }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [center] = useState(initialCenter);
  const [map, setMap] = useState<MapLibreMap | null>(null);

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
      instance.once("load", () => {
        addRouteLayers(instance);
        if (!disposed) setMap(instance);
      });
      created = instance;
    });
    return () => {
      disposed = true;
      created?.remove();
    };
  }, [center]);

  useEffect(() => {
    if (!map) return;
    const data = routeGeoJson(response);
    map.getSource<GeoJSONSource>(SEGMENTS_SOURCE)?.setData(data.segments);
    map.getSource<GeoJSONSource>(ENDPOINTS_SOURCE)?.setData(data.endpoints);
  }, [map, response]);

  return (
    <div
      className="relative h-full w-full"
      data-testid="map"
      data-route-segments={response?.segments.length ?? 0}
    >
      <div ref={containerRef} className="absolute inset-0" />
      {map && children(map)}
    </div>
  );
}
