import { readFile } from "node:fs/promises";
import { expect, test as base, type Page } from "@playwright/test";
import { matchRequestSchema, type MatchRequest, type MatchResponse } from "@/lib/engine/contract";
import { SAMPLE_MATCH_RESPONSE } from "@/lib/engine/fixtures";

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
const test = base.extend<{ allowedErrors: RegExp[]; guard: void }>({
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
const BLANK_STYLE = {
  version: 8,
  sources: {},
  layers: [{ id: "background", type: "background", paint: { "background-color": "#f1f5f9" } }],
};

// 路網版本故意和 /match 回應的 data_version（2026-10-10）不同日期：畫面上的日期必須來自吸附結果
const STATUS = { data: { build_id: "20260101T000000Z", osm_md5: "e2e", created_at: "2026-01-01T00:00:00Z" } };
const STATUS_DATE = "2026-01-01";
const MATCH_DATE = "2026-10-10";

/** 路線往北平移約 220 m（zoom 14 約 51 px）：目標圖形的 SVG 不會蓋住路線，畫出來的像素才數得到。 */
const NORTH_SHIFT_DEG = 0.002;
const HI_STROKES = 4;

function lengthM(points: [number, number][]): number {
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
function matchResponseFor(request: MatchRequest): MatchResponse {
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

/** route-layers.ts 的 route-stroke 圖層線色 #2563eb；容許反鋸齒與疊在目標圖形範圍（6% 紫色）下造成的色差。 */
const ROUTE_RGB = [0x25, 0x63, 0xeb] as const;
const COLOR_TOLERANCE = 12;
const MIN_ROUTE_PIXELS = 200;

/** 在另一個空白分頁解 PNG 並數出接近路線藍色的像素（不要動到被測的頁面）。 */
async function countRoutePixels(decoder: Page, png: Buffer): Promise<number> {
  return decoder.evaluate(
    async ({ base64, rgb, tolerance }) => {
      const image = new Image();
      image.src = `data:image/png;base64,${base64}`;
      await image.decode();
      const canvas = document.createElement("canvas");
      canvas.width = image.naturalWidth;
      canvas.height = image.naturalHeight;
      const ctx = canvas.getContext("2d")!;
      ctx.drawImage(image, 0, 0);
      const { data } = ctx.getImageData(0, 0, canvas.width, canvas.height);
      let count = 0;
      for (let i = 0; i < data.length; i += 4) {
        if (data[i + 3] === 255 && rgb.every((c, k) => Math.abs(data[i + k] - c) <= tolerance)) count += 1;
      }
      return count;
    },
    { base64: png.toString("base64"), rgb: [...ROUTE_RGB], tolerance: COLOR_TOLERANCE },
  );
}

/** 地圖 style 與 /api/status 換成固定內容；不連外、不需要引擎。 */
async function mockMapAndStatus(page: Page) {
  await page.route("https://tiles.openfreemap.org/**", (route) => route.fulfill({ json: BLANK_STYLE }));
  await page.route("**/api/status", (route) => route.fulfill({ json: STATUS }));
}

/** 再加上 /api/match：依請求的筆畫產生回應，順便驗證 app 送出的請求符合契約。 */
async function mockEngine(page: Page, onMatch?: (request: MatchRequest, response: MatchResponse) => void) {
  await mockMapAndStatus(page);
  await page.route("**/api/match", async (route) => {
    const request = matchRequestSchema.parse(route.request().postDataJSON());
    const response = matchResponseFor(request);
    onMatch?.(request, response);
    await route.fulfill({ json: response });
  });
}

test("文字 → 放到地圖 → 吸附 → 下載 GPX", async ({ page }) => {
  const seen: { hi?: MatchResponse } = {};
  await mockEngine(page, (request, response) => {
    if (request.strokes.length === HI_STROKES) seen.hi = response;
  });

  await page.goto("/");
  // 開頁時預設文字也會送一次吸附；這裡等的是「HI」那一次（futural：H 三筆、I 一筆，共 4 筆）
  const hiRequest = page.waitForRequest(
    (r) => r.url().endsWith("/api/match") && r.postDataJSON()?.strokes?.length === HI_STROKES,
  );
  // 用 role 找：Radix 的分頁面板也以「文字」為名（aria-labelledby），getByLabel 會撞到兩個
  await page.getByRole("textbox", { name: "文字" }).fill("HI");
  const request = matchRequestSchema.parse((await hiRequest).postDataJSON());

  // 每一筆各自是一條目標線
  await expect(page.getByTestId("target-stroke")).toHaveCount(HI_STROKES);

  // 吸附結果：地圖上的段落數（預設文字是 15 筆，等到 4 才代表「HI」的結果已套用）、路線長度、分數、路網日期
  await expect(page.getByTestId("map")).toHaveAttribute("data-route-segments", String(HI_STROKES));
  await expect(page.getByText("2.40 km")).toBeVisible();
  await expect(page.getByText("78%")).toBeVisible();
  await expect(page.getByText(MATCH_DATE)).toBeVisible();
  await expect(page.getByText(STATUS_DATE)).toHaveCount(0);
  // warnings 的部件在資訊面板列出
  await expect(page.getByText("第 3 筆：這一筆附近 150 m 內沒有可騎的道路")).toBeVisible();

  // 路線真的畫在地圖上：截圖數路線藍色的像素（data-route-segments 只是 prop，worker 壞了它照樣是 4）
  const decoder = await page.context().newPage();
  await expect
    .poll(async () => countRoutePixels(decoder, await page.getByTestId("map").screenshot()), {
      message: "地圖上沒有畫出路線（路線藍色像素太少）",
    })
    .toBeGreaterThan(MIN_ROUTE_PIXELS);

  // 送出的經緯度在預設位置（台北 101）附近
  const [lon, lat] = request.strokes[0].points[0];
  expect(lon).toBeGreaterThan(121.4);
  expect(lon).toBeLessThan(121.7);
  expect(lat).toBeGreaterThan(24.9);
  expect(lat).toBeLessThan(25.2);

  const response = seen.hi;
  if (!response) throw new Error("「HI」的 /api/match 沒有被 mock 處理");

  const button = page.getByRole("button", { name: "下載 GPX" });
  await expect(button).toBeEnabled();
  const downloadPromise = page.waitForEvent("download");
  await button.click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toBe("artroute-hi.gpx");
  const gpx = await readFile(await download.path(), "utf8");
  // GPX 只輸出 route.points，順序與座標都要和回應一致
  const expectedPoints = response.route.points.map(([x, y]) => `<trkpt lat="${y.toFixed(6)}" lon="${x.toFixed(6)}"/>`);
  expect(gpx.match(/<trkpt [^>]*\/>/g)).toEqual(expectedPoints);
});

/**
 * 在頁面裡用 canvas 畫一張 PNG：白底，blank 為 false 時畫一個黑色實心圓和一個中空的黑色方框
 * （外框 2 個、洞 1 個）。尺寸大於 MAX_SIDE（512），縮圖的路徑也會走到。
 */
async function makePng(page: Page, { width, height, blank }: { width: number; height: number; blank: boolean }) {
  const base64 = await page.evaluate(
    ({ width, height, blank }) => {
      const canvas = document.createElement("canvas");
      canvas.width = width;
      canvas.height = height;
      const ctx = canvas.getContext("2d")!;
      ctx.fillStyle = "#fff";
      ctx.fillRect(0, 0, width, height);
      if (!blank) {
        ctx.fillStyle = "#000";
        ctx.beginPath();
        ctx.arc(width * 0.3, height * 0.5, Math.min(width, height) * 0.25, 0, Math.PI * 2);
        ctx.fill();
        ctx.fillRect(width * 0.6, height * 0.25, width * 0.25, height * 0.5);
        ctx.fillStyle = "#fff";
        ctx.fillRect(width * 0.67, height * 0.4, width * 0.1, height * 0.2);
      }
      return canvas.toDataURL("image/png").split(",")[1];
    },
    { width, height, blank },
  );
  return Buffer.from(base64, "base64");
}

async function uploadImage(page: Page, name: string, buffer: Buffer) {
  await page.getByRole("tab", { name: "圖片" }).click();
  await page.locator("#source-image").setInputFiles({ name, mimeType: "image/png", buffer });
}

test("圖片 → 輪廓 → 以封閉筆畫吸附", async ({ page }) => {
  await mockEngine(page);
  await page.goto("/");
  // 開頁時預設文字（15 筆）也會送一次；圖片預設只選最大的一個外框，所以等的是「1 筆、封閉」那一次
  const imageRequest = page.waitForRequest((r) => {
    const strokes = r.url().endsWith("/api/match") ? r.postDataJSON()?.strokes : undefined;
    return strokes?.length === 1 && strokes[0].closed === true;
  });
  await uploadImage(page, "shapes.png", await makePng(page, { width: 1200, height: 800, blank: false }));

  const request = matchRequestSchema.parse((await imageRequest).postDataJSON());
  expect(request.strokes[0].closed).toBe(true);
  expect(request.strokes[0].points.length).toBeGreaterThanOrEqual(3);

  // worker 抽到輪廓、圖形放上地圖，吸附結果也畫出來
  await expect(page.getByRole("group", { name: "輪廓選取" }).locator("polygon").first()).toBeAttached();
  await expect(page.getByTestId("target-stroke").first()).toBeAttached();
  await expect(page.getByTestId("map")).toHaveAttribute("data-route-segments", "1");
  await expect(page.getByRole("button", { name: "下載 GPX" })).toBeEnabled();
});

test("圖片抽不到輪廓：顯示說明，調整後恢復", async ({ page }) => {
  await mockEngine(page);
  await page.goto("/");
  await uploadImage(page, "blank.png", await makePng(page, { width: 600, height: 400, blank: true }));

  await expect(page.getByText("找不到輪廓，試試調整門檻或反轉")).toBeVisible();
  // 反轉後整張白底變成前景，抽得到外框，說明消失、圖形出現
  await page.getByRole("switch", { name: "反轉（深色背景、淺色圖案）" }).click();
  await expect(page.getByText("找不到輪廓，試試調整門檻或反轉")).toHaveCount(0);
  await expect(page.getByTestId("target-stroke").first()).toBeAttached();
});

test("引擎離線：顯示橫幅、停用吸附，不送 /api/match", async ({ page, allowedErrors }) => {
  // 不 mock /api/status：走真的 standalone 代理，playwright.config.ts 把 ENGINE_URL 設成空字串，代理回 503
  allowedErrors.push(/status of 503.*\/api\/status/);
  const matchRequests: string[] = [];
  page.on("request", (r) => {
    if (r.url().endsWith("/api/match")) matchRequests.push(r.url());
  });
  await page.route("https://tiles.openfreemap.org/**", (route) => route.fulfill({ json: BLANK_STYLE }));
  const statusResponse = page.waitForResponse((r) => r.url().endsWith("/api/status"));
  await page.goto("/");

  const status = await statusResponse;
  expect(status.status()).toBe(503);
  expect((await status.json()).detail.code).toBe("engine_unreachable");
  await expect(page.getByText("路線引擎暫時無法使用")).toBeVisible();
  await expect(page.getByRole("button", { name: "重試" })).toBeVisible();
  // 圖形仍可編輯，但沒有送出任何吸附請求
  await expect(page.getByTestId("target-stroke").first()).toBeAttached();
  await page.waitForLoadState("networkidle");
  expect(matchRequests).toEqual([]);
  await expect(page.getByRole("button", { name: "下載 GPX" })).toBeDisabled();
});
