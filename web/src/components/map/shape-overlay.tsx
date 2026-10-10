"use client";

import type { Map as MapLibreMap } from "maplibre-gl";
import { useEffect, useMemo, useRef, useState, type KeyboardEvent, type PointerEvent } from "react";
import { Button } from "@/components/ui/button";
import { shapeBounds } from "@/lib/shape/normalize";
import {
  dragTransform,
  handlePositions,
  isNudgeKey,
  nudge,
  transformPoint,
  type DragMode,
  type Transform,
} from "@/lib/shape/transform";
import type { LonLat, Shape } from "@/lib/shape/types";

type Props = {
  map: MapLibreMap;
  shape: Shape;
  transform: Transform;
  /** 吸附失敗（warnings）的筆畫索引：目標線畫成紅色。 */
  warned: ReadonlySet<number>;
  /** dragging 為 true 代表還在拖曳（即時吸附會節流），false 代表放開（立刻吸附）。 */
  onChange: (transform: Transform, dragging: boolean) => void;
};

type Drag = { mode: DragMode; start: Transform; from: LonLat; pointerId: number };

const ACCENT = "#7c3aed";
const WARNED = "#dc2626";
const INTERACTIVE = { pointerEvents: "all", touchAction: "none" } as const;

/**
 * 地圖上的編輯器（SVG 疊在地圖上）：目標圖形（虛線）、拖曳本體移動、角落把手等比縮放、旋轉把手旋轉。
 * Pointer Events 讓滑鼠與觸控共用；圖形取得焦點後可以用方向鍵微調。
 */
export function ShapeOverlay({ map, shape, transform, warned, onChange }: Props) {
  const [, setFrame] = useState(0);
  const dragRef = useRef<Drag | null>(null);
  const groupRef = useRef<SVGGElement>(null);
  const bounds = useMemo(() => shapeBounds(shape.strokes), [shape]);

  // 地圖移動或縮放時重畫（座標要重新投影）
  useEffect(() => {
    const rerender = () => setFrame((n) => n + 1);
    const subscriptions = [map.on("move", rerender), map.on("resize", rerender)];
    return () => subscriptions.forEach((s) => s.unsubscribe());
  }, [map]);

  const toScreen = (p: LonLat) => {
    const { x, y } = map.project(p);
    return `${x},${y}`;
  };

  const pointerLonLat = (e: PointerEvent): LonLat => {
    const rect = map.getContainer().getBoundingClientRect();
    const { lng, lat } = map.unproject([e.clientX - rect.left, e.clientY - rect.top]);
    return [lng, lat];
  };

  const begin = (mode: DragMode, e: PointerEvent<SVGElement>) => {
    e.preventDefault();
    e.currentTarget.setPointerCapture(e.pointerId);
    groupRef.current?.focus();
    dragRef.current = { mode, start: transform, from: pointerLonLat(e), pointerId: e.pointerId };
  };

  const track = (dragging: boolean, e: PointerEvent<SVGGElement>) => {
    const drag = dragRef.current;
    if (!drag || drag.pointerId !== e.pointerId) return;
    if (!dragging) dragRef.current = null;
    onChange(dragTransform(drag.mode, drag.start, drag.from, pointerLonLat(e), bounds), dragging);
  };

  const onKeyDown = (e: KeyboardEvent<SVGGElement>) => {
    const next = nudge(transform, e.key, e.shiftKey);
    if (!next) return;
    e.preventDefault();
    onChange(next, true);
  };

  const onKeyUp = (e: KeyboardEvent<SVGGElement>) => {
    if (isNudgeKey(e.key)) onChange(transform, false);
  };

  const recenter = () => {
    const { lng, lat } = map.getCenter();
    onChange({ ...transform, center: [lng, lat] }, false);
  };

  const handles = handlePositions(transform, bounds);
  const topCenter = transformPoint([0, bounds.halfHeight], transform);

  return (
    <>
      <svg className="pointer-events-none absolute inset-0 h-full w-full" aria-label="目標圖形編輯器">
        <g
          ref={groupRef}
          tabIndex={0}
          className="outline-none [&:focus>polygon]:[stroke-width:3]"
          onKeyDown={onKeyDown}
          onKeyUp={onKeyUp}
          onPointerMove={(e) => track(true, e)}
          onPointerUp={(e) => track(false, e)}
          onPointerCancel={(e) => track(false, e)}
        >
          <polygon
            points={handles.corners.map(toScreen).join(" ")}
            fill={ACCENT}
            fillOpacity={0.06}
            stroke={ACCENT}
            strokeOpacity={0.5}
            className="cursor-move"
            style={INTERACTIVE}
            onPointerDown={(e) => begin("move", e)}
          />
          {shape.strokes.map((stroke, i) => (
            <polyline
              key={i}
              data-testid="target-stroke"
              points={(stroke.closed ? [...stroke.points, stroke.points[0]] : stroke.points)
                .map((p) => toScreen(transformPoint(p, transform)))
                .join(" ")}
              fill="none"
              stroke={warned.has(i) ? WARNED : ACCENT}
              strokeWidth={warned.has(i) ? 3 : 2}
              strokeDasharray="6 4"
            />
          ))}
          <polyline
            points={[topCenter, handles.rotate].map(toScreen).join(" ")}
            fill="none"
            stroke={ACCENT}
            strokeWidth={1.5}
          />
          <circle
            cx={map.project(handles.rotate).x}
            cy={map.project(handles.rotate).y}
            r={9}
            fill="white"
            stroke={ACCENT}
            strokeWidth={2}
            className="cursor-grab"
            style={INTERACTIVE}
            aria-label="旋轉"
            onPointerDown={(e) => begin("rotate", e)}
          />
          {handles.corners.map((corner, i) => (
            <rect
              key={i}
              x={map.project(corner).x - 7}
              y={map.project(corner).y - 7}
              width={14}
              height={14}
              fill="white"
              stroke={ACCENT}
              strokeWidth={2}
              className="cursor-nwse-resize"
              style={INTERACTIVE}
              aria-label="縮放"
              onPointerDown={(e) => begin("scale", e)}
            />
          ))}
        </g>
      </svg>
      <Button size="sm" variant="secondary" className="absolute top-3 left-3 shadow" onClick={recenter}>
        圖形移到畫面中央
      </Button>
    </>
  );
}
