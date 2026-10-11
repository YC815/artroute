import { describe, expect, it } from "vitest";
import { haversineM, polylineLengthM } from "@/lib/geo/haversine";

describe("haversineM", () => {
  it("沿經線 1 度 = 平均半徑 × π / 180（與引擎 geo.haversine_m 同一個半徑）", () => {
    expect(haversineM([121.5, 25], [121.5, 26])).toBeCloseTo((6_371_008.8 * Math.PI) / 180, 6);
  });

  it("沿緯線的距離乘上 cos(緯度)、對稱、同一點為 0", () => {
    const d = haversineM([121, 60], [121.001, 60]);
    expect(d).toBeCloseTo(((6_371_008.8 * Math.PI) / 180) * 0.001 * 0.5, 3);
    expect(haversineM([121.001, 60], [121, 60])).toBe(d);
    expect(haversineM([121.5, 25], [121.5, 25])).toBe(0);
  });
});

describe("polylineLengthM", () => {
  it("逐邊累加；少於 2 個點為 0", () => {
    const a: [number, number] = [121.5, 25];
    const b: [number, number] = [121.501, 25];
    const c: [number, number] = [121.501, 25.001];
    expect(polylineLengthM([a, b, c])).toBe(haversineM(a, b) + haversineM(b, c));
    expect(polylineLengthM([a])).toBe(0);
    expect(polylineLengthM([])).toBe(0);
  });
});
