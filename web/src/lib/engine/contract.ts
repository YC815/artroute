// 引擎 /match、/search、/status 的契約（spec §4.6、§4.7）。
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
  MAX_NORM_COORD,
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

export const matchRequestSchema = z.object({
  strokes: z
    .array(
      z.object({
        points: z.array(lonLatSchema).min(MIN_STROKE_POINTS).max(MAX_STROKE_POINTS),
        closed: z.boolean(),
      }),
    )
    .min(1)
    .max(MAX_STROKES),
  search_radius_m: z.number().min(MIN_SEARCH_RADIUS_M).max(MAX_SEARCH_RADIUS_M).optional(),
  // 可省略：省略時與沒有自動搜尋時的行為完全相同
  start_circle: startCircleSchema.nullish(),
  // 可省略：省略或 null 等於 fast（與以前完全相同）；fine 是放開後的描形版（P2.5 spec §6.5）。
  // 一定要列在這裡：代理用這個 schema 驗證後只轉送 schema 內的欄位，漏列會把 quality 默默丟掉。
  quality: z.enum(["fast", "fine"]).nullish(),
}) satisfies z.ZodType<Schemas["MatchRequest"]>;

export const segmentKindSchema = z.enum(["stroke", "detour", "connector"]);

export const matchWarningSchema = z.object({
  stroke_index: z.number().int(),
  code: z.enum(["no_route", "partial"]),
  message: z.string(),
});

const percentSchema = z.number().min(0).max(100);

export const matchResponseSchema = z.object({
  route: z.object({ points: z.array(lonLatSchema), length_m: z.number() }),
  segments: z.array(
    z.object({
      kind: segmentKindSchema,
      stroke_index: z.number().int().nullable(),
      points: z.array(lonLatSchema),
      length_m: z.number(),
    }),
  ),
  score: z.object({
    coverage: percentSchema,
    cleanliness: percentSchema,
    direction: percentSchema,
    overall: percentSchema,
  }),
  target_length_m: z.number(),
  warnings: z.array(matchWarningSchema),
  data_version: z.string(),
}) satisfies z.ZodType<Schemas["MatchResponse"]>;

/**
 * 引擎的領域錯誤（outside_taiwan／too_small／no_route／no_data／timeout）與代理自己的錯誤
 * （too_large／validation／engine_unreachable／timeout）共用的 detail 形狀。
 * 「所有部件都吸附失敗」的 no_route 另附 warnings。
 */
export const errorDetailSchema = z.object({
  code: z.string(),
  message: z.string(),
  // 引擎的 OpenAPI 宣告 array | null：只有 no_route 會帶陣列，其餘可能是 null。讀的人一律 `?? []`。
  warnings: z.array(matchWarningSchema).nullish(),
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
