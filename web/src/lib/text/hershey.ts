// Hershey 單筆畫字型。資料來自 hersheytext 套件的 JSON（字型資料為 Public Domain）。
// 只引入 JSON：套件的 main 依賴 cheerio 和 fs，不能在瀏覽器裡用。
// 字型授權要求隨字型資料散布的致謝（原文照錄，來源：hersheytext 套件 svg_fonts 的 metadata）：
// - The Hershey Fonts were originally created by Dr. A. V. Hershey while working at the U. S. National Bureau of Standards.
// - The format of the Font data in this distribution was originally created by James Hurt, Cognition, Inc., 900 Technology Park Drive, Billerica, MA 01821 (mit-eddie!ci-dandelion!hurt)
import fontData from "hersheytext/hersheytext.min.json";
import type { Point, Stroke } from "@/lib/shape/types";

export const FONT_IDS = ["futural", "scripts"] as const;
export type HersheyFontId = (typeof FONT_IDS)[number];

export const FONT_LABELS: Record<HersheyFontId, string> = {
  futural: "Sans（Roman Simplex）",
  scripts: "Script（Script Simplex）",
};

/** 一個字：筆畫用來源座標（y 向下，大寫字高約 y 1–22），advance 是往右推進的寬度。 */
export type Glyph = { strokes: Stroke[]; advance: number };

// hersheytext JSON：chars[字元碼 - 33]，d 是只用 M/L 的 SVG 路徑，o 是半個字寬
type RawFont = { name: string; chars: { d: string; o: number }[] };
const FONTS = fontData as unknown as Record<HersheyFontId, RawFont>;
const FIRST_CHAR_CODE = 33;

/** 解析 Hershey 的 SVG 路徑（只有絕對座標 M、L；L 後面可以接多組座標）。每個 M 開始一筆。 */
export function parseHersheyPath(d: string): Point[][] {
  const strokes: Point[][] = [];
  for (const [, command, x, y] of d.matchAll(/([A-Za-z])|(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)/g)) {
    if (command === "M") {
      strokes.push([]);
      continue;
    }
    if (command === "L") continue;
    if (command) throw new Error(`不支援的 SVG 路徑指令：${command}`);
    strokes[strokes.length - 1].push([Number(x), Number(y)]);
  }
  return strokes;
}

/** 首尾相同（至少 4 點）的筆畫視為封閉，去掉重複的終點。 */
export function toStroke(points: Point[]): Stroke {
  const first = points[0];
  const last = points[points.length - 1];
  const closed = points.length >= 4 && first[0] === last[0] && first[1] === last[1];
  return { points: closed ? points.slice(0, -1) : points, closed };
}

// ♥ 不在 Hershey 的 ASCII 範圍內：用經典心形參數式
// x = 16 sin³t，y = 13 cos t − 5 cos 2t − 2 cos 3t − cos 4t（y 範圍 −17 到 11.92），
// 縮放到 Hershey 大寫字高（y 1–22），寬 24。
const HEART_SAMPLES = 32;
const HEART_TOP = 11.92;
const HEART_SCALE = 21 / (HEART_TOP + 17);

function heartGlyph(): Glyph {
  const points: Point[] = [];
  for (let i = 0; i < HEART_SAMPLES; i++) {
    const t = (i / HEART_SAMPLES) * 2 * Math.PI;
    const x = 16 * Math.sin(t) ** 3;
    const y = 13 * Math.cos(t) - 5 * Math.cos(2 * t) - 2 * Math.cos(3 * t) - Math.cos(4 * t);
    points.push([12 + x * HEART_SCALE, 1 + (HEART_TOP - y) * HEART_SCALE]);
  }
  return { strokes: [{ points, closed: true }], advance: 24 };
}

/** hersheytext 資料沒有的字：空白（寬 16，約一個大寫字寬）和 ♥。兩套字型共用。鍵是使用者輸入的字，用 Map 查表。 */
const EXTRA_GLYPHS = new Map<string, Glyph>([
  [" ", { strokes: [], advance: 16 }],
  ["♥", heartGlyph()],
]);

/** 取得一個字的筆畫；字型裡沒有的字回傳 null。引擎要求每筆至少 2 點，少於 2 點的筆畫（單點或空的 M）直接丟掉。 */
export function glyphFor(font: HersheyFontId, char: string): Glyph | null {
  const extra = EXTRA_GLYPHS.get(char);
  if (extra) return extra;
  const raw = FONTS[font].chars[char.charCodeAt(0) - FIRST_CHAR_CODE];
  if (!raw) return null;
  return {
    strokes: parseHersheyPath(raw.d).map(toStroke).filter((s) => s.points.length >= 2),
    advance: raw.o * 2,
  };
}
