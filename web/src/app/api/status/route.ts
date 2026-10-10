import { connection } from "next/server";
import { STATUS_TIMEOUT_MS, engineDeps, proxyStatus } from "@/lib/engine/proxy";

export async function GET(request: Request) {
  // cacheComponents 開啟時，GET Route Handler 可能在 build 時被預先產生；connection() 強制每次請求才執行
  await connection();
  return proxyStatus(request, engineDeps(STATUS_TIMEOUT_MS));
}
