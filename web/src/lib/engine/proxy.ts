import { z } from "zod";
import { matchRequestSchema } from "@/lib/engine/contract";

/** Route Handler 的外部依賴；測試時換成假的。 */
export type ProxyDeps = {
  engineUrl: string | undefined;
  fetch: (input: string, init: RequestInit) => Promise<Response>;
  timeoutMs: number;
};

/** 引擎自己有 5 秒的吸附上限（spec §5）；這裡多留餘裕，只防引擎完全卡住。 */
export const MATCH_TIMEOUT_MS = 10_000;
export const STATUS_TIMEOUT_MS = 3_000;

export function engineDeps(timeoutMs: number): ProxyDeps {
  return { engineUrl: process.env.ENGINE_URL, fetch, timeoutMs };
}

/** 代理自己產生的錯誤，body 與引擎的領域錯誤同形：{ detail: { code, message } }。 */
function proxyError(status: number, code: string, message: string): Response {
  return Response.json({ detail: { code, message } }, { status });
}

/** 轉發到引擎，狀態碼與 body 原樣轉回。沒設定 ENGINE_URL 或連不上回 503、逾時回 504。 */
export async function forwardToEngine(
  path: string,
  init: RequestInit & { signal: AbortSignal },
  deps: ProxyDeps,
): Promise<Response> {
  if (!deps.engineUrl) return proxyError(503, "engine_unreachable", "伺服器沒有設定 ENGINE_URL");
  const timeout = AbortSignal.timeout(deps.timeoutMs);
  try {
    const res = await deps.fetch(`${deps.engineUrl}${path}`, {
      ...init,
      cache: "no-store",
      signal: AbortSignal.any([init.signal, timeout]),
    });
    return new Response(res.body, {
      status: res.status,
      headers: { "content-type": res.headers.get("content-type") ?? "application/json" },
    });
  } catch {
    return timeout.aborted
      ? proxyError(504, "timeout", "引擎回應逾時")
      : proxyError(503, "engine_unreachable", "引擎無法連線");
  }
}

/** POST /api/match：先用 zod 驗證（spec §4.7 的上限），通過才轉給引擎。 */
export async function proxyMatch(request: Request, deps: ProxyDeps): Promise<Response> {
  const body: unknown = await request.json().catch(() => undefined);
  const parsed = matchRequestSchema.safeParse(body);
  if (!parsed.success) {
    return proxyError(422, "validation", z.prettifyError(parsed.error));
  }
  return forwardToEngine(
    "/match",
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(parsed.data),
      signal: request.signal,
    },
    deps,
  );
}

/** GET /api/status → 引擎的 GET /status。 */
export function proxyStatus(request: Request, deps: ProxyDeps): Promise<Response> {
  return forwardToEngine("/status", { method: "GET", signal: request.signal }, deps);
}
