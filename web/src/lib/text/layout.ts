import { normalizeStrokes } from "@/lib/shape/normalize";
import type { Shape, Stroke } from "@/lib/shape/types";
import { glyphFor, type HersheyFontId } from "@/lib/text/hershey";

export const MAX_LINES = 2;
export const MAX_LINE_CHARS = 10;
/** 行距（Hershey 單位）：字高約 21，加上下伸部（g、y）與行間空隙。 */
const LINE_HEIGHT = 32;
const ALLOWED = /^[A-Za-z0-9♥!?&.\- ]$/;

/** 檢查文字；合法回傳 null，不合法回傳給使用者看的訊息。空白也算一個字。 */
export function validateText(text: string): string | null {
  const lines = text.split("\n");
  const bad = [...text.replaceAll("\n", "")].find((c) => !ALLOWED.test(c));
  if (bad) return `不支援的字元「${bad}」：只能用英文字母、數字與 ♥ ! ? & . -`;
  if (text.trim() === "") return "請輸入文字";
  if (lines.length > MAX_LINES) return `最多 ${MAX_LINES} 行`;
  if (lines.some((line) => [...line].length > MAX_LINE_CHARS)) return `每行最多 ${MAX_LINE_CHARS} 個字`;
  return null;
}

/** 排版成來源座標（y 向下）的筆畫：每行置中、每個字的每一筆各自是一個 Stroke，不做連接。 */
export function layoutText(text: string, font: HersheyFontId): Stroke[] {
  return text.split("\n").flatMap((line, row) => {
    const glyphs = [...line].map((c) => glyphFor(font, c)).filter((g) => g !== null);
    const width = glyphs.reduce((sum, g) => sum + g.advance, 0);
    let x = -width / 2;
    return glyphs.flatMap((g) => {
      const dx = x;
      x += g.advance;
      return g.strokes.map((s) => ({
        closed: s.closed,
        points: s.points.map(([px, py]): [number, number] => [px + dx, py + row * LINE_HEIGHT]),
      }));
    });
  });
}

/** 文字 → 正規化的 Shape。呼叫前先用 validateText 檢查。 */
export function textShape(text: string, font: HersheyFontId): Shape {
  return { kind: "text", strokes: normalizeStrokes(layoutText(text, font)) };
}
