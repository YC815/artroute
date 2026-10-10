// 描邊流程的狀態機（純邏輯，沒有任何瀏覽器 API，所以能在 node 測）：
// 記住最後一次載入的灰階影像，調門檻或反轉時直接重用。
import { extractRings, type Ring } from "@/lib/image/rings";
import { createSerialQueue } from "@/lib/image/serial-queue";
import { otsuThreshold } from "@/lib/image/threshold";

/** file 有值時重新載入圖片；threshold 為 null 時用 Otsu 自動決定。 */
export type TraceRequest = { id: number; file?: Blob; threshold: number | null; invert: boolean };

export type TraceResponse =
  | { id: number; ok: true; width: number; height: number; threshold: number; rings: Ring[] }
  | { id: number; ok: false; message: string };

export type GrayImage = { gray: Uint8Array; width: number; height: number };

/**
 * 建立描邊器。請求依送出順序一個一個處理（不跳過任何一個），所以保留的影像永遠是「最後送出的檔案」：
 * 慢的 A 先送、快的 B 後送時，A 不會在 B 之後才把影像蓋回去。
 * 回傳的函式不會 reject，錯誤變成 ok: false 的回應。
 */
export function createTracer(decode: (file: Blob) => Promise<GrayImage>) {
  const enqueue = createSerialQueue();
  let image: GrayImage | null = null;

  async function run(request: TraceRequest): Promise<TraceResponse> {
    if (request.file) {
      // 先清掉舊圖：新圖解碼失敗時，之後只送門檻的請求不會描到舊圖
      image = null;
      image = await decode(request.file);
    }
    if (!image) throw new Error("還沒有載入圖片");
    const threshold = request.threshold ?? otsuThreshold(image.gray);
    const rings = extractRings(image.gray, image.width, image.height, threshold, request.invert);
    return { id: request.id, ok: true, width: image.width, height: image.height, threshold, rings };
  }

  return (request: TraceRequest): Promise<TraceResponse> =>
    enqueue(() => run(request)).catch((error: unknown): TraceResponse => {
      const message = error instanceof Error ? error.message : "圖片處理失敗";
      return { id: request.id, ok: false, message };
    });
}
