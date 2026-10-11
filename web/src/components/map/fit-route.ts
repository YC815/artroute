import type { Map as MapLibreMap } from "maplibre-gl";
import { fitPadding, fitsInView, routeBounds } from "@/lib/map/route-bounds";
import type { LonLat } from "@/lib/shape/types";

/** 打開草稿時最多放大到這裡（很短的路線不要拉到看不出是哪裡）。 */
const MAX_FIT_ZOOM = 17;
/** 路線離畫面邊緣至少這麼多像素才算「看得到」：只要看得到就不動鏡頭（工具列蓋住一小塊沒關係，使用者看得見的東西不要亂動）。 */
const VISIBLE_MARGIN_PX = 16;
const VISIBLE_PADDING = { top: VISIBLE_MARGIN_PX, right: VISIBLE_MARGIN_PX, bottom: VISIBLE_MARGIN_PX, left: VISIBLE_MARGIN_PX };

/**
 * 把地圖移到看得到整條路線：路線已經完整在畫面裡就不動鏡頭，否則 fitBounds（留白給下方的工具列）。草稿可能在別的城市，鎖住的圖形又沒有「移到畫面中央」可用。
 */
export function fitMapToRoute(map: MapLibreMap, segments: readonly { points: readonly LonLat[] }[]): void {
  const bounds = routeBounds(segments);
  if (!bounds) return;
  const { clientWidth: width, clientHeight: height } = map.getContainer();
  const sw = map.project(bounds[0]);
  const ne = map.project(bounds[1]);
  if (fitsInView(sw, ne, width, height, VISIBLE_PADDING)) return;
  map.fitBounds(bounds, { padding: fitPadding(width, height), maxZoom: MAX_FIT_ZOOM, duration: 400 });
}
