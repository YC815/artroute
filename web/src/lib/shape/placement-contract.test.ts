import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { transformPoint } from "@/lib/shape/transform";
import type { LonLat, Point } from "@/lib/shape/types";

// 引擎的自動搜尋用 engine/src/engine/search/geometry.py 擺放圖形、回傳 placement；網站拿它當 Transform 重新送 /match。
// 兩邊算出的經緯度必須相同，否則網站重新吸附的路線不是搜尋評過分的那條。這份案例由引擎產生，兩邊的測試都對它比對。
const CASES = new URL("../../../../engine/tests/data/placement_cases.json", import.meta.url);

type Case = {
  placement: { center: LonLat; size_m: number; rotation_deg: number };
  points: Point[];
  lonlat: LonLat[];
};

describe("transformPoint 與引擎的 placement 一致", () => {
  const cases: Case[] = JSON.parse(readFileSync(fileURLToPath(CASES), "utf8"));

  it("有案例", () => {
    expect(cases.length).toBeGreaterThan(0);
  });

  it.each(cases.map((c, i) => [i, c] as const))("案例 %i", (_, c) => {
    const t = { center: c.placement.center, sizeM: c.placement.size_m, rotationDeg: c.placement.rotation_deg };
    c.points.forEach((p, k) => {
      const [lon, lat] = transformPoint(p, t);
      // 三角函數的實作（V8 與 glibc）可能差 1 ulp：容許 1e-11 度（約 1 µm）
      expect(Math.abs(lon - c.lonlat[k][0])).toBeLessThan(1e-11);
      expect(Math.abs(lat - c.lonlat[k][1])).toBeLessThan(1e-11);
    });
  });
});
