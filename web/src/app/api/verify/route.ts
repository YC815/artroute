import { VERIFY_TIMEOUT_MS, engineDeps, proxyVerify } from "@/lib/engine/proxy";

export function POST(request: Request) {
  return proxyVerify(request, engineDeps(VERIFY_TIMEOUT_MS));
}
