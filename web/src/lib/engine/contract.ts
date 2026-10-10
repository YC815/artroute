// 引擎 /match、/status 的契約（spec §4.7）。
//
// 型別的唯一來源是 engine/openapi.json：`pnpm gen:engine-types` 產生 openapi.d.ts，這裡只取用。
// zod schema 負責執行期驗證；matchRequestSchema、matchResponseSchema、statusResponseSchema
// 以 `satisfies z.ZodType<產生的型別>` 在編譯期對齊，引擎改了形狀而 web 沒跟上時 tsc 會失敗。
// errorDetailSchema 刻意不綁：它是引擎錯誤 detail 的超集，另外涵蓋代理自己的 code
// （too_large／validation／engine_unreachable）。
// 其他模組一律只從這裡 import 契約型別與 schema。
import { z } from "zod";
import type { components } from "@/lib/engine/openapi";
import {
  MAX_SEARCH_RADIUS_M,
  MAX_STROKE_POINTS,
  MAX_STROKES,
  MIN_SEARCH_RADIUS_M,
  MIN_STROKE_POINTS,
} from "@/lib/limits";

type Schemas = components["schemas"];

// zod 的錯誤訊息用繁體中文（會顯示給使用者）
z.config(z.locales.zhTW());

/** [經度, 緯度] */
const lonLatSchema = z.tuple([z.number(), z.number()]);

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
  score: z.object({ coverage: percentSchema, cleanliness: percentSchema, overall: percentSchema }),
  target_length_m: z.number(),
  warnings: z.array(matchWarningSchema),
  data_version: z.string(),
}) satisfies z.ZodType<Schemas["MatchResponse"]>;

/**
 * 引擎的領域錯誤（outside_taiwan／too_small／no_route／no_data／timeout）與代理自己的錯誤
 * （validation／engine_unreachable／timeout）共用的 detail 形狀。
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

/** GET /status：data 為 null 代表引擎還沒有可用的路網。 */
export const statusResponseSchema = z.object({
  data: z.object({ build_id: z.string(), osm_md5: z.string(), created_at: z.string() }).nullable(),
}) satisfies z.ZodType<Schemas["StatusResponse"]>;

export type MatchRequest = Schemas["MatchRequest"];
export type MatchResponse = Schemas["MatchResponse"];
export type MatchSegment = Schemas["SegmentOut"];
export type MatchWarning = Schemas["WarningOut"];
export type SegmentKind = Schemas["SegmentOut"]["kind"];
export type StatusResponse = Schemas["StatusResponse"];
