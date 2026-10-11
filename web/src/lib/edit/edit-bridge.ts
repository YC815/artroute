import type { EditOutcome as ClientEditOutcome } from "@/lib/engine/client";
import type { EditRequest, MatchResponse } from "@/lib/engine/contract";
import type { EditedRoute, EditOp } from "@/lib/edit/edit-state";
import type { EditOutcome } from "@/hooks/use-route-edit";
import type { LonLat } from "@/lib/shape/types";

/** 409 stale_data 一律用這句：引擎的說明是給 API 使用者看的，這裡要導向「重新驗證」。 */
export const STALE_DATA_MESSAGE = "路網已更新，這條路線需要重新驗證";

/** /match、/edit、/verify 的 200 回應 → 網站留著的部分（草稿存的同一個形狀）。 */
export function routeFromResponse(response: MatchResponse): EditedRoute {
  return {
    segments: response.segments,
    dataVersion: response.data_version,
    routeSig: response.route_sig,
    score: response.score,
    lengthM: response.route.length_m,
  };
}

/**
 * 把段落攤平成一條折線，與引擎 `compose.route_points` 同一個規則：後一段的第一點是前一段的終點，只算一次；
 * 連續重複的點只留一個。GPX 與資訊面板用這個，所以微調後下載的路線和引擎回應的 route.points 一致。
 */
export function flattenSegments(segments: readonly { points: readonly LonLat[] }[]): LonLat[] {
  const points: LonLat[] = [];
  segments.forEach(({ points: own }, i) => {
    for (const p of i === 0 ? own : own.slice(1)) {
      const last = points.at(-1);
      if (!last || last[0] !== p[0] || last[1] !== p[1]) points.push(p);
    }
  });
  return points;
}

/** 微調後的路線 → 資訊面板與 GPX 用的 MatchResponse 形狀（沒有警告：引擎不回半成品）。 */
export function responseFromRoute(route: EditedRoute, targetLengthM: number | null): MatchResponse {
  return {
    route: { points: flattenSegments(route.segments), length_m: route.lengthM },
    segments: route.segments,
    score: route.score,
    target_length_m: targetLengthM ?? 0,
    warnings: [],
    data_version: route.dataVersion,
    route_sig: route.routeSig,
  };
}

/** 目前的路線（簽章原封不動）＋操作＋使用者放的原圖 → /edit 請求。 */
export function toEditRequest(route: EditedRoute, op: EditOp, strokes: EditRequest["strokes"]): EditRequest {
  return {
    segments: route.segments,
    data_version: route.dataVersion,
    route_sig: route.routeSig,
    strokes,
    op,
  };
}

/**
 * client 的結果 → 微調流程的結果。沒有引擎錯誤碼時（連不上、502…）給一個依狀態的碼，
 * 讓介面分得出「引擎拒絕這個操作」與「引擎有問題」；stale_data 的說明統一成 STALE_DATA_MESSAGE。
 */
export function toEditOutcome(outcome: ClientEditOutcome): EditOutcome {
  if (outcome.ok) return { ok: true, route: routeFromResponse(outcome.response) };
  const { code, message, status, engineDown } = outcome.error;
  return {
    ok: false,
    code: code ?? (engineDown ? "engine_down" : `http_${status}`),
    message: code === "stale_data" ? STALE_DATA_MESSAGE : message,
  };
}
