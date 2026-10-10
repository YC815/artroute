import { describe, expect, it, vi } from "vitest";
import { decodeWithinLimit, MAX_SIDE, type DecodedBitmap } from "./decode";

/** 假的 createImageBitmap：依序回傳指定尺寸，並記錄每個 bitmap 是否被關閉。 */
function fakeFactory(sizes: [number, number][]) {
  const bitmaps = sizes.map(([width, height]) => ({ width, height, close: vi.fn() }));
  const create = vi.fn(async (): Promise<DecodedBitmap> => {
    const next = bitmaps[create.mock.calls.length - 1];
    if (!next) throw new Error("多解碼了一次");
    return next;
  });
  return { create, bitmaps };
}

const file = new Blob(["x"]);

describe("decodeWithinLimit", () => {
  it("橫圖或方圖：只解碼一次，用 resizeWidth 縮到長邊上限", async () => {
    const { create, bitmaps } = fakeFactory([[MAX_SIDE, 300]]);
    const bitmap = await decodeWithinLimit(file, create);
    expect(bitmap).toBe(bitmaps[0]);
    expect(create).toHaveBeenCalledTimes(1);
    expect(create).toHaveBeenCalledWith(file, { resizeWidth: MAX_SIDE, resizeQuality: "high" });
    expect(bitmaps[0].close).not.toHaveBeenCalled();
  });

  it("直圖：第一次的高度超過上限，關掉它並改用 resizeHeight 重新解碼", async () => {
    const { create, bitmaps } = fakeFactory([
      [MAX_SIDE, MAX_SIDE * 3],
      [MAX_SIDE / 3, MAX_SIDE],
    ]);
    const bitmap = await decodeWithinLimit(file, create);
    expect(bitmap).toBe(bitmaps[1]);
    expect(create).toHaveBeenCalledTimes(2);
    expect(create).toHaveBeenLastCalledWith(file, { resizeHeight: MAX_SIDE, resizeQuality: "high" });
    expect(bitmaps[0].close).toHaveBeenCalledTimes(1);
    expect(bitmaps[1].close).not.toHaveBeenCalled();
  });

  it("高度剛好等於上限不重新解碼", async () => {
    const { create } = fakeFactory([[MAX_SIDE, MAX_SIDE]]);
    await decodeWithinLimit(file, create);
    expect(create).toHaveBeenCalledTimes(1);
  });

  it("瀏覽器忽略縮放選項（寬度仍超過上限）：關掉 bitmap 並拒絕，不回傳大圖", async () => {
    const { create, bitmaps } = fakeFactory([[4000, 300]]);
    await expect(decodeWithinLimit(file, create)).rejects.toThrow("圖片尺寸");
    expect(bitmaps[0].close).toHaveBeenCalledTimes(1);
  });

  it("直圖重新解碼後仍超過上限：兩個 bitmap 都關掉並拒絕", async () => {
    const { create, bitmaps } = fakeFactory([
      [4000, 6000],
      [4000, 6000],
    ]);
    await expect(decodeWithinLimit(file, create)).rejects.toThrow("圖片尺寸");
    expect(bitmaps[0].close).toHaveBeenCalledTimes(1);
    expect(bitmaps[1].close).toHaveBeenCalledTimes(1);
  });

  it("解碼失敗直接往外丟，不重試", async () => {
    const create = vi.fn().mockRejectedValue(new Error("壞檔"));
    await expect(decodeWithinLimit(file, create)).rejects.toThrow("壞檔");
    expect(create).toHaveBeenCalledTimes(1);
  });
});
