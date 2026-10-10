import { normalizeStrokes } from "@/lib/shape/normalize";
import type { Point, Shape, Stroke } from "@/lib/shape/types";
import { glyphFor, type HersheyFontId } from "@/lib/text/hershey";

export const MAX_LINES = 2;
export const MAX_LINE_CHARS = 10;
/**
 * 行距（Hershey 單位），每個字型各自決定。字高約 y 1–22。
 * futural 最低到 y=29（g、j、p、q、y），32 夠用；
 * scripts 最低到 y=34（J、Y、Z、f、g、j、p、q、y、z），36 才能讓下一行的字頂（y=1+36=37）不碰到上一行的下伸部。
 */
const LINE_HEIGHT: Record<HersheyFontId, number> = { futural: 32, scripts: 36 };
export const ALLOWED = /^[A-Za-z0-9♥!?&.\- ]$/;

/** 換行統一成 \n；NBSP 當一般空白；去掉看不見的 U+FE0E、U+FE0F（emoji 選擇符）與 U+200B（零寬空白）。 */
export function normalizeText(text: string): string {
  return text
    .replace(/\r\n?/g, "\n")
    .replace(/\u00A0/g, " ")
    .replace(/[\uFE0E\uFE0F\u200B]/g, "");
}

// 控制字元、格式字元（如 U+2060）、空白分隔字元：看不見或長得像空白，錯誤訊息改顯示碼位
const INVISIBLE = /^[\p{Cc}\p{Cf}\p{Z}]$/u;

/** 給使用者看的字元：看不見的字元顯示成 U+XXXX（大寫十六進位，至少 4 位）。 */
function showChar(c: string): string {
  if (!INVISIBLE.test(c)) return c;
  return `U+${c.codePointAt(0)!.toString(16).toUpperCase().padStart(4, "0")}`;
}

/** 檢查文字；合法回傳 null，不合法回傳給使用者看的訊息。空白也算一個字。輸入先經過 normalizeText。 */
export function validateText(raw: string): string | null {
  const text = normalizeText(raw);
  const lines = text.split("\n");
  const bad = [...text.replaceAll("\n", "")].find((c) => !ALLOWED.test(c));
  if (bad) return `不支援的字元「${showChar(bad)}」：只能用英文字母、數字與 ♥ ! ? & . -`;
  if (text.trim() === "") return "請輸入文字";
  if (lines.length > MAX_LINES) return `最多 ${MAX_LINES} 行`;
  if (lines.some((line) => [...line].length > MAX_LINE_CHARS)) return `每行最多 ${MAX_LINE_CHARS} 個字`;
  return null;
}

/** 筆畫整個平移 (dx, dy)，回傳新物件。 */
function shifted(stroke: Stroke, dx: number, dy: number): Stroke {
  return {
    closed: stroke.closed,
    points: stroke.points.map(([x, y]): Point => [x + dx, y + dy]),
  };
}

/** 一行：字依序往右推進，整行的字寬合計後置中，整行往下移 dy。 */
function layoutLine(line: string, font: HersheyFontId, dy: number): Stroke[] {
  const glyphs = [...line].map((c) => glyphFor(font, c)).filter((g) => g !== null);
  const width = glyphs.reduce((sum, g) => sum + g.advance, 0);
  const strokes: Stroke[] = [];
  let x = -width / 2;
  for (const g of glyphs) {
    strokes.push(...g.strokes.map((s) => shifted(s, x, dy)));
    x += g.advance;
  }
  return strokes;
}

/** 排版成來源座標（y 向下）的筆畫：每行置中、每個字的每一筆各自是一個 Stroke，不做連接。輸入先經過 normalizeText。 */
export function layoutText(raw: string, font: HersheyFontId): Stroke[] {
  return normalizeText(raw)
    .split("\n")
    .flatMap((line, row) => layoutLine(line, font, row * LINE_HEIGHT[font]));
}

/** 文字 → 正規化的 Shape。呼叫前先用 validateText 檢查（layoutText 會先做 normalizeText）。 */
export function textShape(text: string, font: HersheyFontId): Shape {
  return { kind: "text", strokes: normalizeStrokes(layoutText(text, font)) };
}
