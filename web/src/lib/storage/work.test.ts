import { describe, expect, it } from "vitest";
import { STORAGE_KEY, loadWork, saveWork, type SavedWork } from "@/lib/storage/work";
import type { Point, Stroke } from "@/lib/shape/types";

function memoryStore(initial: Record<string, string> = {}) {
  const data = new Map(Object.entries(initial));
  return {
    getItem: (key: string) => data.get(key) ?? null,
    setItem: (key: string, value: string) => void data.set(key, value),
  };
}

const TEXT_WORK: SavedWork = {
  source: { kind: "text", text: "I ♥ TAIPEI", font: "futural" },
  transform: { center: [121.5654, 25.033], sizeM: 2000, rotationDeg: 15 },
};

const IMAGE_WORK: SavedWork = {
  source: { kind: "image", strokes: [{ points: [[-0.5, 0.5], [0.5, 0.5], [0, -0.5]], closed: true }] },
  transform: { center: [120.68, 24.14], sizeM: 800, rotationDeg: 0 },
};

function imageWork(strokes: Stroke[]): SavedWork {
  return { source: { kind: "image", strokes }, transform: IMAGE_WORK.transform };
}

function points(n: number): Point[] {
  return Array.from({ length: n }, (_, i) => [i / n, 0.5]);
}

describe("saveWork / loadWork", () => {
  it("文字作品存了讀得回來", () => {
    const store = memoryStore();
    saveWork(store, TEXT_WORK);
    expect(loadWork(store)).toEqual(TEXT_WORK);
  });

  it("圖片作品存了讀得回來", () => {
    const store = memoryStore();
    saveWork(store, IMAGE_WORK);
    expect(loadWork(store)).toEqual(IMAGE_WORK);
  });

  it("沒有存檔回傳 null", () => {
    expect(loadWork(memoryStore())).toBeNull();
  });

  it("壞掉的 JSON、版本不符、欄位不合法都回傳 null", () => {
    expect(loadWork(memoryStore({ [STORAGE_KEY]: "{oops" }))).toBeNull();
    expect(loadWork(memoryStore({ [STORAGE_KEY]: JSON.stringify({ v: 2, ...TEXT_WORK }) }))).toBeNull();
    const badFont = { v: 1, ...TEXT_WORK, source: { kind: "text", text: "A", font: "comic" } };
    expect(loadWork(memoryStore({ [STORAGE_KEY]: JSON.stringify(badFont) }))).toBeNull();
  });

  it("storage 丟錯時不會讓程式壞掉", () => {
    const broken = {
      getItem: () => {
        throw new Error("SecurityError");
      },
      setItem: () => {
        throw new Error("QuotaExceededError");
      },
    };
    expect(() => saveWork(broken, TEXT_WORK)).not.toThrow();
    expect(loadWork(broken)).toBeNull();
  });
});

describe("圖片筆畫的數量與點數上限（與 /match 契約一致）", () => {
  it("0 筆畫回傳 null", () => {
    const store = memoryStore();
    saveWork(store, imageWork([]));
    expect(loadWork(store)).toBeNull();
  });

  it("40 筆畫可以讀回，41 筆畫回傳 null", () => {
    const stroke: Stroke = { points: points(2), closed: false };
    const ok = memoryStore();
    saveWork(ok, imageWork(Array.from({ length: 40 }, () => stroke)));
    expect(loadWork(ok)).not.toBeNull();

    const tooMany = memoryStore();
    saveWork(tooMany, imageWork(Array.from({ length: 41 }, () => stroke)));
    expect(loadWork(tooMany)).toBeNull();
  });

  it("筆畫點數為 0 或 1 回傳 null（即使格式其他部分正確）", () => {
    for (const bad of [[], points(1)]) {
      const store = memoryStore();
      saveWork(store, imageWork([{ points: bad, closed: false }]));
      expect(loadWork(store)).toBeNull();
    }
  });

  it("2000 點可以讀回，2001 點回傳 null", () => {
    const ok = memoryStore();
    saveWork(ok, imageWork([{ points: points(2000), closed: true }]));
    expect(loadWork(ok)).not.toBeNull();

    const tooMany = memoryStore();
    saveWork(tooMany, imageWork([{ points: points(2001), closed: true }]));
    expect(loadWork(tooMany)).toBeNull();
  });
});

describe("有限數字", () => {
  // JSON.parse 把 1e999 讀成 Infinity，NaN 則會在 JSON.stringify 時變成 null
  const INFINITE_SIZE =
    '{"v":1,"source":{"kind":"text","text":"A","font":"futural"},"transform":{"center":[121.5,25],"sizeM":1e999,"rotationDeg":0}}';
  const INFINITE_COORD =
    '{"v":1,"source":{"kind":"image","strokes":[{"points":[[1e999,0],[0,1]],"closed":false}]},"transform":{"center":[121.5,25],"sizeM":800,"rotationDeg":0}}';

  it("測試用的字串確實會解析出 Infinity", () => {
    expect(JSON.parse(INFINITE_SIZE).transform.sizeM).toBe(Infinity);
    expect(JSON.parse(INFINITE_COORD).source.strokes[0].points[0][0]).toBe(Infinity);
  });

  it("Infinity 的 sizeM 或座標回傳 null", () => {
    expect(loadWork(memoryStore({ [STORAGE_KEY]: INFINITE_SIZE }))).toBeNull();
    expect(loadWork(memoryStore({ [STORAGE_KEY]: INFINITE_COORD }))).toBeNull();
  });

  it("NaN 存進去（JSON 會寫成 null）讀回來是 null，不會壞掉", () => {
    const store = memoryStore();
    saveWork(store, { ...TEXT_WORK, transform: { ...TEXT_WORK.transform, sizeM: NaN } });
    expect(loadWork(store)).toBeNull();

    const nanCenter = memoryStore();
    saveWork(nanCenter, { ...TEXT_WORK, transform: { ...TEXT_WORK.transform, center: [NaN, 25] } });
    expect(loadWork(nanCenter)).toBeNull();
  });

  it("sizeM 必須是正數，rotationDeg 必須是有限數", () => {
    for (const transform of [
      { ...TEXT_WORK.transform, sizeM: 0 },
      { ...TEXT_WORK.transform, sizeM: -5 },
      { ...TEXT_WORK.transform, rotationDeg: Infinity },
    ]) {
      const store = memoryStore();
      saveWork(store, { ...TEXT_WORK, transform });
      expect(loadWork(store)).toBeNull();
    }
  });
});
