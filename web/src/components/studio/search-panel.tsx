"use client";

import { useId, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Slider } from "@/components/ui/slider";
import type { SearchControls } from "@/hooks/use-search";
import type { SearchCandidate, SearchResult } from "@/lib/engine/contract";
import { formatCount, formatDistance, formatSeconds } from "@/lib/format";
import { MAX_SEARCH_AREA_RADIUS_M, MAX_SEARCH_KM, MAX_SEARCH_ROTATION_DEG, MIN_SEARCH_KM } from "@/lib/limits";
import {
  DEFAULT_RADIUS_M,
  STAGE_LABELS,
  defaultKmRange,
  defaultRotationSpan,
  kmRangeProblem,
  parseKm,
  progressPercent,
  type KmRange,
  type SearchArea,
} from "@/lib/search/params";
import type { SearchStatus } from "@/lib/search/state";
import type { LonLat, Shape } from "@/lib/shape/types";

/** 半徑滑桿：0.5–10 km，每格 100 m（引擎接受 300 m 起，滑桿從 500 m 起比較好拉）。 */
const RADIUS_SLIDER = { min: 500, max: MAX_SEARCH_AREA_RADIUS_M, step: 100 } as const;
const ROTATION_STEP = 5;

type Props = {
  /** 搜尋範圍；null 代表還沒設定（設定後即時吸附也會帶著它）。 */
  area: SearchArea | null;
  onAreaChange: (area: SearchArea | null) => void;
  /** 目前圖形的中心（transform.center）：第一次設定範圍時的圓心。 */
  shapeCenter: LonLat;
  /** 讀地圖目前的中心；地圖還沒建立時為 null。 */
  getMapCenter: (() => LonLat) | null;
  shapeKind: Shape["kind"] | null;
  /** 目前擺放的目標長度（公尺），用來給公里數的預設值；沒有圖形時為 null。 */
  targetLengthM: number | null;
  /** 不能搜尋的原因（見 searchBlocker）；可以搜尋時為 null。 */
  blocker: string | null;
  search: SearchControls;
  onStart: (km: KmRange, rotationSpan: number) => void;
  /** 套用候選：把圖形擺到候選的位置（之後照常手動微調、即時吸附、下載）。 */
  onApply: (candidate: SearchCandidate) => void;
};

/** 公里數輸入框的文字；null 代表使用者還沒改過，跟著目標長度的預設值走。 */
type KmInput = { min: string; max: string } | null;

function StatusLine({ status }: { status: SearchStatus }) {
  if (status.phase === "starting") return <p>送出搜尋…</p>;
  if (status.phase === "queued") return <p>排隊中：前面還有 {status.position} 個搜尋</p>;
  if (status.phase !== "running") return null;
  const percent = progressPercent(status.stage, status.done, status.total);
  return (
    <div className="flex flex-col gap-1">
      <p>
        {STAGE_LABELS[status.stage]} {status.done}/{status.total}
      </p>
      <div
        role="progressbar"
        aria-label="搜尋進度"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={percent}
        className="h-1.5 w-full overflow-hidden rounded-full bg-muted"
      >
        <div className="h-full bg-primary transition-[width]" style={{ width: `${percent}%` }} />
      </div>
    </div>
  );
}

