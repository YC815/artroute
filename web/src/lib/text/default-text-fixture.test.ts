import { readFileSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { textShape } from "@/lib/text/layout";

// 引擎的搜尋基準測試（engine/src/engine/bench/search_cases.py）用網站預設文字的正規化筆畫量完成條件。
// 這份檔案由網站的 textShape 產生；字型或排版改了，這個測試會紅：設 UPDATE_DEFAULT_TEXT=1 重跑一次重新產生。
const FIXTURE = new URL("../../../../engine/src/engine/bench/default_text.json", import.meta.url);
const TEXT = "I ♥ TAIPEI";
const FONT = "futural";

describe("引擎 bench 的預設文字與網站一致", () => {
  it(`${TEXT}（${FONT}）`, () => {
    const shape = textShape(TEXT, FONT);
    const expected = { text: TEXT, font: FONT, strokes: shape.strokes };
    if (process.env.UPDATE_DEFAULT_TEXT) writeFileSync(fileURLToPath(FIXTURE), `${JSON.stringify(expected)}\n`);
    expect(JSON.parse(readFileSync(fileURLToPath(FIXTURE), "utf8"))).toEqual(expected);
  });
});
