/** RGBA → 灰階（0 黑、255 白）。透明的部分當成白底。 */
export function toGray(rgba: Uint8ClampedArray): Uint8Array {
  const gray = new Uint8Array(rgba.length / 4);
  for (let i = 0; i < gray.length; i++) {
    const luminance = 0.299 * rgba[i * 4] + 0.587 * rgba[i * 4 + 1] + 0.114 * rgba[i * 4 + 2];
    const alpha = rgba[i * 4 + 3] / 255;
    gray[i] = Math.round(luminance * alpha + 255 * (1 - alpha));
  }
  return gray;
}

/** Otsu 門檻：讓「≤ 門檻」與「> 門檻」兩群的組間變異最大。 */
export function otsuThreshold(gray: Uint8Array): number {
  const histogram = new Array<number>(256).fill(0);
  for (const value of gray) histogram[value]++;
  const total = gray.length;
  const sumAll = histogram.reduce((sum, count, value) => sum + count * value, 0);
  let best = { threshold: 0, variance: -1 };
  let weightBelow = 0;
  let sumBelow = 0;
  for (let t = 0; t < 256; t++) {
    weightBelow += histogram[t];
    sumBelow += t * histogram[t];
    const weightAbove = total - weightBelow;
    if (weightBelow === 0 || weightAbove === 0) continue;
    const meanGap = sumBelow / weightBelow - (sumAll - sumBelow) / weightAbove;
    const variance = weightBelow * weightAbove * meanGap * meanGap;
    if (variance > best.variance) best = { threshold: t, variance };
  }
  return best.threshold;
}

/** 二值化：預設「比門檻暗（≤）」是前景 1；invert 時反過來（亮底深圖之外的情況，例如黑底白圖）。 */
export function binaryMask(gray: Uint8Array, threshold: number, invert: boolean): number[] {
  return Array.from(gray, (value) => ((value <= threshold) !== invert ? 1 : 0));
}
