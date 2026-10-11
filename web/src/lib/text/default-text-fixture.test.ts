import { readFileSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { textShape } from "@/lib/text/layout";

// 引擎的搜尋基準測試（engine/src/engine/bench/search_cases.py）用網站預設文字的正規化筆畫量完成條件。
// 這份檔案由網站的 textShape 產生；字型或排版改了，這個測試會紅：設 UPDATE_DEFAULT_TEXT=1 重跑一次重新產生。
const FIXTURE = new URL("../../../../engine/src/engine/bench/default_text.json", import.meta.url);
const TEXT = "I ♥ TAIPEI";
const FONT = "futural";
// ♥ 用 Math.sin／cos 產生，最後一位會隨平台與 Node 版本不同（雲端產生的檔案在 macOS arm64 差 1 ulp）
const EPS = 1e-12;

type Fixture = { text: string; font: string; strokes: { closed: boolean; points: [number, number][] }[] };

/** 座標以外的部分：文字、字型、每筆的 closed 與點數。 */
function outline(f: Fixture) {
  return { text: f.text, font: f.font, strokes: f.strokes.map((s) => ({ closed: s.closed, n: s.points.length })) };
}

describe("引擎 bench 的預設文字與網站一致", () => {
  it(`${TEXT}（${FONT}）`, () => {
    const shape = textShape(TEXT, FONT);
    const expected: Fixture = { text: TEXT, font: FONT, strokes: shape.strokes };
    if (process.env.UPDATE_DEFAULT_TEXT) writeFileSync(fileURLToPath(FIXTURE), `${JSON.stringify(expected)}\n`);
    const actual = JSON.parse(readFileSync(fileURLToPath(FIXTURE), "utf8")) as Fixture;
    expect(outline(actual)).toEqual(outline(expected));
    const diffs = actual.strokes.flatMap((s, i) =>
      s.points.flatMap(([x, y], j) => {
        const [ex, ey] = expected.strokes[i].points[j];
        return [Math.abs(x - ex), Math.abs(y - ey)];
      }),
    );
    expect(Math.max(...diffs)).toBeLessThan(EPS);
  });
});
