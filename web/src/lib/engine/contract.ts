// 引擎 /match、/edit、/verify、/search、/status 的契約（spec §4.6、§4.7）。
//
// 型別的唯一來源是 engine/openapi.json：`pnpm gen:engine-types` 產生 openapi.d.ts，這裡只取用。
// zod schema 負責執行期驗證；請求、回應與 /search 的每一種 SSE 事件
// 都以 `satisfies z.ZodType<產生的型別>` 在編譯期對齊，引擎改了形狀而 web 沒跟上時 tsc 會失敗。
// errorDetailSchema 刻意不綁：它是引擎錯誤 detail 的超集，另外涵蓋代理自己的 code
// （too_large／validation／engine_unreachable／timeout；timeout 與引擎同名）。
// 其他模組一律只從這裡 import 契約型別與 schema。
import { z } from "zod";
import type { components } from "@/lib/engine/openapi";
import {
  MAX_DATA_VERSION_CHARS,
  MAX_NORM_COORD,
  MAX_ROUTE_SEGMENTS,
  MAX_ROUTE_SIG_CHARS,
  MAX_SEGMENT_POINTS,
  MAX_SEARCH_AREA_RADIUS_M,
  MAX_SEARCH_CANDIDATES,
  MAX_SEARCH_KM,
  MAX_SEARCH_RADIUS_M,
  MAX_SEARCH_ROTATION_DEG,
  MAX_STROKE_POINTS,
  MAX_STROKES,
  MIN_SEARCH_AREA_RADIUS_M,
  MIN_SEARCH_KM,
  MIN_SEARCH_RADIUS_M,
  MIN_STROKE_POINTS,
} from "@/lib/limits";

type Schemas = components["schemas"];

// zod 的錯誤訊息用繁體中文（會顯示給使用者）
z.config(z.locales.zhTW());

/** [經度, 緯度] */
const lonLatSchema = z.tuple([z.number(), z.number()]);

/** 自動搜尋的範圍；/match 帶著它時，引擎盡量讓路線從圈內、離圓心最近的地方出發（只是提示）。 */
export const startCircleSchema = z.object({
  center: lonLatSchema,
  radius_m: z.number().min(MIN_SEARCH_AREA_RADIUS_M).max(MAX_SEARCH_AREA_RADIUS_M),
}) satisfies z.ZodType<Schemas["StartCircle"]>;

const strokesSchema = z
  .array(
    z.object({
      points: z.array(lonLatSchema).min(MIN_STROKE_POINTS).max(MAX_STROKE_POINTS),
      closed: z.boolean(),
    }),
  )
  .min(1)
  .max(MAX_STROKES);

export const matchRequestSchema = z.object({
  strokes: strokesSchema,
  search_radius_m: z.number().min(MIN_SEARCH_RADIUS_M).max(MAX_SEARCH_RADIUS_M).optional(),
  // 可省略：省略時與沒有自動搜尋時的行為完全相同
  start_circle: startCircleSchema.nullish(),
  // 可省略：省略或 null 等於 fast（與以前完全相同）；fine 是放開後的描形版（P2.5 spec §6.5）。
  // 一定要列在這裡：代理用這個 schema 驗證後只轉送 schema 內的欄位，漏列會把 quality 默默丟掉。
  quality: z.enum(["fast", "fine"]).nullish(),
}) satisfies z.ZodType<Schemas["MatchRequest"]>;

/** "edit" 是微調（/edit、/verify）換上的段落；/match 不會回。 */
export const segmentKindSchema = z.enum(["stroke", "detour", "connector", "edit"]);

export const matchWarningSchema = z.object({
  stroke_index: z.number().int(),
  code: z.enum(["no_route", "partial"]),
  message: z.string(),
});

const percentSchema = z.number().min(0).max(100);

export const segmentSchema = z.object({
  kind: segmentKindSchema,
  stroke_index: z.number().int().nullable(),
  points: z.array(lonLatSchema),
  length_m: z.number(),
}) satisfies z.ZodType<Schemas["SegmentOut"]>;

export const scoreSchema = z.object({
  coverage: percentSchema,
  cleanliness: percentSchema,
  direction: percentSchema,
  overall: percentSchema,
}) satisfies z.ZodType<Schemas["ScoreOut"]>;

/** /match、/edit、/verify 的 200 回應共用。route_sig 是整條路線的簽章，下一次 /edit、/verify 原樣帶回。 */
export const matchResponseSchema = z.object({
  route: z.object({ points: z.array(lonLatSchema), length_m: z.number() }),
  segments: z.array(segmentSchema),
  score: scoreSchema,
  target_length_m: z.number(),
  warnings: z.array(matchWarningSchema),
  data_version: z.string(),
  route_sig: z.string(),
}) satisfies z.ZodType<Schemas["MatchResponse"]>;

// ---- POST /edit、POST /verify（微調 spec §3.1、§3.6） ----

