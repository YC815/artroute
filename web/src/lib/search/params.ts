import type { Placement, SearchRequest, SearchStage, StartCircle } from "@/lib/engine/contract";
import { toLocal } from "@/lib/geo/local-plane";
import { MAX_SEARCH_KM, MIN_SEARCH_KM } from "@/lib/limits";
import { MAX_SIZE_M, MIN_SIZE_M, type Transform } from "@/lib/shape/transform";
import type { LonLat, Shape } from "@/lib/shape/types";

/** 自動搜尋的範圍：路線起點想落在的圓（與 /match 的 start_circle 同形）。 */
export type SearchArea = StartCircle;

/** 預設半徑 3 km（spec §4.6 的時間目標以 R ≤ 5 km 為準）。 */
export const DEFAULT_RADIUS_M = 3000;

export type KmRange = { min: number; max: number };

/**
 * 路線長度只比目標圖形長：還有部件之間的連接線與繞路。
 * 以目前擺放的目標長度為基準，預設找 1.2–2.5 倍長的路線（同樣大小上下的作品）。
 */
const KM_RANGE_FACTORS = { min: 1.2, max: 2.5 } as const;

const clampKm = (km: number) => Math.min(MAX_SEARCH_KM, Math.max(MIN_SEARCH_KM, km));
const roundKm = (km: number) => Math.round(km * 10) / 10;

/** 預設的公里數範圍（取到 0.1 km、夾在 1–300 km）；最大至少比最小多 1 km，避免範圍窄到找不到。 */
export function defaultKmRange(targetLengthM: number): KmRange {
  const km = targetLengthM / 1000;
  const min = clampKm(roundKm(km * KM_RANGE_FACTORS.min));
  const max = clampKm(Math.max(roundKm(km * KM_RANGE_FACTORS.max), min + 1));
  return { min, max };
}

/**
 * 預設的旋轉範圍（±度）：Strava 的地圖北朝上，轉太多就認不出來，文字與圖片都 ±15°（P2.5 spec §5.3）。
 * 使用者仍可在搜尋面板放寬到 ±180°（不限）。
 */
const DEFAULT_ROTATION_SPAN: Record<Shape["kind"], number> = { text: 15, image: 15 };

export function defaultRotationSpan(kind: Shape["kind"]): number {
  return DEFAULT_ROTATION_SPAN[kind];
}

/** 使用者輸入的公里數 → 數字；不是 1–300 的有限數字時回傳 null。 */
export function parseKm(text: string): number | null {
  if (text.trim() === "") return null;
  const km = Number(text);
  return Number.isFinite(km) && km >= MIN_SEARCH_KM && km <= MAX_SEARCH_KM ? km : null;
}

/** 公里數範圍的問題（給使用者看）；沒問題時回傳 null。 */
export function kmRangeProblem(min: number | null, max: number | null): string | null {
  if (min === null || max === null) return `公里數要介於 ${MIN_SEARCH_KM}–${MAX_SEARCH_KM}`;
  if (min > max) return "最小公里數不能大於最大公里數";
  return null;
}

/** /search 請求：圖形用正規化座標原樣送出；旋轉範圍以正北為 0、順時針，取 ±span。 */
export function buildSearchRequest(shape: Shape, area: SearchArea, km: KmRange, rotationSpan: number): SearchRequest {
  return {
    strokes: shape.strokes.map((s) => ({ points: s.points, closed: s.closed })),
    center: area.center,
    radius_m: area.radius_m,
    min_km: km.min,
    max_km: km.max,
    rotation_min_deg: -rotationSpan,
    rotation_max_deg: rotationSpan,
  };
}

/**
 * 候選的擺放 → 編輯器的 Transform（同一套約定，一對一）。
 * 大小夾在編輯器的範圍（200 m–20 km）：超出的擺放存檔後讀不回來；被夾到時重新吸附的路線會和候選略有不同。
 */
export function placementToTransform(placement: Placement): Transform {
  return {
    center: placement.center,
    sizeM: Math.min(MAX_SIZE_M, Math.max(MIN_SIZE_M, placement.size_m)),
    rotationDeg: placement.rotation_deg,
  };
}

/** 各階段在進度條上占的區間（%）：精算（每個候選跑完整吸附）最慢。 */
const STAGE_SPANS: Record<SearchStage, [number, number]> = {
  coarse: [0, 40],
  refine: [40, 55],
  match: [55, 100],
};

export const STAGE_LABELS: Record<SearchStage, string> = {
  coarse: "粗篩",
  refine: "微調",
  match: "精算",
};

/** 整體進度百分比（整數）。total 為 0 時算階段開頭；done 超出 [0, total] 時夾住，不會跑出階段的區間。 */
export function progressPercent(stage: SearchStage, done: number, total: number): number {
  const [from, to] = STAGE_SPANS[stage];
  const fraction = total > 0 ? Math.min(1, Math.max(0, done / total)) : 0;
  return Math.round(from + (to - from) * fraction);
}

type BlockerInput = {
  hasShape: boolean;
  /** shapeProblem 的結果。 */
  problem: string | null;
  engineStatus: "checking" | "ok" | "down";
  searchReady: boolean;
};

/** 不能搜尋的原因（給使用者看）；可以搜尋時回傳 null。 */
export function searchBlocker({ hasShape, problem, engineStatus, searchReady }: BlockerInput): string | null {
  if (!hasShape) return "先輸入文字或上傳圖片";
  if (problem) return `圖形不能搜尋：${problem}`;
  if (engineStatus === "checking") return "檢查引擎中，稍後才能搜尋";
  if (engineStatus === "down") return "引擎離線中，不能搜尋";
  if (!searchReady) return "目前的路網沒有自動搜尋需要的資料（要重建路網），仍可手動擺放";
  return null;
}

/** 浮點數來回換算的誤差：剛好在圓周上的點不能因此變成圈外。 */
const EDGE_TOLERANCE_M = 1e-6;

/** 路線起點離圓心多遠、是否在圈外（局部切平面距離，與 Transform 同一套；10 km 內誤差可忽略）。 */
export function startDistance(area: SearchArea, start: LonLat): { distanceM: number; outside: boolean } {
  const [east, north] = toLocal(area.center, start);
  const distanceM = Math.hypot(east, north);
  return { distanceM, outside: distanceM > area.radius_m + EDGE_TOLERANCE_M };
}
