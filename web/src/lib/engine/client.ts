import {
  errorBodySchema,
  errorDetailSchema,
  matchResponseSchema,
  statusResponseSchema,
  type EditRequest,
  type MatchRequest,
  type MatchResponse,
  type MatchWarning,
  type VerifyRequest,
} from "@/lib/engine/contract";

/**
 * 給 UI 用的錯誤。status 0 代表沒有拿到 HTTP 回應：連不上（engineDown 為 true），
 * 或被呼叫端用 AbortSignal 取消（engineDown 為 false，message 為「已取消」）。
 * engineDown 為 true 時停用即時吸附並顯示橫幅。
 */
export type MatchError = { status: number; message: string; warnings: MatchWarning[]; engineDown: boolean };

export type MatchOutcome = { ok: true; response: MatchResponse } | { ok: false; error: MatchError };

export type FetchLike = (input: string, init?: RequestInit) => Promise<Response>;

const DEFAULT_MESSAGES: Record<number, string> = {
  0: "連不上伺服器",
  400: "圖形不在台灣範圍內（含金門、馬祖）",
  422: "輸入不合法，或所有部件都吸附失敗",
  502: "引擎回應的格式不符合契約",
  503: "引擎暫時無法服務（可能正在更新路網）",
  504: "吸附超過時間上限，請縮小圖形或減少筆畫",
};

type ReadDetail = { code: string | undefined; message: string | undefined; warnings: MatchWarning[]; segments: number[] };

/**
 * 錯誤 body 的 detail 有三種形狀：領域錯誤物件 → 取 code、message、warnings 與 segments；字串 → 直接當訊息；
 * 其他（pydantic 陣列、body 根本不是物件等）→ 沒有訊息。/match、/edit、/verify 與 /search 共用。
 */
export function readErrorBody(body: unknown): ReadDetail {
  const parsed = errorBodySchema.safeParse(body);
  const detail = parsed.success ? parsed.data.detail : undefined;
  const structured = errorDetailSchema.safeParse(detail);
  if (structured.success) {
    const { code, message, warnings, segments } = structured.data;
    return { code, message, warnings: warnings ?? [], segments: segments ?? [] };
  }
  if (typeof detail === "string") return { code: undefined, message: detail, warnings: [], segments: [] };
  return { code: undefined, message: undefined, warnings: [], segments: [] };
}

/** 狀態碼＋錯誤 body → MatchError。detail 沒有可用的訊息時用依狀態碼的預設訊息。 */
export function toMatchError(status: number, body: unknown): MatchError {
  const { message, warnings } = readErrorBody(body);
  return {
    status,
    message: message || (DEFAULT_MESSAGES[status] ?? `吸附失敗（HTTP ${status}）`),
    warnings,
    engineDown: status === 0 || status === 503,
  };
}

/** 呼叫端自己取消的請求：不是引擎的問題，不能讓 UI 顯示離線橫幅。 */
const CANCELLED_MESSAGE = "已取消";

function cancelled(): MatchOutcome {
  return { ok: false, error: { status: 0, message: CANCELLED_MESSAGE, warnings: [], engineDown: false } };
}

/** POST 之後的三種結果。cancelled 與 network 分開：只有 network 代表連不上。 */
type Posted = { kind: "cancelled" } | { kind: "network" } | { kind: "http"; status: number; ok: boolean; body: unknown };

/**
 * POST JSON 並讀回 body。signal 被取消時（不論發生在等回應或讀 body）一律回 cancelled，
 * 不會被當成引擎離線或格式錯誤。
 */
async function postJson(path: string, request: unknown, signal: AbortSignal, fetchImpl: FetchLike): Promise<Posted> {
  let res: Response;
  try {
    res = await fetchImpl(path, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(request),
      signal,
    });
  } catch {
    return signal.aborted ? { kind: "cancelled" } : { kind: "network" };
  }
  const body: unknown = await res.json().catch(() => null);
  if (signal.aborted) return { kind: "cancelled" };
  return { kind: "http", status: res.status, ok: res.ok, body };
}

/**
 * POST /api/match。不會丟例外：網路錯誤、HTTP 錯誤、格式不符都變成 { ok: false }。
 * signal 被取消時（不論發生在等回應或讀 body）一律回傳「已取消」，不會被當成引擎離線或格式錯誤。
 */
