import { describe, expect, it, vi } from "vitest";
import { FONT_IDS, glyphFor, parseHersheyPath, toStroke } from "@/lib/text/hershey";

describe("parseHersheyPath", () => {
  it("每個 M 開始一筆，L 後面可以接多組座標", () => {
    expect(parseHersheyPath("M4,1 L4,22 M4,1 L13,1 16,2")).toEqual([
      [[4, 1], [4, 22]],
      [[4, 1], [13, 1], [16, 2]],
    ]);
  });

  it("接受負數座標", () => {
    expect(parseHersheyPath("M-1,21 L0,18")).toEqual([[[-1, 21], [0, 18]]]);
  });

  it("遇到 M、L 以外的指令就丟錯", () => {
    expect(() => parseHersheyPath("M0,0 C1,1 2,2 3,3")).toThrow("不支援的 SVG 路徑指令：C");
  });
});

describe("toStroke", () => {
  it("首尾相同視為封閉，去掉重複的終點", () => {
    expect(toStroke([[0, 0], [1, 0], [1, 1], [0, 0]])).toEqual({
      points: [[0, 0], [1, 0], [1, 1]],
      closed: true,
    });
  });

  it("首尾不同是開放筆畫", () => {
    expect(toStroke([[0, 0], [1, 0]])).toEqual({ points: [[0, 0], [1, 0]], closed: false });
  });
});

describe("glyphFor", () => {
  it("futural 的 A：三筆、都開放、寬 18", () => {
    const a = glyphFor("futural", "A")!;
    expect(a.advance).toBe(18);
    expect(a.strokes).toEqual([
      { points: [[9, 1], [1, 22]], closed: false },
      { points: [[9, 1], [17, 22]], closed: false },
      { points: [[4, 15], [14, 15]], closed: false },
    ]);
  });

  it("futural 的 O 是一個 20 點的封閉筆畫", () => {
    const o = glyphFor("futural", "O")!;
    expect(o.strokes).toHaveLength(1);
    expect(o.strokes[0].closed).toBe(true);
    expect(o.strokes[0].points).toHaveLength(20);
  });

  it("scripts 字型也讀得到", () => {
    expect(glyphFor("scripts", "a")!.strokes.length).toBeGreaterThan(0);
  });

  it("空白沒有筆畫、寬 16", () => {
    expect(glyphFor("scripts", " ")).toEqual({ strokes: [], advance: 16 });
  });

  it("♥ 是 32 點的封閉筆畫，落在大寫字高內", () => {
    const heart = glyphFor("futural", "♥")!;
    const [stroke] = heart.strokes;
    expect(stroke.closed).toBe(true);
    expect(stroke.points).toHaveLength(32);
    const ys = stroke.points.map((p) => p[1]);
    expect(Math.min(...ys)).toBeGreaterThanOrEqual(1);
    expect(Math.max(...ys)).toBeLessThanOrEqual(22.01);
  });

  it("字型裡沒有的字回傳 null", () => {
    expect(glyphFor("futural", "中")).toBeNull();
  });

  it("validateText 允許的每個字，每筆都至少 2 點", () => {
    const chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789!?&.- ♥";
    for (const font of FONT_IDS) {
      for (const c of chars) {
        for (const s of glyphFor(font, c)!.strokes) {
          expect(s.points.length, `${font} ${c}`).toBeGreaterThanOrEqual(2);
        }
      }
    }
  });

  it("字型資料裡的單點與空筆畫會被濾掉", async () => {
    // 「M1,1」「M2,2」只有一個點、第三個「M M」是零點，只有「L3,3 L4,4」那筆合格
    const degenerate = { d: "M1,1 M2,2 M M L3,3 L4,4", o: 5 };
    vi.resetModules();
    vi.doMock("hersheytext/hersheytext.min.json", () => ({
      default: {
        futural: { name: "futural", chars: Array.from({ length: 95 }, () => degenerate) },
        scripts: { name: "scripts", chars: Array.from({ length: 95 }, () => degenerate) },
      },
    }));
    try {
      const fake = await import("@/lib/text/hershey");
      expect(fake.glyphFor("futural", "!")!.strokes).toEqual([
        { points: [[3, 3], [4, 4]], closed: false },
      ]);
    } finally {
      vi.doUnmock("hersheytext/hersheytext.min.json");
      vi.resetModules();
    }
  });
});
