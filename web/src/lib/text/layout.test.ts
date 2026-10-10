import { describe, expect, it } from "vitest";
import { shapeBounds } from "@/lib/shape/normalize";
import type { Stroke } from "@/lib/shape/types";
import { FONT_IDS, glyphFor } from "@/lib/text/hershey";
import { ALLOWED, layoutText, normalizeText, textShape, validateText } from "@/lib/text/layout";

const UNSUPPORTED = (c: string) => `不支援的字元「${c}」：只能用英文字母、數字與 ♥ ! ? & . -`;

/** validateText 允許的字元（從 ALLOWED 推導，不手抄）：可印 ASCII 與 ♥，過 ALLOWED 的留下。 */
const ALLOWED_CHARS = [...Array(0x7f - 0x20).keys()]
  .map((i) => String.fromCharCode(i + 0x20))
  .concat("♥")
  .filter((c) => ALLOWED.test(c));

const ys = (strokes: Stroke[]) => strokes.flatMap((s) => s.points.map((p) => p[1]));

describe("validateText", () => {
  it("合法文字回傳 null", () => {
    expect(validateText("I ♥ TAIPEI")).toBeNull();
    expect(validateText("Hello!\nRide & go")).toBeNull();
  });

  it("空字串或只有空白", () => {
    expect(validateText("  ")).toBe("請輸入文字");
  });

  it("不支援的字元", () => {
    expect(validateText("台北")).toBe("不支援的字元「台」：只能用英文字母、數字與 ♥ ! ? & . -");
  });

  it("超過 2 行", () => {
    expect(validateText("A\nB\nC")).toBe("最多 2 行");
  });

  it("一行超過 10 個字（空白也算）", () => {
    expect(validateText("ABCDE FGHIJ")).toBe("每行最多 10 個字");
  });

  it("emoji 的 ♥（U+2665 U+FE0F）合法，排版和純 ♥ 一樣", () => {
    expect(validateText("I ♥️ U")).toBeNull();
    expect(layoutText("I ♥️ U", "futural")).toEqual(layoutText("I ♥ U", "futural"));
  });

  it("NBSP 當一般空白：合法，排版和普通空白一樣", () => {
    expect(validateText("A B")).toBeNull();
    expect(layoutText("I U", "futural")).toEqual(layoutText("I U", "futural"));
  });

  it("\\r\\n 換行合法，行數照算", () => {
    expect(validateText("A\r\nB")).toBeNull();
    expect(validateText("A\r\nB\r\nC")).toBe("最多 2 行");
  });

  it("零寬空白（U+200B）不算字", () => {
    // 去掉 ZWSP 後剛好 10 字；若 ZWSP 被算進去就會變 11 字
    expect(validateText("ABCDE​FGHIJ")).toBeNull();
    expect(validateText("​  ")).toBe("請輸入文字");
  });

  it("不支援的不可見字元用 U+XXXX 顯示（大寫、至少 4 位）", () => {
    expect(validateText("A⁠B")).toBe(UNSUPPORTED("U+2060"));
  });

  it("Tab 這類空白字元也用 U+XXXX 顯示", () => {
    expect(validateText("A\tB")).toBe(UNSUPPORTED("U+0009"));
  });
});

describe("normalizeText", () => {
  it("\\r\\n 與 \\r 轉成 \\n", () => {
    expect(normalizeText("A\r\nB\rC")).toBe("A\nB\nC");
  });

  it("NBSP 轉成一般空白", () => {
    expect(normalizeText("A B")).toBe("A B");
  });

  it("移除 U+FE0F、U+FE0E 與 U+200B", () => {
    expect(normalizeText("I ♥️ U​︎")).toBe("I ♥ U");
  });
});

