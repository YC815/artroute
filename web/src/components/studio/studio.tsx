"use client";

import { X } from "lucide-react";
import type { Map as MapLibreMap } from "maplibre-gl";
import { useEffect, useMemo, useState } from "react";
import { MapView } from "@/components/map/map-view";
import { ShapeOverlay } from "@/components/map/shape-overlay";
import { ImageSource } from "@/components/source/image-source";
import { TextSource } from "@/components/source/text-source";
import { EngineBanner } from "@/components/studio/engine-banner";
import { InfoPanel } from "@/components/studio/info-panel";
import { SearchPanel } from "@/components/studio/search-panel";
import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useEngineStatus } from "@/hooks/use-engine-status";
import { useLiveMatch } from "@/hooks/use-live-match";
import { useSearch } from "@/hooks/use-search";
import type { SearchCandidate } from "@/lib/engine/contract";
import { buildMatchRequest, shapeProblem } from "@/lib/match/request";
import {
  buildSearchRequest,
  placementToTransform,
  searchBlocker,
  startDistance,
  type KmRange,
  type SearchArea,
} from "@/lib/search/params";
import { shapeLengthM, type Transform } from "@/lib/shape/transform";
import type { Shape, Stroke } from "@/lib/shape/types";
import { browserStore, loadWork, saveWork, type SavedSource } from "@/lib/storage/work";
import { isToggleTargetKey } from "@/lib/studio/hotkeys";
import { canDownloadGpx, gpxName, isSavable, outcomeWarnings, shapeKey, visibleOutcome } from "@/lib/studio/state";
import type { HersheyFontId } from "@/lib/text/hershey";
import { textShape, validateText } from "@/lib/text/layout";

type Mode = "text" | "image";
type TextInput = { text: string; font: HersheyFontId };

/** 預設擺在台北 101 附近，長邊 2 km。 */
const DEFAULT_TRANSFORM: Transform = { center: [121.5654, 25.033], sizeM: 2000, rotationDeg: 0 };
const DEFAULT_TEXT: TextInput = { text: "I ♥ TAIPEI", font: "futural" };
/** MapView 的 onError 也會因為只有 sprite 之類的資源失敗而觸發，地圖多半仍可用，所以用溫和的說法。 */
const MAP_NOTICE = "部分地圖資源載入失敗，地圖可能顯示不完整";

