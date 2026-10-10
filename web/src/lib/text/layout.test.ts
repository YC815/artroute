import { describe, expect, it } from "vitest";
import { shapeBounds } from "@/lib/shape/normalize";
import { layoutText, textShape, validateText } from "@/lib/text/layout";

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
});

describe("layoutText", () => {
  it("每行水平置中", () => {
    // futural 的「-」：M4,13 L22,13，寬 26 → 置中後 x 從 -9 到 9
    expect(layoutText("-", "futural")).toEqual([{ points: [[-9, 13], [9, 13]], closed: false }]);
  });

  it("第二行往下移一個行距（32）", () => {
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
});

describe("textShape", () => {
  it("正規化：長邊為 1、kind 是 text", () => {
    const shape = textShape("HI", "futural");
    expect(shape.kind).toBe("text");
    const b = shapeBounds(shape.strokes);
    expect(Math.max(b.halfWidth, b.halfHeight)).toBeCloseTo(0.5, 9);
  });
});
