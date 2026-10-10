import { MATCH_TIMEOUT_MS, engineDeps, proxyMatch } from "@/lib/engine/proxy";

export function POST(request: Request) {
  return proxyMatch(request, engineDeps(MATCH_TIMEOUT_MS));
}
