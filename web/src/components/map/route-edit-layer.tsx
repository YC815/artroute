"use client";

import type { Map as MapLibreMap, MapMouseEvent, MapTouchEvent } from "maplibre-gl";
import { useEffect, useEffectEvent } from "react";
import { setDragPreview } from "@/components/map/route-layers";
import type { EditTool, RoutePos } from "@/lib/edit/edit-state";
import { pickRouteOnMap } from "@/lib/map/route-pick";
import type { LonLat } from "@/lib/shape/types";

type Props = {
  map: MapLibreMap;
  /** 目前畫面上的路線；選點與拖曳都對它做。 */
  route: { segments: readonly { points: readonly LonLat[] }[] } | null;
  tool: EditTool | null;
  /** 可以收操作：微調中而且沒有在等引擎。 */
  enabled: boolean;
  onPick: (pos: RoutePos) => void;
  /** 放開才呼叫（抓點、放開的位置）；拖曳期間只畫提示。 */
  onDrag: (grab: RoutePos, to: LonLat) => void;
};

/** 放開時離按下的位置少於這麼多像素，當成沒拖（不叫引擎）。 */
const MIN_DRAG_PX = 4;

type Drag = { grab: RoutePos; startPx: { x: number; y: number }; last: LonLat; lastPx: { x: number; y: number } };

const lonLatOf = (e: { lngLat: { lng: number; lat: number } }): LonLat => [e.lngLat.lng, e.lngLat.lat];

/**
 * 微調時地圖上的操作（不畫東西，只接事件）：
 * 剪掉、重畫：點路線（12 px 內）選點；拖曳：在路線上按下、拖到新位置、放開才送出，期間地圖不平移。
 * 沒有選到路線的點擊不做事。MapLibre 自己的平移不會產生 click，所以平移地圖不會誤選。
 */
export function RouteEditLayer({ map, route, tool, enabled, onPick, onDrag }: Props) {
  const pickAt = useEffectEvent((lonLat: LonLat) => (route ? pickRouteOnMap(route.segments, lonLat, map.getZoom()) : null));
  const emitPick = useEffectEvent((pos: RoutePos) => onPick(pos));
  const emitDrag = useEffectEvent((grab: RoutePos, to: LonLat) => onDrag(grab, to));

  const active = enabled && tool !== null;

  // 游標：有選操作時用十字（拖曳用抓取），讓人看得出地圖在等點擊
  useEffect(() => {
    if (!active) return;
    const canvas = map.getCanvas();
    canvas.style.cursor = tool === "drag" ? "grab" : "crosshair";
    return () => {
      canvas.style.cursor = "";
    };
  }, [map, active, tool]);

  useEffect(() => {
    if (!active || (tool !== "cut" && tool !== "reroute")) return;
    const onClick = (e: MapMouseEvent) => {
      const hit = pickAt(lonLatOf(e));
      if (hit) emitPick(hit);
    };
    map.on("click", onClick);
    return () => void map.off("click", onClick);
  }, [map, active, tool]);

  useEffect(() => {
    if (!active || tool !== "drag") return;
    let drag: Drag | null = null;
    // 抓到路線就不讓地圖平移：preventDefault 是 MapLibre 的做法，再把 dragPan 關掉保險
    const panWasEnabled = map.dragPan.isEnabled();

    const pointOf = (lonLat: LonLat) => {
      const p = map.project(lonLat);
      return { x: p.x, y: p.y };
    };

    const finish = (commit: boolean) => {
      const done = drag;
      if (!done) return;
      drag = null;
      detach();
      if (panWasEnabled) map.dragPan.enable();
      setDragPreview(map, null, null);
      if (commit && Math.hypot(done.lastPx.x - done.startPx.x, done.lastPx.y - done.startPx.y) >= MIN_DRAG_PX) {
        emitDrag(done.grab, done.last);
      }
    };

    const moveTo = (lonLat: LonLat) => {
      if (!drag) return;
      drag.last = lonLat;
      drag.lastPx = pointOf(lonLat);
      setDragPreview(map, drag.grab.point, lonLat);
    };

    const clientToLonLat = (e: MouseEvent): LonLat => {
      const rect = map.getContainer().getBoundingClientRect();
      const { lng, lat } = map.unproject([e.clientX - rect.left, e.clientY - rect.top]);
      return [lng, lat];
    };

    // 滑鼠用 window 的事件：放開在地圖外面也收得到；觸控用 MapLibre 的事件（比照官方的拖曳範例）
    const onWindowMouseMove = (e: MouseEvent) => moveTo(clientToLonLat(e));
    const onWindowMouseUp = (e: MouseEvent) => {
      moveTo(clientToLonLat(e));
      finish(true);
    };
    const onTouchMove = (e: MapTouchEvent) => moveTo(lonLatOf(e));
    const onTouchEnd = () => finish(true);
    const onTouchCancel = () => finish(false);
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      // capture 階段先收到：取消這次拖曳，preventDefault 讓全域的 Esc（取消操作）這一下不再處理
      e.preventDefault();
      finish(false);
    };
    const onBlur = () => finish(false);

    function detach() {
      window.removeEventListener("mousemove", onWindowMouseMove);
      window.removeEventListener("mouseup", onWindowMouseUp);
      window.removeEventListener("keydown", onKeyDown, true);
      window.removeEventListener("blur", onBlur);
      map.off("touchmove", onTouchMove);
      map.off("touchend", onTouchEnd);
      map.off("touchcancel", onTouchCancel);
    }

    const onDown = (e: MapMouseEvent | MapTouchEvent) => {
      // 多指留給地圖縮放；已經在拖曳時，第二根手指不能搶走這次拖曳
      if (drag || ("points" in e && e.points.length !== 1)) return;
      const hit = pickAt(lonLatOf(e));
      if (!hit) return;
      e.preventDefault();
      map.dragPan.disable();
      drag = { grab: hit, startPx: { x: e.point.x, y: e.point.y }, last: hit.point, lastPx: { x: e.point.x, y: e.point.y } };
      setDragPreview(map, hit.point, hit.point);
      if ("points" in e) {
        map.on("touchmove", onTouchMove);
        map.on("touchend", onTouchEnd);
        map.on("touchcancel", onTouchCancel);
      } else {
        window.addEventListener("mousemove", onWindowMouseMove);
        window.addEventListener("mouseup", onWindowMouseUp);
      }
      window.addEventListener("keydown", onKeyDown, true);
      window.addEventListener("blur", onBlur);
    };

    map.on("mousedown", onDown);
    map.on("touchstart", onDown);
    return () => {
      map.off("mousedown", onDown);
      map.off("touchstart", onDown);
      finish(false);
    };
  }, [map, active, tool]);

  return null;
}
