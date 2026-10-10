import { describe, expect, it } from "vitest";
import { createTracer, type GrayImage, type TraceRequest } from "./tracer";

/** 白底中間一塊黑方塊的灰階圖。 */
function squareImage(size: number): GrayImage {
  const gray = new Uint8Array(size * size).fill(255);
  for (let y = 1; y < size - 1; y++) for (let x = 1; x < size - 1; x++) gray[y * size + x] = 0;
  return { gray, width: size, height: size };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((r) => (resolve = r));
  return { promise, resolve };
}

const fileA = new Blob(["A"]);
const fileB = new Blob(["B"]);
const request = (id: number, extra: Partial<TraceRequest> = {}): TraceRequest => ({
  id,
  threshold: 128,
  invert: false,
  ...extra,
});

describe("createTracer", () => {
  it("慢的 A 先送、快的 B 後送：之後只送門檻的請求，描的是 B，不是 A", async () => {
    const slowA = deferred<GrayImage>();
    const trace = createTracer((file) => (file === fileA ? slowA.promise : Promise.resolve(squareImage(4))));

    const first = trace(request(1, { file: fileA }));
    const second = trace(request(2, { file: fileB }));
    slowA.resolve(squareImage(8)); // A 比 B 晚解完
    const third = trace(request(3));

    const responses = await Promise.all([first, second, third]);
    expect(responses.map((r) => r.id)).toEqual([1, 2, 3]);
    expect(responses[2]).toMatchObject({ ok: true, width: 4, height: 4 });
    expect(responses[2].ok && responses[2].rings.length).toBeGreaterThan(0);
  });

  it("A 還沒解完時送出的門檻請求，等到 B 載入後才執行", async () => {
    const slowA = deferred<GrayImage>();
    const trace = createTracer((file) => (file === fileA ? slowA.promise : Promise.resolve(squareImage(4))));

    const first = trace(request(1, { file: fileA }));
    const second = trace(request(2, { file: fileB }));
    const third = trace(request(3, { threshold: 50 }));
    slowA.resolve(squareImage(8));

    const [a, b, c] = await Promise.all([first, second, third]);
    expect(a).toMatchObject({ ok: true, width: 8 });
    expect(b).toMatchObject({ ok: true, width: 4 });
    expect(c).toMatchObject({ ok: true, width: 4, threshold: 50 });
  });

  it("threshold 為 null 時用 Otsu 決定門檻", async () => {
    const trace = createTracer(async () => squareImage(6));
    const response = await trace(request(1, { file: fileA, threshold: null }));
    expect(response.ok).toBe(true);
    // 純黑與純白的圖：Otsu 門檻落在兩群之間（0 ≤ t < 255）
    expect(response.ok && response.threshold).toBeGreaterThanOrEqual(0);
    expect(response.ok && response.threshold).toBeLessThan(255);
  });

  it("還沒載入圖片就描邊：回傳錯誤，不丟例外", async () => {
    const trace = createTracer(async () => squareImage(4));
    expect(await trace(request(7))).toEqual({ id: 7, ok: false, message: "還沒有載入圖片" });
  });

  it("解碼失敗：回傳該請求的錯誤，並清掉舊圖；之後的請求不會描到舊圖，也不會被卡住", async () => {
    const trace = createTracer(async (file) => {
      if (file === fileB) throw new Error("壞檔");
      return squareImage(4);
    });
    expect(await trace(request(1, { file: fileA }))).toMatchObject({ ok: true });
    expect(await trace(request(2, { file: fileB }))).toEqual({ id: 2, ok: false, message: "壞檔" });
    expect(await trace(request(3))).toEqual({ id: 3, ok: false, message: "還沒有載入圖片" });
    expect(await trace(request(4, { file: fileA }))).toMatchObject({ ok: true, width: 4 });
  });

  it("丟出非 Error 的值時用通用訊息", async () => {
    const trace = createTracer(async () => {
      throw "字串";
    });
    expect(await trace(request(1, { file: fileA }))).toEqual({ id: 1, ok: false, message: "圖片處理失敗" });
  });
});
