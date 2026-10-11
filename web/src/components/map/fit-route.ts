import type { Map as MapLibreMap } from "maplibre-gl";
import { fitPadding, fitsInView, routeBounds } from "@/lib/map/route-bounds";
import type { LonLat } from "@/lib/shape/types";

/** 打開草稿時最多放大到這裡（很短的路線不要拉到看不出是哪裡）。 */
const MAX_FIT_ZOOM = 17;

/**
 * 把地圖移到看得到整條路線：路線已經完整在畫面裡（扣掉下方工具列的留白）就不動鏡頭，
 * 否則 fitBounds。草稿可能在別的城市，鎖住的圖形又沒有「移到畫面中央」可用。
 */
export function fitMapToRoute(map: MapLibreMap, segments: readonly { points: readonly LonLat[] }[]): void {
  const bounds = routeBounds(segments);
  if (!bounds) return;
  const { clientWidth: width, clientHeight: height } = map.getContainer();
  const padding = fitPadding(width, height);
  const sw = map.project(bounds[0]);
  const ne = map.project(bounds[1]);
  if (fitsInView(sw, ne, width, height, padding)) return;
  map.fitBounds(bounds, { padding, maxZoom: MAX_FIT_ZOOM, duration: 400 });
}
