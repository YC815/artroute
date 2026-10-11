import { EDIT_TIMEOUT_MS, engineDeps, proxyEdit } from "@/lib/engine/proxy";

export function POST(request: Request) {
  return proxyEdit(request, engineDeps(EDIT_TIMEOUT_MS));
}