function CandidateList({
  result,
  applied,
  onApply,
}: {
  result: SearchResult;
  applied: SearchCandidate | null;
  onApply: (candidate: SearchCandidate) => void;
}) {
  return (
    <div className="flex flex-col gap-2">
      {result.notice && <p className="text-sm text-amber-700 dark:text-amber-400">{result.notice}</p>}
      {result.candidates.length === 0 ? (
        <p className="text-sm text-muted-foreground">範圍內找不到可用的擺放，試試擴大半徑或放寬公里數。</p>
      ) : (
        <ol className="flex flex-col gap-1.5" aria-label="搜尋結果">
          {result.candidates.map((candidate, i) => (
            // 候選沒有 id，順序就是名次；新的結果會整份換掉
            <li key={i}>
              <Button
                variant={candidate === applied ? "secondary" : "outline"}
                aria-pressed={candidate === applied}
                className="h-auto w-full flex-col items-start gap-1 py-2 text-left whitespace-normal"
                onClick={() => onApply(candidate)}
              >
                <span className="flex w-full items-center justify-between gap-2">
                  <span className="font-semibold">第 {i + 1} 名</span>
                  {!candidate.length_ok && <Badge variant="destructive">公里數不符</Badge>}
                </span>
                <span className="grid w-full grid-cols-3 gap-1 text-xs font-normal text-muted-foreground tabular-nums">
                  <span>整體貼合度 {candidate.match.score.overall}%</span>
                  <span>{formatDistance(candidate.match.route.length_m)}</span>
                  <span>起點離圓心 {formatDistance(candidate.start_distance_m)}</span>
                </span>
              </Button>
            </li>
          ))}
        </ol>
      )}
      <p className="text-xs text-muted-foreground">
        粗篩 {formatCount(result.coarse_candidates)} 個擺放、精算 {formatCount(result.evaluated)} 個，耗時{" "}
        {formatSeconds(result.elapsed_ms)}。點選候選後仍可手動微調。
      </p>
    </div>
  );
}

/**
 * 自動搜尋（spec §4.6）：設定範圍（圓心、半徑）、公里數與旋轉範圍，送出搜尋，顯示排隊、進度、即時預覽與前 5 名。
 * 點選候選就把圖形擺到那個位置，之後的手動編輯、即時吸附與下載 GPX 都照舊；範圍存在時即時吸附會帶上 start_circle。
 */
