"use client";

import { X } from "lucide-react";
import type { Map as MapLibreMap } from "maplibre-gl";
import { useEffect, useMemo, useState } from "react";
import { MapView } from "@/components/map/map-view";
import { RouteEditLayer } from "@/components/map/route-edit-layer";
import { ShapeOverlay } from "@/components/map/shape-overlay";
import { ImageSource } from "@/components/source/image-source";
import { TextSource } from "@/components/source/text-source";
import { DraftsPanel } from "@/components/studio/drafts-panel";
import { EditToolbar } from "@/components/studio/edit-toolbar";
import { EngineBanner } from "@/components/studio/engine-banner";
import { InfoPanel } from "@/components/studio/info-panel";
import { ReverifyNotice } from "@/components/studio/reverify-notice";
import { SearchPanel } from "@/components/studio/search-panel";
import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useDrafts } from "@/hooks/use-drafts";
import { useEngineStatus } from "@/hooks/use-engine-status";
import { useLiveMatch } from "@/hooks/use-live-match";
import { useReverify } from "@/hooks/use-reverify";
import { useRouteEdit } from "@/hooks/use-route-edit";
import { useSearch } from "@/hooks/use-search";
import { isOutdatedRoute, outdatedLabel } from "@/lib/drafts/draft-info";
import { routeFromResponse } from "@/lib/edit/edit-bridge";
import type { DraftNotice } from "@/lib/edit/edit-hint";
import { createEditSession } from "@/lib/edit/edit-session";
import type { MatchWarning, SearchCandidate } from "@/lib/engine/contract";
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
import {
  displayedRoute,
  loadRouteVariant,
  saveRouteVariant,
  type RouteVariant,
} from "@/lib/storage/route-variant";
import { notifyDraftsChanged, openDraftDb, requestPersist, type Draft } from "@/lib/storage/drafts";
import { browserStore, loadWork, saveWork, type SavedSource } from "@/lib/storage/work";
import {
  canEnterEdit,
  currentBuildVersion,
  editSupersededBy,
  liveMatchEnabled,
  resolveDisplayedRoute,
  type VerifiedBuild,
} from "@/lib/studio/edit-view";
import { editHotkey, isToggleTargetKey } from "@/lib/studio/hotkeys";
import {
  canDownloadGpx,
  gpxName,
  isSavable,
  outcomeWarnings,
  shapeKey,
  shapeOfSource,
  visibleOutcome,
} from "@/lib/studio/state";
import type { HersheyFontId } from "@/lib/text/hershey";

type Mode = "text" | "image";

