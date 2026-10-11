import { SEARCH_TIMEOUT_MS, engineDeps, proxySearch } from "@/lib/engine/proxy";

// 回應是 SSE 串流（spec §4.6）：proxySearch 邊收邊轉，不在這裡緩衝
export function POST(request: Request) {
  return proxySearch(request, engineDeps(SEARCH_TIMEOUT_MS));
}