export function SearchPanel(props: Props) {
  const { area, onAreaChange, shapeCenter, getMapCenter, shapeKind, targetLengthM, blocker, search } = props;
  const { onStart, onApply } = props;
  const headingId = useId();
  const radiusLabelId = useId();
  const rotationLabelId = useId();
  const minId = useId();
  const maxId = useId();
  const [kmInput, setKmInput] = useState<KmInput>(null);
  const [rotationInput, setRotationInput] = useState<number | null>(null);
  // 拖曳半徑滑桿時只更新顯示，放開才改範圍：範圍一變即時吸附就重送，不能每一格都送
  const [radiusDraft, setRadiusDraft] = useState<number | null>(null);
  const [applied, setApplied] = useState<SearchCandidate | null>(null);

  const defaults = defaultKmRange(targetLengthM ?? 0);
  const kmText = kmInput ?? { min: String(defaults.min), max: String(defaults.max) };
  const km = { min: parseKm(kmText.min), max: parseKm(kmText.max) };
  const kmProblem = kmRangeProblem(km.min, km.max);
  const rotationSpan = rotationInput ?? defaultRotationSpan(shapeKind ?? "text");
  const radius = radiusDraft ?? area?.radius_m ?? DEFAULT_RADIUS_M;
  const { status, best, active } = search;

  const start = () => {
    if (km.min === null || km.max === null || kmProblem || blocker) return;
    setApplied(null);
    onStart({ min: km.min, max: km.max }, rotationSpan);
  };
  const apply = (candidate: SearchCandidate) => {
    setApplied(candidate);
    onApply(candidate);
  };
  const clear = () => {
    search.cancel();
    setApplied(null);
    onAreaChange(null);
  };

  return (
    <section className="flex flex-col gap-3" aria-labelledby={headingId}>
      <div className="flex flex-col gap-1">
        <h2 id={headingId} className="text-sm font-semibold">
          自動搜尋
        </h2>
        <p className="text-xs text-muted-foreground">在範圍內找貼合度最高的擺放，路線從圈內出發。</p>
      </div>

      {area === null ? (
        <Button
          variant="outline"
          disabled={blocker !== null}
          onClick={() => onAreaChange({ center: shapeCenter, radius_m: DEFAULT_RADIUS_M })}
        >
          設定搜尋範圍
        </Button>
      ) : (
        <div className="flex flex-col gap-3">
          <div className="flex flex-col gap-1.5">
            <p className="text-sm font-medium">圓心</p>
            <div className="flex flex-wrap gap-1.5">
              <Button
                size="sm"
                variant="outline"
                disabled={!getMapCenter}
                onClick={() => getMapCenter && onAreaChange({ ...area, center: getMapCenter() })}
              >
                設為地圖中心
              </Button>
              <Button size="sm" variant="outline" onClick={() => onAreaChange({ ...area, center: shapeCenter })}>
                設為圖形位置
              </Button>
            </div>
          </div>

          {/* Radix 的滑桿把手命名不到 <label>，用群組命名（同 image-source） */}
          <div role="group" aria-labelledby={radiusLabelId} className="flex flex-col gap-2">
            <Label id={radiusLabelId}>半徑：{formatDistance(radius)}</Label>
            <Slider
              min={RADIUS_SLIDER.min}
              max={RADIUS_SLIDER.max}
              step={RADIUS_SLIDER.step}
              value={[radius]}
              onValueChange={([value]) => setRadiusDraft(value)}
              onValueCommit={([value]) => {
                setRadiusDraft(null);
                onAreaChange({ ...area, radius_m: value });
              }}
            />
          </div>

          <div className="grid grid-cols-2 gap-2">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor={minId}>最短（km）</Label>
              <Input
                id={minId}
                type="number"
                inputMode="decimal"
                min={MIN_SEARCH_KM}
                max={MAX_SEARCH_KM}
                step={0.1}
                value={kmText.min}
                aria-invalid={km.min === null || kmProblem !== null}
                onChange={(e) => setKmInput({ ...kmText, min: e.target.value })}
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor={maxId}>最長（km）</Label>
              <Input
                id={maxId}
                type="number"
                inputMode="decimal"
                min={MIN_SEARCH_KM}
                max={MAX_SEARCH_KM}
                step={0.1}
                value={kmText.max}
                aria-invalid={km.max === null || kmProblem !== null}
                onChange={(e) => setKmInput({ ...kmText, max: e.target.value })}
              />
            </div>
          </div>

          <div role="group" aria-labelledby={rotationLabelId} className="flex flex-col gap-2">
            <Label id={rotationLabelId}>
              旋轉範圍：±{rotationSpan}°{rotationSpan === MAX_SEARCH_ROTATION_DEG && "（不限）"}
            </Label>
            <Slider
              min={0}
              max={MAX_SEARCH_ROTATION_DEG}
              step={ROTATION_STEP}
              value={[rotationSpan]}
              onValueChange={([value]) => setRotationInput(value)}
            />
          </div>

          <div className="flex flex-wrap gap-1.5">
            <Button disabled={blocker !== null || kmProblem !== null || active} onClick={start}>
              開始搜尋
            </Button>
            {active && (
              <Button variant="outline" onClick={search.cancel}>
                取消搜尋
              </Button>
            )}
            <Button variant="ghost" onClick={clear}>
              清除範圍
            </Button>
          </div>
        </div>
      )}

      {/* live region 一直留在 DOM 裡：內容出現時才念得出來 */}
      <div role="alert" className="flex flex-col gap-1 text-sm text-destructive">
        {area !== null && kmProblem && <p>{kmProblem}</p>}
        {status.phase === "failed" && <p>{status.message}</p>}
      </div>
      {blocker && <p className="text-xs text-muted-foreground">{blocker}</p>}

      <div role="status" className="flex flex-col gap-1 text-sm">
        <StatusLine status={status} />
        {active && best && (
          <p className="text-muted-foreground">
            目前最佳：整體貼合度 {best.match.score.overall}%、{formatDistance(best.match.route.length_m)}
          </p>
        )}
      </div>
      {status.phase === "done" && <CandidateList result={status.result} applied={applied} onApply={apply} />}
    </section>
  );
}
