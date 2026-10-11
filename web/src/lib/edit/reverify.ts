import { routeFromResponse } from "@/lib/edit/edit-bridge";
import type { EditedRoute } from "@/lib/edit/edit-state";
import { postVerify, type EditOutcome } from "@/lib/engine/client";
import type { VerifyRequest } from "@/lib/engine/contract";

/**
 * ok：新版路網上的路線（已重新簽章）；failed：有問題的段落序號（0 起算，對應目前路線的 segments）；
 * error：其他原因（逾時、503、簽章不合…），路線維持原狀，可以再試。
 */
export type ReverifyResult =
  | { kind: "ok"; route: EditedRoute }
  | { kind: "failed"; segments: number[]; message: string }
  | { kind: "error"; message: string };

type Post = (request: VerifyRequest, signal: AbortSignal) => Promise<EditOutcome>;

/** 舊路網的路線在目前路網上重新檢查（POST /verify，spec §3.6）。被取消時回 null：呼叫端什麼都不顯示。 */
export async function reverifyRoute(
  route: EditedRoute,
  strokes: VerifyRequest["strokes"],
  signal: AbortSignal,
  post: Post = postVerify,
): Promise<ReverifyResult | null> {
  const outcome = await post(
    { segments: route.segments, data_version: route.dataVersion, route_sig: route.routeSig, strokes },
    signal,
  );
  if (signal.aborted) return null;
  if (outcome.ok) return { kind: "ok", route: routeFromResponse(outcome.response) };
  const { code, message, segments } = outcome.error;
  return code === "verify_failed" ? { kind: "failed", segments, message } : { kind: "error", message };
}
