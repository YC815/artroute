import { afterEach, describe, expect, it, vi } from "vitest";
import { POST as postEdit } from "@/app/api/edit/route";
import { POST as postVerify } from "@/app/api/verify/route";
import { SAMPLE_EDIT_REQUEST, SAMPLE_VERIFY_REQUEST } from "@/lib/engine/fixtures";
import { EDIT_TIMEOUT_MS, VERIFY_TIMEOUT_MS } from "@/lib/engine/proxy";

// Route Handler 只負責選逾時與路徑：用假 fetch 看它真的打到引擎的哪個路徑、等多久。
function request(path: string, body: unknown): Request {
  return new Request(`http://web/api${path}`, { method: "POST", body: JSON.stringify(body) });
}

afterEach(() => {
  vi.unstubAllEnvs();
  vi.restoreAllMocks();
});

describe("/api/edit、/api/verify 的 Route Handler", () => {
  it.each([
    ["/edit", postEdit, SAMPLE_EDIT_REQUEST, EDIT_TIMEOUT_MS],
    ["/verify", postVerify, SAMPLE_VERIFY_REQUEST, VERIFY_TIMEOUT_MS],
  ] as const)("%s 轉給引擎同名的路徑，並用各自的逾時", async (path, handler, body, timeoutMs) => {
    vi.stubEnv("ENGINE_URL", "http://engine:8080");
    const timeout = vi.spyOn(AbortSignal, "timeout");
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("{}", { status: 409 }));
    const res = await handler(request(path, body));
    expect(res.status).toBe(409);
    expect(fetchSpy.mock.calls[0][0]).toBe(`http://engine:8080${path}`);
    expect(timeout).toHaveBeenCalledWith(timeoutMs);
  });
});
