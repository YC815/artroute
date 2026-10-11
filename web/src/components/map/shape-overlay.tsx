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
  /** 隱藏原圖：目標線（含標紅的失敗筆畫）不畫，只看路線；外框與把手照常，仍可拖曳、縮放、旋轉。 */
  targetHidden: boolean;
  onToggleTarget: () => void;
  /** dragging 為 true 代表還在拖曳（即時吸附會節流），false 代表放開（立刻吸附）。 */
  onChange: (transform: Transform, dragging: boolean) => void;
};

/** last：最近一次有效（來自 pointermove）的擺放。pointercancel 的座標不可靠，取消時只提交它。 */
type Drag = { mode: DragMode; start: Transform; from: LonLat; pointerId: number; last: Transform };

const ACCENT = "#7c3aed";
const WARNED = "#dc2626";
const INTERACTIVE = { pointerEvents: "all", touchAction: "none" } as const;
/** 把手的可觸碰範圍半徑（px）：視覺上的把手只有 14 px，手指點不準。 */
const HIT_RADIUS = 22;
/** 角落順序：右上、左上、左下、右下（同 handlePositions）。 */
const CORNER_CURSORS = ["cursor-nesw-resize", "cursor-nwse-resize", "cursor-nesw-resize", "cursor-nwse-resize"] as const;

/**
 * 地圖上的編輯器（SVG 疊在地圖上）：目標圖形（虛線）、拖曳本體移動、角落把手等比縮放、旋轉把手旋轉。
 * Pointer Events 讓滑鼠與觸控共用；圖形取得焦點後可以用方向鍵微調。
 * 「隱藏原圖」只拿掉目標線：路線、起終點、方向箭頭在 MapLibre 圖層上，不受影響。
 */
