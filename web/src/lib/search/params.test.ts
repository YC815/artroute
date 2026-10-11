import { describe, expect, it } from "vitest";
import { searchRequestSchema } from "@/lib/engine/contract";
import { MAX_SEARCH_KM, MIN_SEARCH_KM } from "@/lib/limits";
import {
  DEFAULT_RADIUS_M,
  buildSearchRequest,
  defaultKmRange,
  defaultRotationSpan,
  kmRangeProblem,
  parseKm,
  placementToTransform,
  progressPercent,
  searchBlocker,
  startDistance,
} from "@/lib/search/params";
import { transformPoint, MAX_SIZE_M, MIN_SIZE_M } from "@/lib/shape/transform";
import type { Shape } from "@/lib/shape/types";
import { fromLocal } from "@/lib/geo/local-plane";

const shape: Shape = {
  kind: "text",
  strokes: [
    { points: [[-0.5, 0.1], [0.5, -0.1]], closed: false },
    { points: [[0, 0], [0.2, 0.2], [0.1, 0.3]], closed: true },
  ],
};
const AREA = { center: [121.5654, 25.033] as [number, number], radius_m: DEFAULT_RADIUS_M };

describe("defaultKmRange", () => {
  it("約為目標長度的 1.2 與 2.5 倍（路線含連接線，比目標長），取到 0.1 km", () => {
    expect(defaultKmRange(10_000)).toEqual({ min: 12, max: 25 });
    expect(defaultKmRange(7_340)).toEqual({ min: 8.8, max: 18.4 });
  });

  it("夾在 1–300 km 內，最大至少比最小多 1 km", () => {
    expect(defaultKmRange(300)).toEqual({ min: MIN_SEARCH_KM, max: 2 });
    expect(defaultKmRange(200_000)).toEqual({ min: 240, max: MAX_SEARCH_KM });
    expect(defaultKmRange(400_000)).toEqual({ min: MAX_SEARCH_KM, max: MAX_SEARCH_KM });
  });

  it("產生的範圍一定通過 searchRequestSchema", () => {
    for (const target of [0, 1, 500, 5_000, 123_456, 1e7]) {
      const km = defaultKmRange(target);
      const request = buildSearchRequest(shape, AREA, km, 15);
      expect(searchRequestSchema.safeParse(request).success).toBe(true);
    }
  });
});

describe("defaultRotationSpan", () => {
  it("文字 ±15°（保持可讀）、圖片不限（±180°）", () => {
    expect(defaultRotationSpan("text")).toBe(15);
    expect(defaultRotationSpan("image")).toBe(180);
  });
});

describe("parseKm／kmRangeProblem", () => {
  it("1–300 的有限數字才算", () => {
    expect(parseKm("12.5")).toBe(12.5);
    expect(parseKm(" 3 ")).toBe(3);
    expect(parseKm("")).toBeNull();
    expect(parseKm("abc")).toBeNull();
    expect(parseKm("0.5")).toBeNull();
    expect(parseKm("301")).toBeNull();
    expect(parseKm("Infinity")).toBeNull();
  });

  it("範圍的問題（給使用者看）", () => {
    expect(kmRangeProblem(5, 10)).toBeNull();
    expect(kmRangeProblem(5, 5)).toBeNull();
    expect(kmRangeProblem(null, 10)).toBe("公里數要介於 1–300");
    expect(kmRangeProblem(12, 10)).toBe("最小公里數不能大於最大公里數");
  });
});

describe("buildSearchRequest", () => {
  it("正規化筆畫原樣送出，旋轉範圍是 ±span，通過契約", () => {
    const request = buildSearchRequest(shape, { center: [121.5, 25.05], radius_m: 4500 }, { min: 3, max: 9 }, 30);
    expect(request).toEqual({
      strokes: shape.strokes.map((s) => ({ points: s.points, closed: s.closed })),
      center: [121.5, 25.05],
      radius_m: 4500,
      min_km: 3,
      max_km: 9,
      rotation_min_deg: -30,
      rotation_max_deg: 30,
    });
    expect(searchRequestSchema.parse(request)).toEqual(request);
  });
});

describe("placementToTransform", () => {
  it("與 Transform 同一套約定：center、size_m → sizeM、rotation_deg → rotationDeg", () => {
    const placement = { center: [121.55, 25.04] as [number, number], size_m: 1500, rotation_deg: 350 };
    const t = placementToTransform(placement);
    expect(t).toEqual({ center: [121.55, 25.04], sizeM: 1500, rotationDeg: 350 });
    // 擺放後的點與引擎同一個公式（transformPoint）：右邊的點在順時針 350° 後偏北
    const [lon, lat] = transformPoint([0.5, 0], t);
    expect(lon).toBeGreaterThan(121.55);
    expect(lat).toBeGreaterThan(25.04);
  });

  it("大小夾在編輯器的範圍內（存檔才讀得回來）", () => {
    const at = (size_m: number) => placementToTransform({ center: [121.5, 25], size_m, rotation_deg: 0 }).sizeM;
    expect(at(50)).toBe(MIN_SIZE_M);
    expect(at(80_000)).toBe(MAX_SIZE_M);
  });
});

describe("progressPercent", () => {
  it("三個階段依序占 0–40、40–55、55–100", () => {
    expect(progressPercent("coarse", 0, 10)).toBe(0);
    expect(progressPercent("coarse", 5, 10)).toBe(20);
    expect(progressPercent("refine", 0, 30)).toBe(40);
    expect(progressPercent("refine", 30, 30)).toBe(55);
    expect(progressPercent("match", 5, 10)).toBe(78);
    expect(progressPercent("match", 10, 10)).toBe(100);
  });

  it("total 為 0 或 done 超出時不會 NaN 或超出階段", () => {
    expect(progressPercent("match", 0, 0)).toBe(55);
    expect(progressPercent("coarse", 15, 10)).toBe(40);
    expect(progressPercent("coarse", -1, 10)).toBe(0);
  });
});

describe("searchBlocker", () => {
  const ok = { hasShape: true, problem: null, engineStatus: "ok" as const, searchReady: true };

  it("都沒問題 → null", () => {
    expect(searchBlocker(ok)).toBeNull();
  });

  it("依序說明不能搜尋的原因", () => {
    expect(searchBlocker({ ...ok, hasShape: false })).toBe("先輸入文字或上傳圖片");
    expect(searchBlocker({ ...ok, problem: "有筆畫超過 2000 點" })).toBe("圖形不能搜尋：有筆畫超過 2000 點");
    expect(searchBlocker({ ...ok, engineStatus: "checking" })).toBe("檢查引擎中，稍後才能搜尋");
    expect(searchBlocker({ ...ok, engineStatus: "down" })).toBe("引擎離線中，不能搜尋");
    expect(searchBlocker({ ...ok, searchReady: false })).toBe(
      "目前的路網沒有自動搜尋需要的資料（要重建路網），仍可手動擺放",
    );
  });
});

describe("startDistance", () => {
  it("路線起點離圓心的距離；超過半徑算圈外", () => {
    const inside = fromLocal(AREA.center, 300, 400);
    expect(startDistance(AREA, inside).distanceM).toBeCloseTo(500, 6);
    expect(startDistance(AREA, inside).outside).toBe(false);
    const outside = fromLocal(AREA.center, 0, 3001);
    expect(startDistance(AREA, outside)).toEqual({ distanceM: expect.closeTo(3001, 6), outside: true });
  });

  it("剛好在圓周上算圈內", () => {
    expect(startDistance(AREA, fromLocal(AREA.center, AREA.radius_m, 0)).outside).toBe(false);
  });
});
