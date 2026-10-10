import { z } from "zod";
import { matchRequestSchema, type MatchRequest } from "@/lib/engine/contract";

/** Route Handler 的外部依賴；測試時換成假的。 */
export type ProxyDeps = {
  engineUrl: string | undefined;
  fetch: (input: string, init: RequestInit) => Promise<Response>;
  timeoutMs: number;
};

/** 引擎自己有 5 秒的吸附上限（spec §5）；這裡多留餘裕，只防引擎完全卡住。 */
export const MATCH_TIMEOUT_MS = 10_000;
export const STATUS_TIMEOUT_MS = 3_000;

/** 合法請求最大約 3.4 MB（40 筆 × 2000 點）；8 MB 之外一律拒絕，不讀進記憶體。 */
const MAX_BODY_BYTES = 8 * 1024 * 1024;
/** 驗證失敗訊息只列前幾個問題並限制長度；問題數量由使用者決定，回應大小不能跟著放大。 */
const MAX_ISSUES_SHOWN = 5;
const MAX_MESSAGE_CHARS = 500;

export function engineDeps(timeoutMs: number): ProxyDeps {
  return { engineUrl: process.env.ENGINE_URL, fetch, timeoutMs };
}

/** 所有代理回應都帶：引擎的 content-type 是原樣轉回的，不讓瀏覽器自己猜內容類型。 */
const SECURITY_HEADERS = { "x-content-type-options": "nosniff" };

/** 代理自己產生的錯誤，body 與引擎的領域錯誤同形：{ detail: { code, message } }。 */
function proxyError(status: number, code: string, message: string): Response {
  return Response.json({ detail: { code, message } }, { status, headers: SECURITY_HEADERS });
}

/** 轉發到引擎，狀態碼與 body 原樣轉回。沒設定 ENGINE_URL 或連不上回 503、逾時（含讀 body 階段）回 504。 */
export async function forwardToEngine(
  path: string,
  init: RequestInit & { signal: AbortSignal },
  deps: ProxyDeps,
): Promise<Response> {
  if (!deps.engineUrl) return proxyError(503, "engine_unreachable", "伺服器沒有設定 ENGINE_URL");
  const timeout = AbortSignal.timeout(deps.timeoutMs);
  let res: Response;
  let bytes: ArrayBuffer;
  try {
    res = await deps.fetch(`${deps.engineUrl.replace(/\/+$/, "")}${path}`, {
      ...init,
      cache: "no-store",
      signal: AbortSignal.any([init.signal, timeout]),
    });
    // 整個 body 讀完才回應：body 階段逾時要變成 504，不能留下一條截斷的串流
    bytes = await res.arrayBuffer();
  } catch (error) {
    // 瀏覽器自己取消的請求（即時吸附每次都會取消上一筆）不是伺服器問題，不記 log
    if (!init.signal.aborted) console.error("[engine proxy]", path, error);
    return timeout.aborted
      ? proxyError(504, "timeout", "引擎回應逾時")
      : proxyError(503, "engine_unreachable", "引擎無法連線");
  }
  return new Response(bytes, {
    status: res.status,
    headers: { ...SECURITY_HEADERS, "content-type": res.headers.get("content-type") ?? "application/json" },
  });
}

class BodyTooLarge extends Error {}

/** 讀請求 body 的文字，超過 MAX_BODY_BYTES 就中止並丟 BodyTooLarge。 */
async function readTextCapped(request: Request): Promise<string> {
  if (Number(request.headers.get("content-length")) > MAX_BODY_BYTES) throw new BodyTooLarge();
  if (!request.body) return "";
  const reader = request.body.getReader();
  const decoder = new TextDecoder();
  let text = "";
  let total = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) return text + decoder.decode();
    total += value.byteLength;
    if (total > MAX_BODY_BYTES) {
      await reader.cancel();
      throw new BodyTooLarge();
    }
    text += decoder.decode(value, { stream: true });
  }
}

function validationMessage(error: z.ZodError): string {
  const shown = new z.ZodError(error.issues.slice(0, MAX_ISSUES_SHOWN));
  return z.prettifyError(shown).slice(0, MAX_MESSAGE_CHARS);
}

type Validation = { ok: true; data: MatchRequest } | { ok: false; message: string };

function validateMatchRequest(body: unknown): Validation {
  try {
    const parsed = matchRequestSchema.safeParse(body);
    return parsed.success ? { ok: true, data: parsed.data } : { ok: false, message: validationMessage(parsed.error) };
  } catch (error) {
    // 問題數量極大時 zod 會因展開參數而 RangeError（Maximum call stack size exceeded）；這也是不合法的請求
    if (!(error instanceof RangeError)) throw error;
    return { ok: false, message: "請求內容不合法" };
  }
}

/** POST /api/match：先限制大小、用 zod 驗證（spec §4.7 的上限），通過才轉給引擎。 */
export async function proxyMatch(request: Request, deps: ProxyDeps): Promise<Response> {
  let body: unknown;
  try {
    body = JSON.parse(await readTextCapped(request));
  } catch (error) {
    if (error instanceof BodyTooLarge) return proxyError(413, "too_large", "請求太大");
    body = undefined;
  }
  const validation = validateMatchRequest(body);
  if (!validation.ok) return proxyError(422, "validation", validation.message);
  return forwardToEngine(
    "/match",
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(validation.data),
      signal: request.signal,
    },
    deps,
  );
}

/** GET /api/status → 引擎的 GET /status。 */
export function proxyStatus(request: Request, deps: ProxyDeps): Promise<Response> {
  return forwardToEngine("/status", { method: "GET", signal: request.signal }, deps);
}
