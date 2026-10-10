import { describe, expect, it } from "vitest";
import { fromLocal, toLocal } from "@/lib/geo/local-plane";
import type { LonLat } from "@/lib/shape/types";

const TAIPEI: LonLat = [121.5654, 25.033];

describe("局部切平面", () => {
  it("往北 111195 m 約等於緯度 +1°", () => {
    const [lon, lat] = fromLocal(TAIPEI, 0, 111_195);
    expect(lon).toBeCloseTo(TAIPEI[0], 9);
    expect(lat).toBeCloseTo(TAIPEI[1] + 1, 3);
  });

  it("往東的公尺數依緯度縮放（cos 25.033°）", () => {
    const [lon] = fromLocal(TAIPEI, 100_000, 0);
    const expected = 100_000 / (6_371_008.8 * Math.cos((25.033 * Math.PI) / 180)) / (Math.PI / 180);
    expect(lon - TAIPEI[0]).toBeCloseTo(expected, 9);
  });

  it("toLocal 是 fromLocal 的反函數", () => {
    const p = fromLocal(TAIPEI, -1234.5, 678.9);
    const [east, north] = toLocal(TAIPEI, p);
    expect(east).toBeCloseTo(-1234.5, 6);
    expect(north).toBeCloseTo(678.9, 6);
  });
});
