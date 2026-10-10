// 圖片描邊的 Web Worker：解碼、縮圖、灰階、Otsu、抽輪廓都在這裡做，不卡住畫面。
// 解碼後的灰階影像留在 worker 裡，調門檻時不用重新解碼；請求的排隊與狀態在 tracer.ts。
import { decodeWithinLimit } from "@/lib/image/decode";
import { createTracer, type GrayImage, type TraceRequest } from "@/lib/image/tracer";
import { toGray } from "@/lib/image/threshold";

export type { TraceRequest, TraceResponse } from "@/lib/image/tracer";

/** 瀏覽器的解碼錯誤訊息是英文，換成給使用者看的說明。 */
function createBitmap(file: Blob, options: ImageBitmapOptions): Promise<ImageBitmap> {
  return createImageBitmap(file, options).catch(() => {
    throw new Error("無法讀取這張圖片（檔案可能已損毀，或尺寸過大）");
  });
}

async function decode(file: Blob): Promise<GrayImage> {
  // 走到 canvas 的 bitmap 長邊已 ≤ MAX_SIDE（縮小的是輸出；解碼本身可能仍先解出原始尺寸，見 decode.ts 開頭）
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

const trace = createTracer(decode);

self.onmessage = (event: MessageEvent<TraceRequest>) => {
  void trace(event.data).then((response) => self.postMessage(response));
};