/** 路線上的位置：第幾段、從該段起點沿折線量的公尺數（不用經緯度：來回騎的路會選錯是哪一趟）。 */
export const routePosSchema = z.object({
  segment: z.number().int().min(0),
  offset_m: z.number().min(0),
}) satisfies z.ZodType<Schemas["RoutePosIn"]>;

export const cutOpSchema = z.object({
  type: z.literal("cut"),
  at: routePosSchema,
}) satisfies z.ZodType<Schemas["CutOp"]>;

export const rerouteOpSchema = z.object({
  type: z.literal("reroute"),
  from: routePosSchema,
  to: routePosSchema,
  mode: z.enum(["shortest", "shape"]),
}) satisfies z.ZodType<Schemas["RerouteOp"]>;

export const dragOpSchema = z.object({
  type: z.literal("drag"),
  grab: routePosSchema,
  to: lonLatSchema,
}) satisfies z.ZodType<Schemas["DragOp"]>;

export const editOpSchema = z.discriminatedUnion("type", [cutOpSchema, rerouteOpSchema, dragOpSchema]);

/** 上一次回應的 segments 原封不動（簽章會驗），所以點數的上限是引擎的 SegmentIn。 */
const routeSegmentsSchema = z
  .array(
    z.object({
      kind: segmentKindSchema,
      stroke_index: z.number().int().min(0).nullable(),
      points: z.array(lonLatSchema).min(2).max(MAX_SEGMENT_POINTS),
      length_m: z.number().min(0),
    }),
  )
  .min(1)
  .max(MAX_ROUTE_SEGMENTS);

const signedRouteFields = {
  segments: routeSegmentsSchema,
  data_version: z.string().max(MAX_DATA_VERSION_CHARS),
  route_sig: z.string().max(MAX_ROUTE_SIG_CHARS),
  // 使用者放的原圖：算分數、「貼圖形」模式用
  strokes: strokesSchema,
};

export const editRequestSchema = z.object({
  ...signedRouteFields,
  op: editOpSchema,
}) satisfies z.ZodType<Schemas["EditRequest"]>;

export const verifyRequestSchema = z.object(signedRouteFields) satisfies z.ZodType<Schemas["VerifyRequest"]>;

/**
 * 引擎的領域錯誤（`EngineErrorCode`：outside_taiwan／too_small／no_route／no_data／timeout，
 * 以及微調的 stale_data／invalid_route／not_on_route／nothing_to_cut／turn_not_rideable／no_road／verify_failed）
 * 與代理自己的錯誤（too_large／validation／engine_unreachable／timeout）共用的 detail 形狀。
 * 「所有部件都吸附失敗」的 no_route 另附 warnings；/verify 的 verify_failed 另附有問題的段落序號 segments。
 */
export const errorDetailSchema = z.object({
  code: z.string(),
  message: z.string(),
  // 引擎的 OpenAPI 宣告 array | null：只有 no_route 會帶陣列，其餘可能是 null。讀的人一律 `?? []`。
  warnings: z.array(matchWarningSchema).nullish(),
  // 同樣 array | null：只有 verify_failed 會帶，其餘可能是 null。讀的人一律 `?? []`。
  segments: z.array(z.number().int().min(0)).nullish(),
});

/**
 * 錯誤回應 body：{ detail }。
 * detail 有三種形狀：errorDetailSchema 的領域錯誤物件、字串（舊式 HTTPException），
 * 以及 FastAPI 請求驗證失敗的 pydantic 錯誤陣列等任意值。
 * 所以型別不限（unknown），欄位本身也可省略；由 client.ts 的 readDetail 統一判別。
 */
export const errorBodySchema = z.object({
  detail: z.unknown().optional(),
});

/**
 * GET /status：data 為 null 代表引擎還沒有可用的路網。
 * search_ready：目前的路網有沒有自動搜尋需要的資料。舊版引擎不送這個欄位，缺少時當成 false（不是格式錯誤）。
 * planner_ready：目前的路網有沒有描形規劃器（quality: "fine"）需要的路網圖；同樣，缺少時當成 false。
 */
export const statusResponseSchema = z.object({
  data: z.object({ build_id: z.string(), osm_md5: z.string(), created_at: z.string() }).nullable(),
  search_ready: z.boolean().default(false),
  planner_ready: z.boolean().default(false),
}) satisfies z.ZodType<Schemas["StatusResponse"]>;

// ---- POST /search（spec §4.6）：請求是正規化圖形＋範圍，回應是 SSE，每個事件的 data 是下面其中一種 ----

const normCoordSchema = z.number().min(-MAX_NORM_COORD).max(MAX_NORM_COORD);
const rotationSchema = z.number().min(-MAX_SEARCH_ROTATION_DEG).max(MAX_SEARCH_ROTATION_DEG);
const kmSchema = z.number().min(MIN_SEARCH_KM).max(MAX_SEARCH_KM);