export async function fetchMatch(
  request: MatchRequest,
  signal: AbortSignal,
  fetchImpl: FetchLike = fetch,
): Promise<MatchOutcome> {
  const posted = await postJson("/api/match", request, signal, fetchImpl);
  if (posted.kind === "cancelled") return cancelled();
  if (posted.kind === "network") return { ok: false, error: toMatchError(0, null) };
  if (!posted.ok) return { ok: false, error: toMatchError(posted.status, posted.body) };
  const parsed = matchResponseSchema.safeParse(posted.body);
  return parsed.success ? { ok: true, response: parsed.data } : { ok: false, error: toMatchError(502, null) };
}

/**
 * /edit、/verify 的錯誤。code 是引擎或代理的錯誤碼（stale_data、invalid_route、nothing_to_cut…），
 * 沒有結構化 detail 時為 undefined；segments 是 verify_failed 列出的有問題的段落序號（沒有就是空陣列）。
 * status 0 的意義同 MatchError。
 */
export type EditError = {
  status: number;
  code: string | undefined;
  message: string;
  segments: number[];
  engineDown: boolean;
};

export type EditOutcome = { ok: true; response: MatchResponse } | { ok: false; error: EditError };

const EDIT_DEFAULT_MESSAGES: Record<number, string> = {
  0: "連不上伺服器",
  409: "路網已更新，請先重新驗證這條路線",
  422: "這個操作做不到，或路線資料不合法",
  502: "引擎回應的格式不符合契約",
  503: "引擎暫時無法服務（可能正在更新路網）",
  504: "微調超過時間上限",
};

/** 狀態碼＋錯誤 body → EditError。detail 沒有可用的訊息時用依狀態碼的預設訊息。 */
export function toEditError(status: number, body: unknown): EditError {
  const { code, message, segments } = readErrorBody(body);
  return {
    status,
    code,
    message: message || (EDIT_DEFAULT_MESSAGES[status] ?? `微調失敗（HTTP ${status}）`),
    segments,
    engineDown: status === 0 || status === 503,
  };
}

async function postEditLike(
  path: "/api/edit" | "/api/verify",
  request: EditRequest | VerifyRequest,
  signal: AbortSignal,
  fetchImpl: FetchLike,
): Promise<EditOutcome> {
  const posted = await postJson(path, request, signal, fetchImpl);
  if (posted.kind === "cancelled") {
    return { ok: false, error: { status: 0, code: undefined, message: CANCELLED_MESSAGE, segments: [], engineDown: false } };
  }
  if (posted.kind === "network") return { ok: false, error: toEditError(0, null) };
  if (!posted.ok) return { ok: false, error: toEditError(posted.status, posted.body) };
  const parsed = matchResponseSchema.safeParse(posted.body);
  return parsed.success ? { ok: true, response: parsed.data } : { ok: false, error: toEditError(502, null) };
}

/** POST /api/edit：對整條路線做一個微調操作。不會丟例外；取消的語意同 fetchMatch。 */
export function postEdit(request: EditRequest, signal: AbortSignal, fetchImpl: FetchLike = fetch): Promise<EditOutcome> {
  return postEditLike("/api/edit", request, signal, fetchImpl);
}

/** POST /api/verify：舊路網的草稿在新路網上重新檢查；通過回新版本的路線，沒過是 verify_failed 並列出有問題的段落。 */
export function postVerify(
  request: VerifyRequest,
  signal: AbortSignal,
  fetchImpl: FetchLike = fetch,
): Promise<EditOutcome> {
  return postEditLike("/api/verify", request, signal, fetchImpl);
}

/** 引擎可用時的狀態：路網的 build id，以及這份路網能不能自動搜尋、能不能用描形規劃器（quality: "fine"）。 */
export type EngineInfo = { dataVersion: string; searchReady: boolean; plannerReady: boolean };

/** GET /api/status → 目前路網的資訊；引擎連不上或沒有路網時回傳 null。 */
export async function fetchStatus(fetchImpl: FetchLike = fetch): Promise<EngineInfo | null> {
  try {
    const res = await fetchImpl("/api/status", { cache: "no-store" });
    const parsed = statusResponseSchema.safeParse(await res.json());
    if (!res.ok || !parsed.success || !parsed.data.data) return null;
    const { build_id: dataVersion } = parsed.data.data;
    return { dataVersion, searchReady: parsed.data.search_ready, plannerReady: parsed.data.planner_ready };
  } catch {
    return null;
  }
}