export function ShapeOverlay({ map, shape, transform, warned, targetHidden, onToggleTarget, onChange }: Props) {
  const [, setFrame] = useState(0);
  const dragRef = useRef<Drag | null>(null);
  const nudgingRef = useRef(false);
  const svgRef = useRef<SVGSVGElement>(null);
  const groupRef = useRef<SVGGElement>(null);
  const bounds = useMemo(() => shapeBounds(shape.strokes), [shape]);

  // 地圖移動或縮放時重畫（座標要重新投影）
  useEffect(() => {
    const rerender = () => setFrame((n) => n + 1);
    const subscriptions = [map.on("move", rerender), map.on("resize", rerender)];
    return () => subscriptions.forEach((s) => s.unsubscribe());
  }, [map]);

  // MapLibre 的滾輪縮放掛在 canvas container 上，SVG 是它的兄弟節點，圖形上的滾輪會被吞掉。
  // React 的 onWheel 是 passive、不能 preventDefault，所以用原生 listener 轉發給地圖。
  useEffect(() => {
    const svg = svgRef.current;
    if (!svg) return;
    const forward = (ev: WheelEvent) => {
      ev.preventDefault();
      map.getCanvasContainer().dispatchEvent(new WheelEvent("wheel", ev));
    };
    svg.addEventListener("wheel", forward, { passive: false });
    return () => svg.removeEventListener("wheel", forward);
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
    // 只收主要按鈕；已經在拖曳時，第二根手指不能搶走這次拖曳
    if (e.button !== 0 || dragRef.current) return;
    e.preventDefault();
    e.currentTarget.setPointerCapture(e.pointerId);
    groupRef.current?.focus({ preventScroll: true });
    dragRef.current = { mode, start: transform, from: pointerLonLat(e), pointerId: e.pointerId, last: transform };
  };

  const move = (e: PointerEvent<SVGGElement>) => {
    const drag = dragRef.current;
    if (!drag || drag.pointerId !== e.pointerId) return;
    drag.last = dragTransform(drag.mode, drag.start, drag.from, pointerLonLat(e), bounds);
    onChange(drag.last, true);
  };

  /** 結束拖曳。pointerup 的座標可信；cancel／失去 capture 的座標不可信，只提交最後一次有效的擺放。重複呼叫無效。 */
  const finish = (e: PointerEvent<SVGGElement>, trustPointer: boolean) => {
    const drag = dragRef.current;
    if (!drag || drag.pointerId !== e.pointerId) return;
    dragRef.current = null;
    const final = trustPointer
      ? dragTransform(drag.mode, drag.start, drag.from, pointerLonLat(e), bounds)
      : drag.last;
    onChange(final, false);
  };

  const onKeyDown = (e: KeyboardEvent<SVGGElement>) => {
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    const next = nudge(transform, e.key, e.shiftKey);
    if (!next) return;
    e.preventDefault();
    nudgingRef.current = true;
    onChange(next, true);
  };

  /** 微調結束（放開方向鍵或失去焦點）：立刻吸附。 */
  const settleNudge = () => {
    if (!nudgingRef.current) return;
    nudgingRef.current = false;
    onChange(transform, false);
  };

  const onKeyUp = (e: KeyboardEvent<SVGGElement>) => {
    if (isNudgeKey(e.key)) settleNudge();
  };

  const recenter = () => {
    const { lng, lat } = map.getCenter();
    onChange({ ...transform, center: [lng, lat] }, false);
  };

  const handles = handlePositions(transform, bounds);
  const topCenter = transformPoint([0, bounds.halfHeight], transform);
  const rotateScreen = map.project(handles.rotate);

  return (
    <>
      <svg
        ref={svgRef}
        className="pointer-events-none absolute inset-0 h-full w-full touch-none"
        aria-label="目標圖形編輯器"
      >
        <g
          ref={groupRef}
          tabIndex={0}
          role="group"
          aria-label="目標圖形，方向鍵微調位置"
          aria-roledescription="可拖曳圖形"
          style={{ touchAction: "none" }}
          className="outline-none [&:focus>polygon]:[stroke-opacity:1] [&:focus>polygon]:[stroke-width:3]"
          onKeyDown={onKeyDown}
          onKeyUp={onKeyUp}
          onBlur={settleNudge}
          onPointerMove={move}
          onPointerUp={(e) => finish(e, true)}
          onPointerCancel={(e) => finish(e, false)}
          onLostPointerCapture={(e) => finish(e, false)}
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
          {!targetHidden &&
            shape.strokes.map((stroke, i) => (
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
          <g aria-hidden="true">
            <circle
              data-testid="handle-rotate"
              cx={rotateScreen.x}
              cy={rotateScreen.y}
              r={HIT_RADIUS}
              fill="transparent"
              className="cursor-grab"
              style={INTERACTIVE}
              onPointerDown={(e) => begin("rotate", e)}
            />
            <circle cx={rotateScreen.x} cy={rotateScreen.y} r={9} fill="white" stroke={ACCENT} strokeWidth={2} />
          </g>
          {handles.corners.map((corner, i) => {
            const { x, y } = map.project(corner);
            return (
              <g key={i} aria-hidden="true">
                <circle
                  data-testid="handle-scale"
                  cx={x}
                  cy={y}
                  r={HIT_RADIUS}
                  fill="transparent"
                  className={CORNER_CURSORS[i]}
                  style={INTERACTIVE}
                  onPointerDown={(e) => begin("scale", e)}
                />
                <rect x={x - 7} y={y - 7} width={14} height={14} fill="white" stroke={ACCENT} strokeWidth={2} />
              </g>
            );
          })}
        </g>
      </svg>
      <div className="absolute top-3 left-3 flex flex-wrap gap-2">
        <Button size="sm" variant="secondary" className="shadow" onClick={recenter}>
          圖形移到畫面中央
        </Button>
        {/* 快捷鍵 H 在 studio 處理（焦點不在圖形上也能用）。切換按鈕的名稱固定、狀態由 aria-pressed 表達（WAI-ARIA toggle button） */}
        <Button
          size="sm"
          variant="secondary"
          className="shadow"
          aria-pressed={targetHidden}
          aria-keyshortcuts="H"
          title="快捷鍵：H"
          onClick={onToggleTarget}
        >
          隱藏原圖
        </Button>
      </div>
    </>
  );
}
