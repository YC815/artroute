import type { MatchResponse } from "@/lib/engine/contract";

export type RouteVariant = "fast" | "fine";

export const DEFAULT_ROUTE_VARIANT: RouteVariant = "fast";
export const ROUTE_VARIANT_STORAGE_KEY = "artroute:route-variant";

type KeyValueStore = Pick<Storage, "getItem" | "setItem">;

/** 讀取偏好的路線版本；壞值或 localStorage 不可用時採用快速版。 */
export function loadRouteVariant(store: KeyValueStore): RouteVariant {
  try {
    const value = store.getItem(ROUTE_VARIANT_STORAGE_KEY);
    return value === "fast" || value === "fine" ? value : DEFAULT_ROUTE_VARIANT;
  } catch {
    return DEFAULT_ROUTE_VARIANT;
  }
}

/** 儲存偏好的路線版本；localStorage 不可用時不影響切換。 */
export function saveRouteVariant(store: KeyValueStore, variant: RouteVariant): void {
  try {
    store.setItem(ROUTE_VARIANT_STORAGE_KEY, variant);
  } catch {
    // 不存
  }
}

/** 描形版還沒成功或不屬於目前擺放時，偏好描形版也先顯示快速版。 */
export function displayedRoute(
  preference: RouteVariant,
  fast: MatchResponse | null,
  fine: MatchResponse | null,
): { variant: RouteVariant; response: MatchResponse | null } {
  return preference === "fine" && fine ? { variant: "fine", response: fine } : { variant: "fast", response: fast };
}
