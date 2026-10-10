import type { LonLat } from "@/lib/shape/types";

const XML_ESCAPES: Record<string, string> = { "<": "&lt;", ">": "&gt;", "&": "&amp;", "'": "&apos;", '"': "&quot;" };

function escapeXml(text: string): string {
  return text.replace(/[<>&'"]/g, (c) => XML_ESCAPES[c]);
}

/** XML 1.0 不允許的字元。嚴格的解析器遇到會拒絕整個檔案。 */
const XML_ILLEGAL_CHARS = /[\u0000-\u0008\u000B\u000C\u000E-\u001F\uFFFE\uFFFF]/g;

/** 作品名稱（可能來自使用者文字）：去掉非法字元，所有空白收成一個空白並去頭尾（兩行文字會變成一行），清空後用 artroute。 */
function cleanName(name: string): string {
  return name.replace(XML_ILLEGAL_CHARS, "").replace(/\s+/g, " ").trim() || "artroute";
}

/** 路線 → GPX 1.1（一個 trk、一個 trkseg）。座標取到小數第 6 位（約 0.1 m）。 */
export function buildGpx(points: LonLat[], name: string): string {
  const title = escapeXml(cleanName(name));
  return [
    '<?xml version="1.0" encoding="UTF-8"?>',
    '<gpx version="1.1" creator="artroute" xmlns="http://www.topografix.com/GPX/1/1">',
    `  <metadata><name>${title}</name></metadata>`,
    "  <trk>",
    `    <name>${title}</name>`,
    "    <trkseg>",
    ...points.map(([lon, lat]) => `      <trkpt lat="${lat.toFixed(6)}" lon="${lon.toFixed(6)}"/>`),
    "    </trkseg>",
    "  </trk>",
    "</gpx>",
    "",
  ].join("\n");
}

/** 下載檔名：英數字以外的字元換成 -，全部去掉就用 route。 */
export function gpxFileName(name: string): string {
  const slug = name
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
  return `artroute-${slug || "route"}.gpx`;
}