const NO_PICKS: readonly [number, number][] = [];
const NO_WARNINGS: readonly MatchWarning[] = [];
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
  const [routeVariant, setRouteVariant] = useState<RouteVariant>(() => loadRouteVariant(browserStore));
  // 自動搜尋的範圍：設定後，即時吸附的每個請求都帶 start_circle（手動微調時起點仍盡量在圈內）
  const [searchArea, setSearchArea] = useState<SearchArea | null>(null);
  const [map, setMap] = useState<MapLibreMap | null>(null);
  const engine = useEngineStatus();
  // 微調：狀態、對引擎的請求、自動存的草稿。草稿存檔的結果（超過上限、存不了）要讓使用者看到
  const [draftNotice, setDraftNotice] = useState<DraftNotice | null>(null);
  const [editSession] = useState(() =>
    createEditSession({
      openDb: () => openDraftDb(),
      requestPersist,
      notifyChanged: notifyDraftsChanged,
      onStatus: (status) => setDraftNotice(status === "saved" ? null : status),
    }),
  );
  const edit = useRouteEdit({ postEdit: editSession.postEdit, saveDraft: editSession.saveDraft });
  const drafts = useDrafts();
  // 重新驗證通過的版本：/status 是開頁時讀的，引擎重建路網之後它就過時了（見 currentBuildVersion）
  const [verifiedBuild, setVerifiedBuild] = useState<VerifiedBuild | null>(null);
  const buildVersion = currentBuildVersion(engine.dataVersion, verifiedBuild);
  const { replaceRoute } = edit;
  const { refresh: refreshEngineStatus } = engine;
  const reverify = useReverify(edit.state?.history.present.routeSig ?? null, (route, from) => {
    setVerifiedBuild({ seenEngineVersion: engine.dataVersion, version: route.dataVersion });
    replaceRoute(route, from);
    refreshEngineStatus();
  });

  const source = useMemo<SavedSource>(
    () => (mode === "text" ? { kind: "text", ...textInput } : { kind: "image", strokes: imageStrokes }),
    [mode, textInput, imageStrokes],
  );
  const shape = useMemo<Shape | null>(() => shapeOfSource(source), [source]);
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
  // planner_ready 時，立刻送出的請求在 fast 之後接著送描形版；兩版同時保留，依使用者偏好顯示
  const { response, fineResponse, error, pending, appliedTag, appliedKey, currentKey, retry, refine } = useLiveMatch(
    request,
    dragging,
    // 微調（含按了「完成」之後還留著的微調路線）：吸附一律停掉，進行中的請求取消、遲到的結果丟掉
    liveMatchEnabled(engine.status === "ok", edit.state),
    engine.markDown,
    currentShapeKey,
    engine.plannerReady,
  );
  const visibleFast = useMemo(
    () => visibleOutcome({ response, error }, appliedTag, currentShapeKey),
    [response, error, appliedTag, currentShapeKey],
  );
  // 描形版除了圖形相同，還必須屬於目前擺放；換位置而新 fast 尚未回來時不能沿用舊描形版。
  const currentFine = useMemo(
    () =>
      engine.plannerReady && appliedTag === currentShapeKey && appliedKey !== null && appliedKey === currentKey
        ? fineResponse
        : null,
    [engine.plannerReady, appliedTag, currentShapeKey, appliedKey, currentKey, fineResponse],
  );
  const shown = useMemo(
    () => displayedRoute(routeVariant, visibleFast.response, currentFine),
    [routeVariant, visibleFast.response, currentFine],
  );
  const visible = useMemo(
    () => ({ response: shown.response, error: visibleFast.error }),
    [shown.response, visibleFast.error],
  );
  // 圖形的 tag 只分得出「哪個圖形」；下載要分到「哪個擺放」：結果所屬請求的 key 等於目前請求的 key。
  // 描形版與 fast 共用同一個 key；response 則是目前實際顯示的版本。
  const liveDownloadable = canDownloadGpx(visible.response, appliedKey, currentKey);
  // 畫面、資訊面板與 GPX 的路線：有微調（含按了「完成」之後）就是微調後的，否則是即時吸附的
  const editedRoute = edit.state?.history.present ?? null;
  // 草稿是在舊路網上微調的（或引擎在微調中回 409「路網已更新」）：照常顯示、可以下載 GPX，要編輯先重新驗證
  const routeStale = edit.state?.errorCode === "stale_data";
  const outdated = editedRoute !== null && (routeStale || isOutdatedRoute(editedRoute.dataVersion, buildVersion));
  const display = useMemo(
    () => resolveDisplayedRoute(editedRoute, { response: visible.response, downloadable: liveDownloadable }, targetLengthM),
    [editedRoute, visible.response, liveDownloadable, targetLengthM],
  );
  const downloadable = display.downloadable;
  // 微調過的路線沒有警告（引擎不回半成品）；即時吸附的警告屬於被取代的路線，不再標紅
  const liveWarnings = outcomeWarnings(visible);
  const warnings = display.edited ? NO_WARNINGS : liveWarnings;
  const warned = useMemo(() => new Set(warnings.map((w) => w.stroke_index)), [warnings]);
  const editing = edit.state?.active === true;
  const canOperate = editing && !outdated;
  const editPicks = useMemo(
    () => (editing && edit.state ? edit.state.picks.map((p) => p.point) : NO_PICKS),
    [editing, edit.state],
  );
  // 搜尋結果屬於搜尋當時的圖形（候選的擺放只對它有意義）：圖形換了就不顯示、進行中的取消
  const search = useSearch(currentShapeKey, engine.markDown);
  const blocker = searchBlocker({
    hasShape: shape !== null,
    problem,
    engineStatus: engine.status,
    searchReady: engine.searchReady,
  });
  const routeStart = display.response?.route.points[0];
  const startFromCenter = searchArea && routeStart ? startDistance(searchArea, routeStart) : null;

  // 拖曳中圖形變成 null 時編輯器會卸載、收不到放開事件：在這裡結束拖曳，否則自動存檔會一直停著
  if (dragging && !shape) setDragging(false);

  // 拖曳中不存，放開或修改後才存；不合法的來源不存（保留上一份合法的作品）
  useEffect(() => {
    if (dragging || !isSavable(source)) return;
    saveWork(browserStore, { source, transform });
  }, [source, transform, dragging]);

  // 使用者移動了圖形（拖曳、鍵盤、套用搜尋候選、改文字、換圖片）：離開微調、回到即時吸附。
  // 微調的路線已經在草稿裡（每次編輯都存），不會消失。
  const { reset: resetEdit } = edit;
  const hasEdit = edit.state !== null;
  useEffect(() => {
    if (hasEdit && editSupersededBy(editSession.base(), { shapeKey: currentShapeKey, transform })) {
      resetEdit();
    }
  }, [hasEdit, currentShapeKey, transform, resetEdit, editSession]);

  const canEnter = canEnterEdit({
    response: display.response,
    downloadable: display.downloadable,
    pending,
    edit: edit.state,
  });
  const enterEdit = () => {
    if (!canEnter || !request || !display.response) return;
    editSession.begin({ source, transform, strokes: request.strokes, base: { shapeKey: currentShapeKey, transform } });
    setDraftNotice(null);
    edit.enter(routeFromResponse(display.response), null);
  };

  const startReverify = () => {
    const strokes = editSession.strokes();
    if (editedRoute && strokes) reverify.run(editedRoute, strokes);
  };

  // 回到自動吸附：擺放不變、重新吸附；微調的路線在草稿裡，舊草稿保留
  const backToAuto = () => {
    reverify.cancel();
    resetEdit();
  };

  // 打開草稿：還原來源、擺放與路線，進入微調。先 begin 再 enter：基準要是草稿的圖形與擺放，
  // 否則「圖形被移動」的判斷會拿舊的基準、馬上把微調結束。
  const openDraft = (draft: Draft) => {
    const draftShape = shapeOfSource(draft.source);
    if (!draftShape) return;
    reverify.cancel();
    if (draft.source.kind === "text") {
      setMode("text");
      setTextInput({ text: draft.source.text, font: draft.source.font });
    } else {
      setMode("image");
      setImageStrokes(draft.source.strokes);
    }
    setTransform(draft.transform);
    setDragging(false);
    editSession.begin({
      source: draft.source,
      transform: draft.transform,
      strokes: buildMatchRequest(draftShape, draft.transform).strokes,
      base: { shapeKey: shapeKey(draftShape), transform: draft.transform },
    });
    setDraftNotice(null);
    edit.enter(draft.route, draft.id);
    void drafts.markOpened(draft.id);
  };

  // 刪掉正在編輯的那一份：結束微調，否則下一次存檔會把它重新建立回來
  const removeDraft = (draft: Draft) => {
    if (edit.state?.draftId === draft.id) backToAuto();
    void drafts.remove(draft.id);
  };

  const chooseRouteVariant = (variant: RouteVariant) => {
    setRouteVariant(variant);
    saveRouteVariant(browserStore, variant);
  };

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

  // 微調中的快捷鍵：⌘／Ctrl＋Z 復原、Shift＋⌘／Ctrl＋Z 重做、Esc 取消正在選的操作（打字的地方除外，見 editHotkey）
  const { undo, redo, cancel } = edit;
  useEffect(() => {
    if (!canOperate) return;
    const actions = { undo, redo, cancel };
    const onKeyDown = (e: KeyboardEvent) => {
      const key = editHotkey(e);
      if (!key) return;
      e.preventDefault();
      actions[key]();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [canOperate, undo, redo, cancel]);

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
          response={display.response}
          fastResponse={visibleFast.response}
          fineResponse={currentFine}
          error={display.edited ? null : visible.error}
          pending={display.edited ? (edit.state?.pending ?? false) : pending}
          pendingLabel={display.edited ? "微調中…" : "吸附中…"}
          edited={display.edited}
          editAction={
            display.edited
              ? editing || outdated
                ? null
                : { label: "繼續微調", disabled: false, onClick: edit.resume }
              : { label: "微調路線", disabled: !canEnter, onClick: enterEdit }
          }
          plannerReady={engine.plannerReady && !display.edited}
          routeVariant={routeVariant}
          onRouteVariantChange={chooseRouteVariant}
          refineStatus={display.edited ? "idle" : refine}
          downloadable={downloadable}
          onRetry={retry}
          warnings={warnings}
          problem={problem}
          dataVersion={engine.dataVersion}
          workName={gpxName(source)}
          startDistance={startFromCenter}
        />
        <DraftsPanel
          view={drafts.view}
          openId={edit.state?.draftId ?? null}
          engineVersion={buildVersion}
          onOpen={openDraft}
          onRemove={removeDraft}
        />
      </aside>
      <main className="relative order-1 min-h-0 flex-1 md:order-2">
        <MapView
          initialCenter={transform.center}
          response={display.response}
          editPicks={editPicks}
          badSegments={reverify.state.bad}
          searchArea={searchArea}
          preview={search.active ? (search.best?.match ?? null) : null}
          onMapChange={setMap}
          onError={() => setMapNotice(true)}
        >
          {(map) => (
            <>
              {shape && (
                <ShapeOverlay
                  map={map}
                  shape={shape}
                  transform={transform}
                  warned={warned}
                  targetHidden={targetHidden}
                  onToggleTarget={() => setTargetHidden((hidden) => !hidden)}
                  locked={editing}
                  onChange={onTransform}
                />
              )}
              <RouteEditLayer
                map={map}
                route={display.edited ? display.response : null}
                tool={edit.state?.tool ?? null}
                enabled={canOperate && edit.state?.pending === false}
                onPick={edit.pick}
                onDrag={edit.drag}
              />
            </>
          )}
        </MapView>
        {outdated && editedRoute && (
          <div className="pointer-events-none absolute inset-x-3 bottom-14 z-10 flex justify-center">
            <ReverifyNotice
              label={routeStale ? `路網已更新。${outdatedLabel(editedRoute.dataVersion)}` : outdatedLabel(editedRoute.dataVersion)}
              state={reverify.state}
              onReverify={startReverify}
              onBackToAuto={backToAuto}
            />
          </div>
        )}
        {canOperate && edit.state && (
          <div className="pointer-events-none absolute inset-x-3 bottom-14 z-10 flex justify-center">
            <EditToolbar
              state={edit.state}
              canUndo={edit.canUndo}
              canRedo={edit.canRedo}
              draftNotice={draftNotice}
              onTool={edit.selectTool}
              onMode={edit.chooseMode}
              onUndo={edit.undo}
              onRedo={edit.redo}
              onFinish={edit.finish}
              onReverify={startReverify}
            />
          </div>
        )}
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