export function Studio() {
  // 只在瀏覽器執行（studio-loader 以 ssr: false 載入），所以可以在初始化時讀 localStorage
  const [saved] = useState(() => loadWork(browserStore));
  const [mode, setMode] = useState<Mode>(saved?.source.kind ?? "text");
  const [textInput, setTextInput] = useState<TextInput>(
    saved?.source.kind === "text" ? { text: saved.source.text, font: saved.source.font } : DEFAULT_TEXT,
  );
  const [imageStrokes, setImageStrokes] = useState<Stroke[]>(
    saved?.source.kind === "image" ? saved.source.strokes : [],
  );
  const [transform, setTransform] = useState<Transform>(saved?.transform ?? DEFAULT_TRANSFORM);
  const [dragging, setDragging] = useState(false);
  const [mapNotice, setMapNotice] = useState(false);
  // 隱藏原圖（P2.5 spec §5.4）：只看路線像不像。預設顯示、不存檔，重新載入就恢復
  const [targetHidden, setTargetHidden] = useState(false);
  // 自動搜尋的範圍：設定後，即時吸附的每個請求都帶 start_circle（手動微調時起點仍盡量在圈內）
  const [searchArea, setSearchArea] = useState<SearchArea | null>(null);
  const [map, setMap] = useState<MapLibreMap | null>(null);
  const engine = useEngineStatus();

  const source = useMemo<SavedSource>(
    () => (mode === "text" ? { kind: "text", ...textInput } : { kind: "image", strokes: imageStrokes }),
    [mode, textInput, imageStrokes],
  );
  const shape = useMemo<Shape | null>(() => {
    if (source.kind === "image") return source.strokes.length > 0 ? { kind: "image", strokes: source.strokes } : null;
    return validateText(source.text) === null ? textShape(source.text, source.font) : null;
  }, [source]);
  const currentShapeKey = useMemo(() => shapeKey(shape), [shape]);
  // 長邊 1 m 時的目標長度：大圖片有上萬點，拖曳中每次 pointermove 重算太浪費
  const unitLengthM = useMemo(() => (shape ? shapeLengthM(shape.strokes, 1) : null), [shape]);
  const targetLengthM = unitLengthM === null ? null : unitLengthM * transform.sizeM;
  const problem = shape ? shapeProblem(shape) : null;
  const request = useMemo(
    () => (shape && !problem ? buildMatchRequest(shape, transform, searchArea) : null),
    [shape, problem, transform, searchArea],
  );
  // 每個請求送出時帶著圖形的 key：結果只在屬於目前圖形時顯示（圖形換了、新結果回來前不顯示舊圖形的路線）
  const { response, error, pending, appliedTag, appliedKey, currentKey, retry } = useLiveMatch(
    request,
    dragging,
    engine.status === "ok",
    engine.markDown,
    currentShapeKey,
  );
  const visible = useMemo(
    () => visibleOutcome({ response, error }, appliedTag, currentShapeKey),
    [response, error, appliedTag, currentShapeKey],
  );
  // 圖形的 tag 只分得出「哪個圖形」；下載要分到「哪個擺放」：結果所屬請求的 key 等於目前請求的 key
  const downloadable = canDownloadGpx(visible.response, appliedKey, currentKey);
  const warnings = outcomeWarnings(visible);
  const warned = useMemo(() => new Set(warnings.map((w) => w.stroke_index)), [warnings]);
  // 搜尋結果屬於搜尋當時的圖形（候選的擺放只對它有意義）：圖形換了就不顯示、進行中的取消
  const search = useSearch(currentShapeKey, engine.markDown);
  const blocker = searchBlocker({
    hasShape: shape !== null,
    problem,
    engineStatus: engine.status,
    searchReady: engine.searchReady,
  });
  const routeStart = visible.response?.route.points[0];
  const startFromCenter = searchArea && routeStart ? startDistance(searchArea, routeStart) : null;

  // 拖曳中圖形變成 null 時編輯器會卸載、收不到放開事件：在這裡結束拖曳，否則自動存檔會一直停著
  if (dragging && !shape) setDragging(false);

  // 拖曳中不存，放開或修改後才存；不合法的來源不存（保留上一份合法的作品）
  useEffect(() => {
    if (dragging || !isSavable(source)) return;
    saveWork(browserStore, { source, transform });
  }, [source, transform, dragging]);

  // 快捷鍵 H 切換隱藏原圖；掛在 window 上，焦點在哪裡都能用（打字的地方與帶修飾鍵的除外，見 isToggleTargetKey）
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.defaultPrevented || !isToggleTargetKey(e)) return;
      e.preventDefault();
      setTargetHidden((hidden) => !hidden);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  const onTransform = (next: Transform, isDragging: boolean) => {
    setTransform(next);
    setDragging(isDragging);
  };

  const startSearch = (km: KmRange, rotationSpan: number) => {
    if (!shape || problem || !searchArea || currentShapeKey === null) return;
    search.start(buildSearchRequest(shape, searchArea, km, rotationSpan), currentShapeKey);
  };

  // 套用候選＝把圖形擺到那裡（等同放開拖曳）：即時吸附立刻重送，之後照常手動編輯、下載
  const applyCandidate = (candidate: SearchCandidate) => onTransform(placementToTransform(candidate.placement), false);

  const getMapCenter = map
    ? () => {
        const { lng, lat } = map.getCenter();
        return [lng, lat] as [number, number];
      }
    : null;

  return (
    <div className="flex h-dvh flex-col md:flex-row">
      <aside className="order-2 flex max-h-[45dvh] w-full shrink-0 flex-col gap-4 overflow-y-auto border-t p-4 md:order-1 md:max-h-none md:w-96 md:border-t-0 md:border-r">
        <header>
          <h1 className="text-lg font-semibold">artroute</h1>
          <p className="text-sm text-muted-foreground">把文字或圖片放到地圖上，變成合法可騎的自行車路線。</p>
        </header>
        <EngineBanner status={engine.status} onRetry={engine.retry} />
        <Tabs value={mode} onValueChange={(value) => setMode(value as Mode)}>
          <TabsList className="w-full">
            <TabsTrigger value="text">文字</TabsTrigger>
            <TabsTrigger value="image">圖片</TabsTrigger>
          </TabsList>
          <TabsContent value="text">
            <TextSource
              text={textInput.text}
              font={textInput.font}
              onChange={(text, font) => setTextInput({ text, font })}
            />
          </TabsContent>
          {/* forceMount：切換分頁時保留已上傳的圖片與輪廓 */}
          <TabsContent value="image" forceMount className="data-[state=inactive]:hidden">
            <ImageSource hasShape={imageStrokes.length > 0} onStrokes={setImageStrokes} />
          </TabsContent>
        </Tabs>
        <SearchPanel
          area={searchArea}
          onAreaChange={setSearchArea}
          shapeCenter={transform.center}
          getMapCenter={getMapCenter}
          shapeKind={shape?.kind ?? null}
          targetLengthM={targetLengthM}
          blocker={blocker}
          search={search}
          onStart={startSearch}
          onApply={applyCandidate}
        />
        <InfoPanel
          targetLengthM={targetLengthM}
          response={visible.response}
          error={visible.error}
          pending={pending}
          downloadable={downloadable}
          onRetry={retry}
          warnings={warnings}
          problem={problem}
          dataVersion={engine.dataVersion}
          workName={gpxName(source)}
          startDistance={startFromCenter}
        />
      </aside>
      <main className="relative order-1 min-h-0 flex-1 md:order-2">
        <MapView
          initialCenter={transform.center}
          response={visible.response}
          searchArea={searchArea}
          preview={search.active ? (search.best?.match ?? null) : null}
          onMapChange={setMap}
          onError={() => setMapNotice(true)}
        >
          {(map) =>
            shape && (
              <ShapeOverlay
                map={map}
                shape={shape}
                transform={transform}
                warned={warned}
                targetHidden={targetHidden}
                onToggleTarget={() => setTargetHidden((hidden) => !hidden)}
                onChange={onTransform}
              />
            )
          }
        </MapView>
        {/* 不擋操作：只有關閉按鈕接收指標事件，圖形照樣可以拖曳、吸附。live region 一直存在，提示出現時才念得出來 */}
        <div role="status" className="pointer-events-none absolute inset-x-3 bottom-10 z-10 flex justify-center">
          {mapNotice && (
            <div className="flex items-center gap-1 rounded-md bg-background/90 py-1 pr-1 pl-3 text-sm shadow">
              <p>{MAP_NOTICE}</p>
              <Button
                size="icon-xs"
                variant="ghost"
                className="pointer-events-auto"
                aria-label="關閉地圖提示"
                onClick={() => setMapNotice(false)}
              >
                <X />
              </Button>
            </div>
          )}
        </div>
      </main>
    </div>
  );
}
