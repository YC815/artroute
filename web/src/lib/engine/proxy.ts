import { z } from "zod";
import { editRequestSchema, matchRequestSchema, searchRequestSchema, verifyRequestSchema } from "@/lib/engine/contract";

/** Route Handler 的外部依賴；測試時換成假的。 */
export type ProxyDeps = {
  engineUrl: string | undefined;
  fetch: (input: string, init: RequestInit) => Promise<Response>;
  timeoutMs: number;
};

/** 引擎自己有 5 秒的吸附上限（spec §5）；這裡多留餘裕，只防引擎完全卡住。 */
export const MATCH_TIMEOUT_MS = 10_000;
export const STATUS_TIMEOUT_MS = 3_000;
/** /edit 與 /match 同；/verify 要對每段做 trace_attributes，引擎的上限是 15 秒（微調 spec §3.5），這裡多留 1 秒。 */
export const EDIT_TIMEOUT_MS = MATCH_TIMEOUT_MS;
export const VERIFY_TIMEOUT_MS = 16_000;
/**
 * 自動搜尋：引擎排隊最多 120 秒、搜尋本身 60 秒硬上限（spec §5），再多留餘裕。
 * 收到標頭前逾時回 504；串流中途逾時就結束串流（前端把沒有結果事件的串流當成中斷）。
 */
export const SEARCH_TIMEOUT_MS = 200_000;

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

function engineUrl(deps: ProxyDeps, path: string): string {
  return `${(deps.engineUrl ?? "").replace(/\/+$/, "")}${path}`;
}

/** 呼叫引擎或讀 body 時丟出例外：逾時回 504，其餘（連不上）回 503。 */
function engineFailure(path: string, clientSignal: AbortSignal, timeout: AbortSignal, error: unknown): Response {
  // 瀏覽器自己取消的請求（即時吸附每次都會取消上一筆）不是伺服器問題，不記 log
  if (!clientSignal.aborted) console.error("[engine proxy]", path, error);
  return timeout.aborted
    ? proxyError(504, "timeout", "引擎回應逾時")
    : proxyError(503, "engine_unreachable", "引擎無法連線");
}

type Upstream = { ok: true; res: Response; timeout: AbortSignal } | { ok: false; response: Response };

/** 送出請求、等到引擎的標頭。瀏覽器斷線（init.signal）或逾時都會取消對引擎的請求。 */
async function openEngine(path: string, init: RequestInit & { signal: AbortSignal }, deps: ProxyDeps): Promise<Upstream> {
  if (!deps.engineUrl) return { ok: false, response: proxyError(503, "engine_unreachable", "伺服器沒有設定 ENGINE_URL") };
  const timeout = AbortSignal.timeout(deps.timeoutMs);
  try {
    const res = await deps.fetch(engineUrl(deps, path), {
      ...init,
      cache: "no-store",
      signal: AbortSignal.any([init.signal, timeout]),
    });
    return { ok: true, res, timeout };
  } catch (error) {
    return { ok: false, response: engineFailure(path, init.signal, timeout, error) };
  }
}

/** 整個 body 讀完才回應：body 階段逾時要變成 504，不能留下一條截斷的串流。 */
async function bufferedResponse(
  path: string,
  signal: AbortSignal,
  upstream: Extract<Upstream, { ok: true }>,
): Promise<Response> {
  const { res, timeout } = upstream;
  let bytes: ArrayBuffer;
  try {
    bytes = await res.arrayBuffer();
  } catch (error) {
    return engineFailure(path, signal, timeout, error);
  }
  return new Response(bytes, {
    status: res.status,
    headers: { ...SECURITY_HEADERS, "content-type": res.headers.get("content-type") ?? "application/json" },
  });
}

/** 轉發到引擎，狀態碼與 body 原樣轉回。沒設定 ENGINE_URL 或連不上回 503、逾時（含讀 body 階段）回 504。 */
export async function forwardToEngine(
  path: string,
  init: RequestInit & { signal: AbortSignal },
  deps: ProxyDeps,
): Promise<Response> {
  const upstream = await openEngine(path, init, deps);
  return upstream.ok ? bufferedResponse(path, init.signal, upstream) : upstream.response;
}

/** SSE 回應的標頭：不快取、不壓縮也不緩衝（no-transform 讓 Next 的壓縮略過，x-accel-buffering 給 nginx 類的反向代理）。 */
const SSE_HEADERS = {
  ...SECURITY_HEADERS,
  "content-type": "text/event-stream; charset=utf-8",
  "cache-control": "no-cache, no-transform",
  "x-accel-buffering": "no",
};

/**
 * 引擎的 SSE body 逐塊轉給瀏覽器，不緩衝。
 * 引擎串流中途出錯（逾時、連線斷掉）時正常結束串流而不是讓它出錯：前端收到「沒有結果事件就結束」的串流，當成中斷處理。
 * 瀏覽器取消回應串流時，一併取消引擎的串流（引擎偵測到斷線就停止搜尋）。
 */
