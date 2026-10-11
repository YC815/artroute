import type { z } from "zod";
import { readErrorBody, type FetchLike } from "@/lib/engine/client";
import {
  searchBestEventSchema,
  searchFailedEventSchema,
  searchProgressEventSchema,
  searchQueuedEventSchema,
  searchResultEventSchema,
  type SearchBest,
  type SearchEvent,
  type SearchProgress,
  type SearchQueued,
  type SearchRequest,
  type SearchResult,
} from "@/lib/engine/contract";
import { createSseParser, type SseMessage } from "@/lib/search/sse";

/**
 * 自動搜尋失敗的原因。
 * status：HTTP 狀態碼；0 代表沒有拿到回應（連不上）或串流中途斷掉；200 代表串流裡收到引擎的 error 事件。
 * code：引擎或代理的錯誤碼（timeout、busy、no_search_data、validation…）；串流中斷是 incomplete；沒有時是 null。
 * engineDown：連不上或引擎沒有路網（503，no_search_data 除外），呼叫端停用吸附並顯示橫幅。
 */
export type SearchError = { status: number; code: string | null; message: string; engineDown: boolean };

/** 搜尋的最後結果。aborted 是呼叫端自己取消的（不是錯誤，不必顯示）。 */
export type SearchOutcome =
  | { kind: "result"; result: SearchResult }
  | { kind: "failed"; error: SearchError }
  | { kind: "aborted" };

/** 搜尋途中的事件（不是最後一個）。 */
export type SearchUpdate = SearchQueued | SearchProgress | SearchBest;

const DEFAULT_MESSAGES: Record<number, string> = {
  0: "連不上伺服器",
  400: "搜尋範圍的圓心不在台灣範圍內（含金門、馬祖）",
  413: "請求太大",
  422: "輸入不合法，或圖形太小",
  502: "引擎回應的格式不符合契約",
  503: "引擎暫時無法服務（可能正在更新路網）",
  504: "搜尋超過時間上限，請縮小範圍後重試",
};

const INCOMPLETE: SearchOutcome = {
  kind: "failed",
  error: { status: 0, code: "incomplete", message: "搜尋的連線中斷了，沒有收到結果，請重試", engineDown: false },
};
const INVALID: SearchOutcome = {
  kind: "failed",
  error: { status: 502, code: null, message: DEFAULT_MESSAGES[502], engineDown: false },
};
const ABORTED: SearchOutcome = { kind: "aborted" };

/** 狀態碼＋錯誤 body → SearchError。no_search_data 只代表這份路網不能搜尋，吸附照常，不算引擎離線。 */
export function toSearchError(status: number, body: unknown): SearchError {
  const { code, message } = readErrorBody(body);
  return {
    status,
    code: code ?? null,
    message: message || (DEFAULT_MESSAGES[status] ?? `搜尋失敗（HTTP ${status}）`),
    engineDown: status === 0 || (status === 503 && code !== "no_search_data"),
  };
}

// 用 Map 而不是物件：SSE 的事件名稱是任意字串，物件會撞到 "constructor" 之類的原型屬性
const EVENT_SCHEMAS = new Map<string, z.ZodType<SearchEvent>>([
  ["queued", searchQueuedEventSchema],
  ["progress", searchProgressEventSchema],
  ["best", searchBestEventSchema],
  ["result", searchResultEventSchema],
  ["error", searchFailedEventSchema],
]);

/** 已知事件 → 事件；不認得的事件名稱 → null（向前相容：引擎新增的事件略過）；已知事件格式不符 → "invalid"。 */
function readEvent(message: SseMessage): SearchEvent | null | "invalid" {
  const schema = EVENT_SCHEMAS.get(message.event);
  if (!schema) return null;
  let data: unknown;
  try {
    data = JSON.parse(message.data);
  } catch {
    return "invalid";
  }
  // 各事件 schema 的 type 是字面值：事件名稱和 data.type 不一致也算格式不符
  const parsed = schema.safeParse(data);
  return parsed.success ? parsed.data : "invalid";
}

/**
 * POST /api/search，讀 SSE 直到最後一個事件（result 或 error）。不會丟例外。
 * 途中的 queued／progress／best 依序交給 onUpdate；收到最後一個事件就停止讀取並取消串流。
 * 已知事件格式不符 → 502；串流結束（或斷線）卻沒有最後一個事件 → incomplete；signal 取消 → aborted。
 */
export async function fetchSearch(
  request: SearchRequest,
  signal: AbortSignal,
  onUpdate: (update: SearchUpdate) => void,
  fetchImpl: FetchLike = fetch,
): Promise<SearchOutcome> {
  let res: Response;
  try {
    res = await fetchImpl("/api/search", {
      method: "POST",
      headers: { "content-type": "application/json", accept: "text/event-stream" },
      body: JSON.stringify(request),
      signal,
    });
  } catch {
    return signal.aborted ? ABORTED : { kind: "failed", error: toSearchError(0, null) };
  }
  if (!res.ok) {
    const body: unknown = await res.json().catch(() => null);
    return signal.aborted ? ABORTED : { kind: "failed", error: toSearchError(res.status, body) };
  }
  if (!(res.headers.get("content-type") ?? "").startsWith("text/event-stream") || !res.body) {
    await res.body?.cancel().catch(() => {});
    return INVALID;
  }

  let final: SearchOutcome | null = null;
  const parser = createSseParser((message) => {
    if (final || signal.aborted) return;
    const event = readEvent(message);
    if (event === null) return;
    if (event === "invalid") final = INVALID;
    else if (event.type === "result") final = { kind: "result", result: event };
    else if (event.type === "error") {
      final = { kind: "failed", error: { status: 200, code: event.code, message: event.message, engineDown: false } };
    } else onUpdate(event);
  });
  const reader = res.body.getReader();
  try {
    while (!final) {
      const { done, value } = await reader.read();
      if (done) {
        parser.end();
        break;
      }
      parser.push(value);
    }
  } catch {
    return signal.aborted ? ABORTED : INCOMPLETE;
  }
  if (signal.aborted) return ABORTED;
  if (!final) return INCOMPLETE;
  // 不再讀了：取消串流，連線不必等引擎關閉
  void reader.cancel().catch(() => {});
  return final;
}