/** 圖形用正規化座標（Shape.strokes 原樣：x 向右、y 向上、外框中心為原點、長邊 = 1）；旋轉是順時針、0 = 上方朝北。 */
export const searchRequestSchema = z
  .object({
    strokes: z
      .array(
        z.object({
          points: z.array(z.tuple([normCoordSchema, normCoordSchema])).min(MIN_STROKE_POINTS).max(MAX_STROKE_POINTS),
          closed: z.boolean(),
        }),
      )
      .min(1)
      .max(MAX_STROKES),
    center: lonLatSchema,
    radius_m: z.number().min(MIN_SEARCH_AREA_RADIUS_M).max(MAX_SEARCH_AREA_RADIUS_M),
    min_km: kmSchema,
    max_km: kmSchema,
    rotation_min_deg: rotationSchema,
    rotation_max_deg: rotationSchema,
  })
  .refine((r) => r.min_km <= r.max_km, { message: "最小公里數不能大於最大公里數", path: ["min_km"] })
  .refine((r) => r.rotation_min_deg <= r.rotation_max_deg, {
    message: "最小旋轉角度不能大於最大旋轉角度",
    path: ["rotation_min_deg"],
  }) satisfies z.ZodType<Schemas["SearchRequest"]>;

/** 候選的擺放，與 Transform 同一套約定（size_m = sizeM、rotation_deg = rotationDeg）。 */
export const placementSchema = z.object({
  center: lonLatSchema,
  size_m: z.number().positive(),
  rotation_deg: z.number().min(0).lt(360),
}) satisfies z.ZodType<Schemas["PlacementOut"]>;

const countSchema = z.number().int().min(0);

export const searchCandidateSchema = z.object({
  placement: placementSchema,
  match: matchResponseSchema,
  start_distance_m: z.number().min(0),
  length_ok: z.boolean(),
}) satisfies z.ZodType<Schemas["SearchCandidate"]>;

export const searchQueuedEventSchema = z.object({
  type: z.literal("queued"),
  position: z.number().int().min(1),
}) satisfies z.ZodType<Schemas["SearchQueued"]>;

export const searchProgressEventSchema = z.object({
  type: z.literal("progress"),
  stage: z.enum(["coarse", "refine", "match"]),
  done: countSchema,
  total: countSchema,
}) satisfies z.ZodType<Schemas["SearchProgress"]>;

export const searchBestEventSchema = z.object({
  type: z.literal("best"),
  candidate: searchCandidateSchema,
}) satisfies z.ZodType<Schemas["SearchBest"]>;

export const searchResultEventSchema = z.object({
  type: z.literal("result"),
  candidates: z.array(searchCandidateSchema).max(MAX_SEARCH_CANDIDATES),
  coarse_candidates: countSchema,
  evaluated: countSchema,
  elapsed_ms: countSchema,
  notice: z.string().nullish(),
}) satisfies z.ZodType<Schemas["SearchResult"]>;

export const searchFailedEventSchema = z.object({
  type: z.literal("error"),
  code: z.enum(["timeout", "busy", "no_data", "internal"]),
  message: z.string(),
}) satisfies z.ZodType<Schemas["SearchFailed"]>;

/** 所有已知的 SSE 事件；SSE 的 event 名稱與 data.type 相同。 */
export const searchEventSchema = z.discriminatedUnion("type", [
  searchQueuedEventSchema,
  searchProgressEventSchema,
  searchBestEventSchema,
  searchResultEventSchema,
  searchFailedEventSchema,
]);

export type MatchRequest = Schemas["MatchRequest"];
export type MatchResponse = Schemas["MatchResponse"];
export type MatchSegment = Schemas["SegmentOut"];
export type Segment = Schemas["SegmentOut"];
export type Score = Schemas["ScoreOut"];
export type EditRequest = Schemas["EditRequest"];
export type VerifyRequest = Schemas["VerifyRequest"];
export type EditOp = Schemas["CutOp"] | Schemas["RerouteOp"] | Schemas["DragOp"];
export type RoutePos = Schemas["RoutePosIn"];
export type EngineErrorCode = Schemas["ErrorDetail"]["code"];
export type MatchWarning = Schemas["WarningOut"];
export type SegmentKind = Schemas["SegmentOut"]["kind"];
export type StatusResponse = Schemas["StatusResponse"];
export type StartCircle = Schemas["StartCircle"];
export type SearchRequest = Schemas["SearchRequest"];
export type Placement = Schemas["PlacementOut"];
export type SearchCandidate = Schemas["SearchCandidate"];
export type SearchQueued = Schemas["SearchQueued"];
export type SearchProgress = Schemas["SearchProgress"];
export type SearchStage = Schemas["SearchProgress"]["stage"];
export type SearchBest = Schemas["SearchBest"];
export type SearchResult = Schemas["SearchResult"];
export type SearchFailed = Schemas["SearchFailed"];
export type SearchEvent = SearchQueued | SearchProgress | SearchBest | SearchResult | SearchFailed;
export type SearchEventType = SearchEvent["type"];