function relay(path: string, body: ReadableStream<Uint8Array>, clientSignal: AbortSignal): ReadableStream<Uint8Array> {
  const reader = body.getReader();
  let cancelled = false;
  return new ReadableStream<Uint8Array>({
    async pull(controller) {
      let chunk: ReadableStreamReadResult<Uint8Array>;
      try {
        chunk = await reader.read();
      } catch (error) {
        if (!clientSignal.aborted) console.error("[engine proxy]", path, error);
        chunk = { done: true, value: undefined };
      }
      // 等待引擎的這段時間裡瀏覽器取消了：串流已經關閉，不能再 close／enqueue
      if (cancelled) return;
      if (chunk.done) controller.close();
      else controller.enqueue(chunk.value);
    },
    cancel(reason) {
      cancelled = true;
      return reader.cancel(reason);
    },
  });
}

/** 轉發到引擎；引擎回 200 的 SSE 就邊收邊轉，其餘（錯誤狀態碼的 JSON 等）照 forwardToEngine 讀完再轉。 */
async function streamFromEngine(
  path: string,
  init: RequestInit & { signal: AbortSignal },
  deps: ProxyDeps,
): Promise<Response> {
  const upstream = await openEngine(path, init, deps);
  if (!upstream.ok) return upstream.response;
  const { res } = upstream;
  const isStream = res.status === 200 && (res.headers.get("content-type") ?? "").startsWith("text/event-stream");
  if (!isStream || !res.body) return bufferedResponse(path, init.signal, upstream);
  return new Response(relay(path, res.body, init.signal), { status: 200, headers: SSE_HEADERS });
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

type Validation<T> = { ok: true; data: T } | { ok: false; message: string };

function validate<T>(schema: z.ZodType<T>, body: unknown): Validation<T> {
  try {
    const parsed = schema.safeParse(body);
    return parsed.success ? { ok: true, data: parsed.data } : { ok: false, message: validationMessage(parsed.error) };
  } catch (error) {
    // 問題數量極大時 zod 會因展開參數而 RangeError（Maximum call stack size exceeded）；這也是不合法的請求
    if (!(error instanceof RangeError)) throw error;
    return { ok: false, message: "請求內容不合法" };
  }
}

/** 讀請求 body（限制大小）並用 schema 驗證；不通過時回傳代理自己的 413／422 回應。 */
async function readRequest<T>(request: Request, schema: z.ZodType<T>): Promise<{ data: T } | { response: Response }> {
  let body: unknown;
  try {
    body = JSON.parse(await readTextCapped(request));
  } catch (error) {
    if (error instanceof BodyTooLarge) return { response: proxyError(413, "too_large", "請求太大") };
    body = undefined;
  }
  const validation = validate(schema, body);
  return validation.ok ? { data: validation.data } : { response: proxyError(422, "validation", validation.message) };
}

function jsonPost(data: unknown, signal: AbortSignal): RequestInit & { signal: AbortSignal } {
  return { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(data), signal };
}

/** POST /api/match：先限制大小、用 zod 驗證（spec §4.7 的上限），通過才轉給引擎。 */
export async function proxyMatch(request: Request, deps: ProxyDeps): Promise<Response> {
  const read = await readRequest(request, matchRequestSchema);
  if ("response" in read) return read.response;
  return forwardToEngine("/match", jsonPost(read.data, request.signal), deps);
}

/**
 * POST /api/edit：驗證方式同 /api/match。引擎的 409（stale_data）、422（invalid_route、not_on_route、nothing_to_cut、
 * turn_not_rideable、no_road…）、503、504 狀態碼與 body 原樣轉回，網站靠 detail.code 分辨。
 */
export async function proxyEdit(request: Request, deps: ProxyDeps): Promise<Response> {
  const read = await readRequest(request, editRequestSchema);
  if ("response" in read) return read.response;
  return forwardToEngine("/edit", jsonPost(read.data, request.signal), deps);
}

/** POST /api/verify：同 /api/edit；verify_failed 的 422 帶有問題的段落序號（detail.segments），原樣轉回。 */
export async function proxyVerify(request: Request, deps: ProxyDeps): Promise<Response> {
  const read = await readRequest(request, verifyRequestSchema);
  if ("response" in read) return read.response;
  return forwardToEngine("/verify", jsonPost(read.data, request.signal), deps);
}

/**
 * POST /api/search：驗證方式同 /api/match；引擎回 200 的 SSE 時邊收邊轉（spec §4.6），錯誤照原樣轉回。
 * 瀏覽器斷線時取消對引擎的請求（排隊中的立刻離開、搜尋中的在下一次檢查時停下）。
 */
export async function proxySearch(request: Request, deps: ProxyDeps): Promise<Response> {
  const read = await readRequest(request, searchRequestSchema);
  if ("response" in read) return read.response;
  return streamFromEngine("/search", jsonPost(read.data, request.signal), deps);
}

/** GET /api/status → 引擎的 GET /status。 */
export function proxyStatus(request: Request, deps: ProxyDeps): Promise<Response> {
  return forwardToEngine("/status", { method: "GET", signal: request.signal }, deps);
}
