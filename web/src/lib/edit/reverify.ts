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

/** 重新驗證的畫面狀態；sig 是它對應的那條路線（簽章）：路線換了（打開別的草稿、通過後換成新版）就不再適用。 */
export type ReverifyState = {
  sig: string | null;
  status: "idle" | "verifying";
  /** 失敗或錯誤的說明；沒有就是 null。 */
  message: string | null;
  /** verify_failed 的有問題段落序號（地圖上標紅）。 */
  bad: number[];
};

export const IDLE_REVERIFY: ReverifyState = { sig: null, status: "idle", message: null, bad: [] };

export function verifyingState(sig: string): ReverifyState {
  return { sig, status: "verifying", message: null, bad: [] };
}

/** 結果 → 狀態：通過就回到 idle（路線已經換成新版）；沒過保留說明與有問題的段；其他錯誤只保留說明，可以再試。 */
export function stateAfterReverify(sig: string, result: ReverifyResult): ReverifyState {
  switch (result.kind) {
    case "ok":
      return IDLE_REVERIFY;
    case "failed":
      return { sig, status: "idle", message: result.message, bad: result.segments };
    case "error":
      return { sig, status: "idle", message: result.message, bad: [] };
  }
}

/** 只有對應目前這條路線的狀態才顯示。 */
export function reverifyFor(state: ReverifyState, currentSig: string | null): ReverifyState {
  return currentSig !== null && state.sig === currentSig ? state : IDLE_REVERIFY;
}
