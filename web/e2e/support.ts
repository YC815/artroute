import { expect, test as base, type Page } from "@playwright/test";
import type { MatchRequest, MatchResponse } from "@/lib/engine/contract";
import { SAMPLE_MATCH_RESPONSE } from "@/lib/engine/fixtures";

// 兩個 spec 檔共用的守門員與假引擎的基本零件（smoke.spec.ts、edit.spec.ts）。
const LOCAL_HOST = "127.0.0.1";

/**
 * 每個測試都套用的守門員：
 * - 頁面的 pageerror 與 console.error 必須是空的（例如 MapLibre 的 worker 載入失敗只會 console.error，
 *   地圖照樣有 style，圖形編輯器也照樣出現，光看畫面斷言抓不到）。
 * - 不連外：沒被測試 mock 的 http(s) 請求（本機以外）一律中止並記錄，測試結束時必須是空的。
 * - 刻意製造 HTTP 錯誤的測試（例如引擎離線時真的收到 503）：Chromium 會把 4xx／5xx 的回應記成 console.error，
 *   測試用 allowedErrors 登記預期的那一則（比對格式化後的整行文字），其餘照樣不准出現。
 * 斷言放在 fixture 收尾：測試本身先失敗時，這裡的失敗訊息也會一起列出來。
 */
export const test = base.extend<{ allowedErrors: RegExp[]; guard: void }>({
  // 每個測試各自一份空陣列（Playwright 要求 fixture 的第一個參數是解構）。用 tuple 寫法：
  // 直接寫成 allowedErrors: async (...) 的話，react-hooks 規則會把裡面的 use() 誤判成 Hook。
  allowedErrors: [
    async ({}, use) => {
      await use([]);
    },
    { scope: "test" },
  ],
  guard: [
    async ({ page, allowedErrors }, use) => {
      const errors: string[] = [];
      const external: string[] = [];
      page.on("pageerror", (error) => errors.push(`pageerror: ${error.message}`));
      page.on("console", (message) => {
        if (message.type() === "error") errors.push(`console.error: ${message.text()} (${message.location().url})`);
      });
      // 最先註冊 = 最後比對（Playwright 以相反順序比對 route）：測試裡的 mock 優先，漏網的才落到這裡
      await page.route(/^https?:\/\//, async (route) => {
        const url = route.request().url();
        if (new URL(url).hostname === LOCAL_HOST) return route.continue();
        external.push(url);
        return route.abort();
      });
      await use();
      expect(external, "有請求連到外網（沒有被 mock）").toEqual([]);
      const unexpected = errors.filter((line) => !allowedErrors.some((allowed) => allowed.test(line)));
      expect(unexpected, "頁面出現 pageerror 或 console.error").toEqual([]);
    },
    { auto: true },
  ],
});

// 不連外：OpenFreeMap 的 style 換成只有背景的空白 style（路線圖層只用 icon，不需要 glyphs）
export const BLANK_STYLE = {
  version: 8,
  sources: {},
  layers: [{ id: "background", type: "background", paint: { "background-color": "#f1f5f9" } }],
};

// 路網版本故意和 /match 回應的 data_version（2026-10-10）不同日期：畫面上的日期必須來自吸附結果
export const STATUS = { data: { build_id: "20260101T000000Z", osm_md5: "e2e", created_at: "2026-01-01T00:00:00Z" } };

/** 路線往北平移約 220 m（zoom 14 約 51 px）：目標圖形的 SVG 不會蓋住路線，畫出來的像素才數得到。 */
export const NORTH_SHIFT_DEG = 0.002;

export function lengthM(points: [number, number][]): number {
  return points.slice(1).reduce((sum, [lon, lat], i) => {
    const [prevLon, prevLat] = points[i];
    const dx = (lon - prevLon) * 111_320 * Math.cos((lat * Math.PI) / 180);
    const dy = (lat - prevLat) * 110_540;
    return sum + Math.hypot(dx, dy);
  }, 0);
}

/**
 * 依請求的筆畫產生回應：每一筆一段 stroke 路線（座標來自請求，往北平移），所以路線一定落在畫面內。
 * 長度、分數、warnings、data_version 沿用固定的 fixture，資訊面板的斷言維持固定值。
 */
export function matchResponseFor(request: MatchRequest): MatchResponse {
  const segments = request.strokes.map((stroke, i) => {
    const points = stroke.points.map(([lon, lat]): [number, number] => [lon, lat + NORTH_SHIFT_DEG]);
    return { kind: "stroke" as const, stroke_index: i, points, length_m: lengthM(points) };
  });
  return {
    ...SAMPLE_MATCH_RESPONSE,
    route: { points: segments.flatMap((s) => s.points), length_m: SAMPLE_MATCH_RESPONSE.route.length_m },
    segments,
  };
}

/** 地圖 style 與 /api/status 換成固定內容；不連外、不需要引擎。status 預設是舊版引擎（沒有 search_ready）。 */
export async function mockMapAndStatus(page: Page, status: object = STATUS) {
  await page.route("https://tiles.openfreemap.org/**", (route) => route.fulfill({ json: BLANK_STYLE }));
  await page.route("**/api/status", (route) => route.fulfill({ json: status }));
}