describe("layoutText", () => {
  it("每行水平置中", () => {
    // futural 的「-」：M4,13 L22,13，寬 26 → 置中後 x 從 -9 到 9
    expect(layoutText("-", "futural")).toEqual([{ points: [[-9, 13], [9, 13]], closed: false }]);
  });

  it("第二行往下移一個行距（futural 為 32）", () => {
    expect(layoutText("-\n-", "futural")[1]).toEqual({ points: [[-9, 45], [9, 45]], closed: false });
  });

  it("每個字的每一筆各自獨立，不做連接", () => {
    // futural：H 三筆、I 一筆
    expect(layoutText("HI", "futural")).toHaveLength(4);
  });

  it("空白只推進、不產生筆畫", () => {
    const strokes = layoutText("I I", "futural");
    expect(strokes).toHaveLength(2);
    // I 寬 8、空白 16 → 總寬 32，第一個 I 的豎線在 x = -16 + 4
    expect(strokes[0].points[0][0]).toBe(-12);
    expect(strokes[1].points[0][0]).toBe(12);
  });

  it("scripts 用自己的字寬推進：I 寬 16、空白 16 → 總寬 48", () => {
    const strokes = layoutText("I I", "scripts");
    expect(strokes).toHaveLength(2);
    // 第一個 I 的第一點 x = 14 − 24；第二個 I 起點 x = −24 + 32，再加 14
    expect(strokes[0].points[0][0]).toBe(-10);
    expect(strokes[1].points[0][0]).toBe(22);
  });

  it("閉合筆畫經過排版仍是 closed，點數不變", () => {
    const [o] = layoutText("O", "futural");
    expect(o.closed).toBe(true);
    expect(o.points).toHaveLength(20);
    const [heart] = layoutText("♥", "futural");
    expect(heart.closed).toBe(true);
    expect(heart.points).toHaveLength(32);
  });

  it("scripts 行距大於下伸部：第一行最低點低於第二行最高點", () => {
    // scripts 的 Z 下伸到 y=34；第二行的字高從 y=1 開始再加行距
    const two = layoutText("ZZ\nZZ", "scripts");
    const one = layoutText("ZZ", "scripts");
    expect(Math.max(...ys(two.slice(0, one.length)))).toBeLessThan(Math.min(...ys(two.slice(one.length))));
  });

  it("ALLOWED 全部字元排成兩行，兩行在 y 方向不重疊（每個字型）", () => {
    const line = ALLOWED_CHARS.filter((c) => c !== " ").join("");
    for (const font of FONT_IDS) {
      const one = layoutText(line, font);
      const two = layoutText(`${line}\n${line}`, font);
      expect(Math.max(...ys(two.slice(0, one.length))), font).toBeLessThan(
        Math.min(...ys(two.slice(one.length))),
      );
    }
  });
});

describe("ALLOWED 的每個字", () => {
  it("每個字型都查得到，且每筆至少 2 點", () => {
    for (const font of FONT_IDS) {
      for (const c of ALLOWED_CHARS) {
        const glyph = glyphFor(font, c);
        expect(glyph, `${font} ${c}`).not.toBeNull();
        for (const s of glyph!.strokes) {
          expect(s.points.length, `${font} ${c}`).toBeGreaterThanOrEqual(2);
        }
      }
    }
  });
});

describe("textShape", () => {
  it("正規化：長邊為 1、kind 是 text", () => {
    const shape = textShape("HI", "futural");
    expect(shape.kind).toBe("text");
    const b = shapeBounds(shape.strokes);
    expect(Math.max(b.halfWidth, b.halfHeight)).toBeCloseTo(0.5, 9);
  });

  it("y 軸翻轉：第一行在上（正規化後 y 較大）", () => {
    const first = layoutText("A", "futural").length;
    const shape = textShape("A\nB", "futural");
    expect(Math.min(...ys(shape.strokes.slice(0, first)))).toBeGreaterThan(
      Math.max(...ys(shape.strokes.slice(first))),
    );
  });

  it("CRLF 與 NBSP 經過 textShape 結果一樣", () => {
    expect(textShape("A\r\nB C", "scripts")).toEqual(textShape("A\nB C", "scripts"));
  });
});
