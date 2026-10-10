// 圖片描邊的 Web Worker：解碼、縮圖、灰階、Otsu、抽輪廓都在這裡做，不卡住畫面。
// 解碼後的灰階影像留在 worker 裡，調門檻時不用重新解碼。
import { decodeWithinLimit } from "@/lib/image/decode";
import { extractRings, type Ring } from "@/lib/image/rings";
import { otsuThreshold, toGray } from "@/lib/image/threshold";

/** file 有值時重新載入圖片；threshold 為 null 時用 Otsu 自動決定。 */
export type TraceRequest = { id: number; file?: Blob; threshold: number | null; invert: boolean };

export type TraceResponse =
  | { id: number; ok: true; width: number; height: number; threshold: number; rings: Ring[] }
  | { id: number; ok: false; message: string };

type GrayImage = { gray: Uint8Array; width: number; height: number };
let image: GrayImage | null = null;

/** 瀏覽器的解碼錯誤訊息是英文，換成給使用者看的說明。 */
function createBitmap(file: Blob, options: ImageBitmapOptions): Promise<ImageBitmap> {
  return createImageBitmap(file, options).catch(() => {
    throw new Error("無法讀取這張圖片（檔案可能已損毀，或尺寸過大）");
  });
}

async function decode(file: Blob): Promise<GrayImage> {
  // 長邊縮到 MAX_SIDE 的 bitmap 才會走到 canvas，不會完整解出原始尺寸
  const bitmap = await decodeWithinLimit(file, createBitmap);
  try {
    const { width, height } = bitmap;
    const canvas = new OffscreenCanvas(width, height);
    const ctx = canvas.getContext("2d");
    if (!ctx) throw new Error("瀏覽器不支援 OffscreenCanvas 2D");
    ctx.drawImage(bitmap, 0, 0);
    return { gray: toGray(ctx.getImageData(0, 0, width, height).data), width, height };
  } finally {
    bitmap.close();
  }
}

async function trace(request: TraceRequest): Promise<TraceResponse> {
  if (request.file) image = await decode(request.file);
  if (!image) throw new Error("還沒有載入圖片");
  const threshold = request.threshold ?? otsuThreshold(image.gray);
  const rings = extractRings(image.gray, image.width, image.height, threshold, request.invert);
  return { id: request.id, ok: true, width: image.width, height: image.height, threshold, rings };
}

self.onmessage = (event: MessageEvent<TraceRequest>) => {
  const { id } = event.data;
  trace(event.data)
    .then((response) => self.postMessage(response))
    .catch((error: unknown) => {
      const message = error instanceof Error ? error.message : "圖片處理失敗";
      self.postMessage({ id, ok: false, message } satisfies TraceResponse);
    });
};
