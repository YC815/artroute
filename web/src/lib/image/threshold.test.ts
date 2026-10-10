import { describe, expect, it } from "vitest";
import { binaryMask, otsuThreshold, toGray } from "@/lib/image/threshold";

describe("toGray", () => {
  it("依亮度加權轉灰階，透明當成白底", () => {
    const rgba = new Uint8ClampedArray([
      255, 255, 255, 255, // 白
      0, 0, 0, 255, // 黑
      255, 0, 0, 255, // 紅：0.299 × 255 ≈ 76
      0, 0, 0, 0, // 全透明 → 白
    ]);
    expect(Array.from(toGray(rgba))).toEqual([255, 0, 76, 255]);
  });
});

describe("otsuThreshold", () => {
  it("雙峰影像：門檻落在兩峰之間", () => {
    const gray = new Uint8Array([30, 30, 30, 30, 220, 220, 220, 220, 220, 220]);
    const t = otsuThreshold(gray);
    expect(t).toBeGreaterThanOrEqual(30);
    expect(t).toBeLessThan(220);
  });

  it("單一顏色時回傳 0", () => {
    expect(otsuThreshold(new Uint8Array([128, 128, 128]))).toBe(0);
  });
});

describe("binaryMask", () => {
  const gray = new Uint8Array([10, 100, 200]);

  it("比門檻暗（≤）的是前景", () => {
    expect(binaryMask(gray, 100, false)).toEqual([1, 1, 0]);
  });

  it("invert 時反過來", () => {
    expect(binaryMask(gray, 100, true)).toEqual([0, 0, 1]);
  });
});
