// 圖片解碼的尺寸防線：絕不產生長邊超過 MAX_SIDE 的 bitmap（防解壓縮炸彈）。
// 5 MB 的 PNG 可以解出每邊數萬像素，所以不能先完整解碼再縮。

/** 描邊用的影像長邊上限（像素）。輪廓的洞歸屬運算是超線性的（1024 px 的雜訊圖約 26 秒），不能拿掉或調高。 */
export const MAX_SIDE = 512;

/** createImageBitmap 回傳值中這裡用到的部分。 */
export type DecodedBitmap = { width: number; height: number; close(): void };

/** createImageBitmap 的形狀；抽成參數是為了在 node 裡用假的測試。 */
export type BitmapFactory<B extends DecodedBitmap> = (file: Blob, options: ImageBitmapOptions) => Promise<B>;

const OVERSIZE_MESSAGE = "圖片尺寸超出處理上限";

/**
 * 解碼並縮到長邊 ≤ MAX_SIDE。事先不知道方向，所以先用 resizeWidth 解一次；
 * 高度超過上限（直圖）才關掉它，改用 resizeHeight 重新解碼。
 * 回傳的 bitmap 由呼叫端關閉；瀏覽器忽略縮放選項而回傳大圖時，關掉並丟錯，不讓大圖流到後面。
 */
export async function decodeWithinLimit<B extends DecodedBitmap>(file: Blob, create: BitmapFactory<B>): Promise<B> {
  let bitmap = await create(file, { resizeWidth: MAX_SIDE, resizeQuality: "high" });
  if (bitmap.height > MAX_SIDE) {
    bitmap.close();
    bitmap = await create(file, { resizeHeight: MAX_SIDE, resizeQuality: "high" });
  }
  if (bitmap.width > MAX_SIDE || bitmap.height > MAX_SIDE) {
    bitmap.close();
    throw new Error(OVERSIZE_MESSAGE);
  }
  return bitmap;
}
