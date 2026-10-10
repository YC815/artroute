import { z } from "zod";
import { MAX_STROKE_POINTS, MAX_STROKES, MIN_STROKE_POINTS } from "@/lib/limits";
import type { Transform } from "@/lib/shape/transform";
import type { Stroke } from "@/lib/shape/types";
import { FONT_IDS, type HersheyFontId } from "@/lib/text/hershey";

/** 目前的作品：圖形來源＋擺放。圖片只存選好的正規化筆畫（圖片本身太大，不存）。 */
export type SavedSource =
  | { kind: "text"; text: string; font: HersheyFontId }
  | { kind: "image"; strokes: Stroke[] };

export type SavedWork = { source: SavedSource; transform: Transform };

export const STORAGE_KEY = "artroute:work";

// zod 4 的 z.number() 本來就拒絕 NaN 與 Infinity（.finite() 已經是 no-op），
// JSON.parse 讀到 1e999 會得到 Infinity，這裡靠它擋掉損壞的存檔。
const pointSchema = z.tuple([z.number(), z.number()]);

// 圖片筆畫的上限與 /match 契約相同（點數 2–2000、筆畫 1–40），超出就當成沒有存檔。
const strokeSchema = z.object({
  points: z.array(pointSchema).min(MIN_STROKE_POINTS).max(MAX_STROKE_POINTS),
  closed: z.boolean(),
});

const savedWorkSchema = z.object({
  v: z.literal(1),
  source: z.discriminatedUnion("kind", [
    z.object({ kind: z.literal("text"), text: z.string(), font: z.enum(FONT_IDS) }),
    z.object({
      kind: z.literal("image"),
      strokes: z.array(strokeSchema).min(1).max(MAX_STROKES),
    }),
  ]),
  transform: z.object({ center: pointSchema, sizeM: z.number().positive(), rotationDeg: z.number() }),
});

type KeyValueStore = Pick<Storage, "getItem" | "setItem">;

/** 瀏覽器的 localStorage。存取 window.localStorage 本身也可能丟錯，所以包在方法裡，由 saveWork／loadWork 的 try 接住。 */
export const browserStore: KeyValueStore = {
  getItem: (key) => window.localStorage.getItem(key),
  setItem: (key, value) => window.localStorage.setItem(key, value),
};

/** 存檔。隱私模式或空間不足時 setItem 會丟錯：放棄這次存檔，不影響使用。 */
export function saveWork(store: KeyValueStore, work: SavedWork): void {
  try {
    store.setItem(STORAGE_KEY, JSON.stringify({ v: 1, ...work }));
  } catch {
    // 不存
  }
}

/** 讀檔。沒有存檔、格式壞掉、版本不符或讀取丟錯都回傳 null。 */
export function loadWork(store: KeyValueStore): SavedWork | null {
  try {
    const parsed = savedWorkSchema.safeParse(JSON.parse(store.getItem(STORAGE_KEY) ?? "null"));
    return parsed.success ? { source: parsed.data.source, transform: parsed.data.transform } : null;
  } catch {
    return null;
  }
}
