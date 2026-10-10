import { MAX_IMAGE_BYTES } from "@/lib/limits";

export const IMAGE_TYPES = ["image/png", "image/jpeg", "image/webp"];

/** 上傳的圖片不能用的原因；可以用時回傳 null。 */
export function imageFileProblem(file: { type: string; size: number }): string | null {
  if (!IMAGE_TYPES.includes(file.type)) return "只支援 PNG、JPG、WebP 圖片";
  if (file.size > MAX_IMAGE_BYTES) return "圖片不能超過 5 MB";
  return null;
}
