import { readFile } from "node:fs/promises";
import { expect, type Page } from "@playwright/test";
import {
  matchRequestSchema,
  searchRequestSchema,
  type MatchRequest,
  type MatchResponse,
  type SearchCandidate,
  type SearchEvent,
} from "@/lib/engine/contract";
import { SAMPLE_MATCH_RESPONSE, sampleCandidate, sampleResult, toSse } from "@/lib/engine/fixtures";
import { defaultKmRange, placementToTransform } from "@/lib/search/params";
import { shapeLengthM, transformStrokes, type Transform } from "@/lib/shape/transform";
import { BLANK_STYLE, NORTH_SHIFT_DEG, STATUS, lengthM, matchResponseFor, mockMapAndStatus, test } from "./support";

const STATUS_DATE = "2026-01-01";
const MATCH_DATE = "2026-10-10";

const HI_STROKES = 4;

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

/** 再加上 /api/match：依請求的筆畫產生回應，順便驗證 app 送出的請求符合契約。 */
async function mockEngine(
  page: Page,
  onMatch?: (request: MatchRequest, response: MatchResponse) => void,
  status: object = STATUS,
) {
  await mockMapAndStatus(page, status);
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

test("吸附失敗（504）：錯誤旁有「重新吸附」，按了重送同一個請求並畫出路線", async ({ page, allowedErrors }) => {
  allowedErrors.push(/status of 504.*\/api\/match/);
  await mockMapAndStatus(page);
  const bodies: string[] = [];
  await page.route("**/api/match", async (route) => {
    bodies.push(route.request().postData() ?? "");
    if (bodies.length === 1) {
      return route.fulfill({
        status: 504,
        json: { detail: { code: "timeout", message: "吸附超過時間上限，請縮小圖形或減少筆畫" } },
      });
    }
    await route.fulfill({ json: matchResponseFor(matchRequestSchema.parse(route.request().postDataJSON())) });
  });

  await page.goto("/");
  // 預設文字的第一次吸附就逾時：顯示錯誤，沒有路線，不能下載；引擎沒有離線，橫幅不出現
  await expect(page.getByText("吸附超過時間上限，請縮小圖形或減少筆畫")).toBeVisible();
  await expect(page.getByTestId("map")).toHaveAttribute("data-route-segments", "0");
  await expect(page.getByRole("button", { name: "下載 GPX" })).toBeDisabled();
  await expect(page.getByText("路線引擎暫時無法使用")).toHaveCount(0);

  await page.getByRole("button", { name: "重新吸附" }).click();
  await expect(page.getByTestId("map")).not.toHaveAttribute("data-route-segments", "0");
  await expect(page.getByText("吸附超過時間上限，請縮小圖形或減少筆畫")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "重新吸附" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "下載 GPX" })).toBeEnabled();
  // 重送的是同一個請求，而且只多送一次
  expect(bodies).toHaveLength(2);
  expect(bodies[1]).toBe(bodies[0]);
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
  expect(status.headers()["x-content-type-options"]).toBe("nosniff");
  await expect(page.getByText("路線引擎暫時無法使用")).toBeVisible();
  await expect(page.getByRole("button", { name: "重試" })).toBeVisible();
  // 圖形仍可編輯，但沒有送出任何吸附請求
  await expect(page.getByTestId("target-stroke").first()).toBeAttached();
  await page.waitForLoadState("networkidle");
  expect(matchRequests).toEqual([]);
  await expect(page.getByRole("button", { name: "下載 GPX" })).toBeDisabled();
});

/** studio.tsx 的預設擺放（台北 101、長邊 2 km）；新的瀏覽器 context 沒有存檔，一定從這裡開始。 */
const DEFAULT_TRANSFORM: Transform = { center: [121.5654, 25.033], sizeM: 2000, rotationDeg: 0 };
/** 搜尋結果的第 2 名：擺在預設位置的西北方、轉 20°、比預設小，公里數不符。 */
const SECOND: SearchCandidate = sampleCandidate({
  placement: { center: [121.55, 25.045], size_m: 1500, rotation_deg: 20 },
  start_distance_m: 1830,
  length_ok: false,
});
const NOTICE = "沒有完全符合公里數的候選，列出最接近的幾個";

function closeTo(actual: number[][][], expected: number[][][], tolerance: number): boolean {
  return (
    actual.length === expected.length &&
    actual.every(
      (stroke, i) =>
        stroke.length === expected[i].length &&
        stroke.every(([x, y], k) => Math.abs(x - expected[i][k][0]) < tolerance && Math.abs(y - expected[i][k][1]) < tolerance),
    )
  );
}

test("自動搜尋：設定範圍 → 搜尋（SSE）→ 套用第 2 名 → 吸附帶 start_circle → 下載 GPX", async ({ page }) => {
  const matches: MatchRequest[] = [];
  await mockEngine(page, (request) => matches.push(request), { ...STATUS, search_ready: true });
  const events: SearchEvent[] = [
    { type: "queued", position: 1 },
    { type: "progress", stage: "coarse", done: 10, total: 10 },
    { type: "best", candidate: sampleCandidate() },
    { type: "progress", stage: "match", done: 4, total: 10 },
    sampleResult([sampleCandidate(), SECOND], NOTICE),
  ];
  // 引擎每 15 秒的 keepalive 註解行也夾在裡面
  const body = events.map(toSse).join(": ping\n\n");
  const searches: unknown[] = [];
  await page.route("**/api/search", async (route) => {
    searches.push(route.request().postDataJSON());
    await route.fulfill({ status: 200, headers: { "content-type": "text/event-stream" }, body });
  });

  await page.goto("/");
  // 開頁時的第一次吸附（預設文字）還沒有範圍：不帶 start_circle
  await expect.poll(() => matches.length).toBeGreaterThan(0);
  expect(matches[0].start_circle).toBeUndefined();

  // 設定範圍：圓心是目前圖形的中心、半徑 3 km；地圖畫出圓，即時吸附改帶 start_circle 重送
  const circleMatch = page.waitForRequest((r) => r.url().endsWith("/api/match") && r.postDataJSON()?.start_circle);
  await page.getByRole("button", { name: "設定搜尋範圍" }).click();
  const area = { center: DEFAULT_TRANSFORM.center, radius_m: 3000 };
  await expect(page.getByTestId("map")).toHaveAttribute("data-search-area", "121.5654,25.033,3000");
  const before = matchRequestSchema.parse((await circleMatch).postDataJSON());
  expect(before.start_circle).toEqual(area);

  const searchRequest = page.waitForRequest((r) => r.url().endsWith("/api/search"));
  await page.getByRole("button", { name: "開始搜尋" }).click();
  const sent = searchRequestSchema.parse((await searchRequest).postDataJSON());
  // 圖形用正規化座標：照預設擺放轉成經緯度，必須和剛才送去吸附的筆畫一模一樣
  const placed = transformStrokes(sent.strokes, DEFAULT_TRANSFORM);
  expect(closeTo(placed.map((s) => s.points), before.strokes.map((s) => s.points), 1e-9)).toBe(true);
  expect(sent.strokes.map((s) => s.closed)).toEqual(before.strokes.map((s) => s.closed));
  expect(sent.strokes.flatMap((s) => s.points.flat()).every((v) => Math.abs(v) <= 0.5)).toBe(true);
  expect(sent.center).toEqual(area.center);
  expect(sent.radius_m).toBe(3000);
  // 公里數預設：目前目標長度的 1.2–2.5 倍；文字的旋轉範圍預設 ±15°
  const km = defaultKmRange(shapeLengthM(sent.strokes, DEFAULT_TRANSFORM.sizeM));
  expect([sent.min_km, sent.max_km]).toEqual([km.min, km.max]);
  expect([sent.rotation_min_deg, sent.rotation_max_deg]).toEqual([-15, 15]);

  // 結果：前 2 名、notice、公里數不符的標記；預覽在結束後清掉
  const list = page.getByRole("list", { name: "搜尋結果" });
  await expect(list.getByRole("listitem")).toHaveCount(2);
  await expect(page.getByText(NOTICE)).toBeVisible();
  await expect(list.getByRole("listitem").nth(1).getByText("公里數不符")).toBeVisible();
  await expect(list.getByRole("listitem").nth(0).getByText("公里數不符")).toHaveCount(0);
  await expect(page.getByTestId("map")).toHaveAttribute("data-search-preview", "0");
  expect(searches).toHaveLength(1);

  // 套用第 2 名：圖形擺到候選的位置，即時吸附帶著 start_circle 重送
  const applied = placementToTransform(SECOND.placement);
  const expectedStrokes = transformStrokes(sent.strokes, applied).map((s) => s.points);
  const appliedMatch = page.waitForRequest((r) => {
    if (!r.url().endsWith("/api/match")) return false;
    const strokes = (r.postDataJSON() as MatchRequest).strokes.map((s) => s.points);
    return closeTo(strokes, expectedStrokes, 1e-9);
  });
  await page.getByRole("button", { name: /第 2 名/ }).click();
  const after = matchRequestSchema.parse((await appliedMatch).postDataJSON());
  expect(after.start_circle).toEqual(area);
  await expect(page.getByRole("button", { name: /第 2 名/ })).toHaveAttribute("aria-pressed", "true");

  // 吸附結果套用後：起點離圓心一列出現（模擬的路線起點在圈內），可以下載
  await expect(page.getByText("起點離圓心", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "下載 GPX" })).toBeEnabled();

  // 清除範圍：圓消失，之後的吸附不再帶 start_circle
  const plainMatch = page.waitForRequest((r) => r.url().endsWith("/api/match") && !r.postDataJSON()?.start_circle);
  await page.getByRole("button", { name: "清除範圍" }).click();
  await expect(page.getByTestId("map")).toHaveAttribute("data-search-area", "");
  expect(matchRequestSchema.parse((await plainMatch).postDataJSON()).start_circle).toBeUndefined();
  await expect(page.getByText("起點離圓心", { exact: true })).toHaveCount(0);
});

test("引擎沒有回報 search_ready（舊版引擎）：自動搜尋停用並說明，吸附照常", async ({ page }) => {
  const searches: string[] = [];
  page.on("request", (r) => {
    if (r.url().endsWith("/api/search")) searches.push(r.url());
  });
  await mockEngine(page);
  await page.goto("/");
  await expect(page.getByText("目前的路網沒有自動搜尋需要的資料（要重建路網），仍可手動擺放")).toBeVisible();
  await expect(page.getByRole("button", { name: "設定搜尋範圍" })).toBeDisabled();
  await expect(page.getByRole("button", { name: "開始搜尋" })).toHaveCount(0);
  // 吸附不受影響
  await expect(page.getByRole("button", { name: "下載 GPX" })).toBeEnabled();
  expect(searches).toEqual([]);
});

test("隱藏原圖：按鈕與 H 切換目標線，路線與把手照常", async ({ page }) => {
  await mockEngine(page);
  await page.goto("/");
  const targets = page.getByTestId("target-stroke");
  const map = page.getByTestId("map");
  const frame = page.getByRole("group", { name: "目標圖形，方向鍵微調位置" }).locator("polygon");
  const textbox = page.getByRole("textbox", { name: "文字" });
  // 用「HI」（4 筆）：後面要在輸入框多打一個 h，預設文字已經 10 個字，再打就超過上限、圖形消失
  await textbox.fill("HI");
  await textbox.blur();
  // 預設顯示：目標線都在；等「HI」的吸附結果套用、路線畫出來。
  // 隱藏是不畫目標線，所以數 DOM 裡的數量（「I」這種直線的外框寬度是 0，toBeVisible 會判成看不見）
  const strokeCount = HI_STROKES;
  await expect(targets).toHaveCount(strokeCount);
  await expect(map).toHaveAttribute("data-route-segments", String(HI_STROKES));
  await expect(page.getByRole("button", { name: "隱藏原圖" })).toHaveAttribute("aria-pressed", "false");

  const decoder = await page.context().newPage();
  const expectHidden = async () => {
    await expect(targets).toHaveCount(0);
    await expect(page.getByRole("button", { name: "隱藏原圖" })).toHaveAttribute("aria-pressed", "true");
    // 路線（MapLibre 圖層）照常畫：截圖數路線藍色的像素；外框與把手照常在
    await expect(map).not.toHaveAttribute("data-route-segments", "0");
    await expect
      .poll(async () => countRoutePixels(decoder, await map.screenshot()), {
        message: "隱藏原圖時地圖上沒有畫出路線（路線藍色像素太少）",
      })
      .toBeGreaterThan(MIN_ROUTE_PIXELS);
    await expect(frame).toBeVisible();
    await expect(page.getByTestId("handle-rotate")).toBeVisible();
    await expect(page.getByTestId("handle-scale")).toHaveCount(4);
  };
  const expectShown = async () => {
    await expect(targets).toHaveCount(strokeCount);
    await expect(targets.first()).toBeAttached();
    await expect(page.getByRole("button", { name: "隱藏原圖" })).toHaveAttribute("aria-pressed", "false");
  };

  // 按鈕：隱藏 → 再按恢復
  await page.getByRole("button", { name: "隱藏原圖" }).click();
  await expectHidden();
  await page.getByRole("button", { name: "隱藏原圖" }).click();
  await expectShown();

  // 鍵盤 H（大小寫都可）：焦點在按鈕上（不是輸入框）時切換
  await page.keyboard.press("h");
  await expectHidden();
  await page.keyboard.press("Shift+H");
  await expectShown();
  // 帶修飾鍵不攔
  await page.keyboard.press("Alt+h");
  await expectShown();

  // 焦點在文字輸入框時不攔：h 打進文字裡，目標線照常顯示；刪掉後恢復原本的文字
  await textbox.press("End");
  await textbox.press("h");
  await expect(textbox).toHaveValue("HIh");
  await expect(page.getByRole("button", { name: "隱藏原圖" })).toHaveAttribute("aria-pressed", "false");
  await expect(targets.first()).toBeAttached();
  await textbox.press("Backspace");
  await textbox.blur();
  await expectShown();

  // 隱藏時把手照常可用：拖曳角落把手放大，外框跟著變
  await page.keyboard.press("h");
  await expectHidden();
  const before = await frame.getAttribute("points");
  const box = await page.getByTestId("handle-scale").first().boundingBox();
  if (!box) throw new Error("找不到縮放把手");
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width / 2 + 40, box.y + box.height / 2 - 40, { steps: 5 });
  await page.mouse.up();
  await expect(frame).not.toHaveAttribute("points", before ?? "");
  await expect(targets).toHaveCount(0);
});

// ---- 放開後的描形版與版本切換 ----

const PLANNER_STATUS = { ...STATUS, planner_ready: true };
const REFINE_FAILED_NOTICE = "描形版失敗，顯示快速版";

/**
 * 描形版的模擬路線：和 matchResponseFor 不同的一條（往北平移更多），每一筆之間再加一段連接線，
 * 所以 N 筆的 fast 是 N 段、fine 是 2N − 1 段，看 data-route-segments 就分得出畫面上是哪一條。
 */
function fineResponseFor(request: MatchRequest): MatchResponse {
  const strokes = request.strokes.map((stroke, i) => {
    const points = stroke.points.map(([lon, lat]): [number, number] => [lon, lat + NORTH_SHIFT_DEG * 1.5]);
    return { kind: "stroke" as const, stroke_index: i, points, length_m: lengthM(points) };
  });
  const segments = strokes.flatMap((segment, i) => {
    const next = strokes[i + 1];
    if (!next) return [segment];
    const points: [number, number][] = [segment.points[segment.points.length - 1], next.points[0]];
    return [segment, { kind: "connector" as const, stroke_index: null, points, length_m: lengthM(points) }];
  });
  return {
    ...SAMPLE_MATCH_RESPONSE,
    score: { ...SAMPLE_MATCH_RESPONSE.score, overall: 91 },
    route: { points: segments.flatMap((s) => s.points), length_m: 3100 },
    segments,
  };
}

type SeenMatch = { fine: boolean; hasQuality: boolean; strokes: string; request: MatchRequest };

/**
 * /api/match：不帶 quality 的回 fast 的路線（matchResponseFor），quality: "fine" 的交給 onFine（預設回 fineResponseFor）。
 * gate 有值時 fine 等它 resolve 才回應，測「fine 在途」的畫面。送出的每一筆都記在 seen。
 * 被瀏覽器取消的請求（新的操作 abort 了在途的 fine）fulfill 會失敗，忽略即可。
 */
async function mockRefineEngine(page: Page, status: object, onFine?: (request: MatchRequest) => Promise<object>) {
  const seen: SeenMatch[] = [];
  const gate: { current: Promise<void> | null } = { current: null };
  await mockMapAndStatus(page, status);
  await page.route("**/api/match", async (route) => {
    const raw = route.request().postDataJSON() as Record<string, unknown>;
    const request = matchRequestSchema.parse(raw);
    const fine = request.quality === "fine";
    seen.push({ fine, hasQuality: "quality" in raw, strokes: JSON.stringify(request.strokes), request });
    if (!fine) return route.fulfill({ json: matchResponseFor(request) }).catch(() => {});
    if (gate.current) await gate.current;
    const reply = onFine ? await onFine(request) : { json: fineResponseFor(request) };
    await route.fulfill(reply).catch(() => {});
  });
  return { seen, gate };
}

/** 讓 fine 停在途中，回傳放行的函式。 */
function holdFine(gate: { current: Promise<void> | null }): () => void {
  let open = () => {};
  gate.current = new Promise<void>((resolve) => {
    open = resolve;
  });
  return () => {
    gate.current = null;
    open();
  };
}

const isFineRequest = (r: { url(): string; postDataJSON(): unknown }) =>
  r.url().endsWith("/api/match") && (r.postDataJSON() as { quality?: string } | null)?.quality === "fine";

test("快速版與描形版切換：預設 fast，切換後路線、分數、GPX 都用 fine，重新載入保留偏好", async ({ page }) => {
  const { seen, gate } = await mockRefineEngine(page, PLANNER_STATUS);
  const map = page.getByTestId("map");
  await page.goto("/");
  const textbox = page.getByRole("textbox", { name: "文字" });
  await textbox.fill("HI");
  await textbox.blur();
  // 改文字會先後算出兩版，但預設仍顯示 fast（4 段）；切換選項同時顯示各自分數。
  const variants = page.getByRole("group", { name: "路線版本" });
  await expect(variants.getByRole("button", { name: "快速版 78%" })).toHaveAttribute("aria-pressed", "true");
  await expect(variants.getByRole("button", { name: "描形版 91%" })).toBeVisible();
  await expect(map).toHaveAttribute("data-route-segments", String(HI_STROKES));
  await expect(page.getByText("2.40 km")).toBeVisible();
  const hiStrokes = seen.filter((s) => !s.fine).at(-1)?.strokes;

  // 拖曳縮放把手後放開；這次的 fine 先停在途中
  const open = holdFine(gate);
  const dragStart = seen.length;
  const fineRequest = page.waitForRequest((r) => isFineRequest(r) && JSON.stringify(r.postDataJSON().strokes) !== hiStrokes);
  const box = await page.getByTestId("handle-scale").first().boundingBox();
  if (!box) throw new Error("找不到縮放把手");
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width / 2 + 60, box.y + box.height / 2 - 60, { steps: 8 });
  await page.mouse.up();
  const finalFine = matchRequestSchema.parse((await fineRequest).postDataJSON());

  // fine 在途：即使偏好稍後改成描形版，畫面仍暫用新擺放的 fast，且 fast 已可下載。
  await expect(page.getByText("描形版計算中…")).toBeVisible();
  await expect(variants.getByRole("button", { name: "描形版 計算中…" })).toBeVisible();
  await variants.getByRole("button", { name: "描形版 計算中…" }).click();
  await expect(map).toHaveAttribute("data-route-segments", String(HI_STROKES));
  await expect(page.getByRole("button", { name: "下載 GPX" })).toBeEnabled();

  open();
  await expect(map).toHaveAttribute("data-route-segments", String(HI_STROKES * 2 - 1));
  await expect(page.getByText("描形版計算中…")).toHaveCount(0);
  await expect(page.getByText("3.10 km")).toBeVisible();
  await expect(variants.getByRole("button", { name: "描形版 91%" })).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByText(REFINE_FAILED_NOTICE)).toHaveCount(0);

  // 拖曳開始之後只送了一筆 fine：放開後、內容和最後一筆 fast 完全相同（加上 quality），而且是最後一筆請求
  const afterDrag = seen.slice(dragStart);
  const fines = afterDrag.filter((s) => s.fine);
  const fasts = afterDrag.filter((s) => !s.fine);
  expect(fines).toHaveLength(1);
  expect(afterDrag.at(-1)?.fine).toBe(true);
  expect(fines[0].request).toEqual({ ...fasts.at(-1)?.request, quality: "fine" });
  expect(fasts.length).toBeGreaterThan(1); // 拖曳中確實送過 fast，上面的「只有一筆 fine」才不是空洞的
  // fast 的請求不帶 quality（與以前逐位元相同）
  expect(seen.filter((s) => !s.fine).every((s) => !s.hasQuality)).toBe(true);

  // GPX 下載的是目前顯示的描形版。
  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "下載 GPX" }).click();
  const gpx = await readFile(await (await downloadPromise).path(), "utf8");
  const expected = fineResponseFor(finalFine).route.points.map(
    ([x, y]) => `<trkpt lat="${y.toFixed(6)}" lon="${x.toFixed(6)}"/>`,
  );
  expect(gpx.match(/<trkpt [^>]*\/>/g)).toEqual(expected);

  // 重新載入後偏好仍是描形版；fast 先回來時暫顯示 fast，fine 回來便自動切回描形版。
  await page.reload();
  await expect(page.getByRole("group", { name: "路線版本" }).getByRole("button", { name: "描形版 91%" })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  await expect(map).toHaveAttribute("data-route-segments", String(HI_STROKES * 2 - 1));
  await expect(page.getByText("3.10 km")).toBeVisible();
});

test("描形版失敗（504）：預設不提示，選描形版才說明並顯示 fast，不自動重送", async ({ page, allowedErrors }) => {
  allowedErrors.push(/status of 504.*\/api\/match/);
  const timeout = { status: 504, json: { detail: { code: "timeout", message: "吸附超過時間上限，請縮小圖形或減少筆畫" } } };
  const { seen, gate } = await mockRefineEngine(page, PLANNER_STATUS, async () => timeout);
  const map = page.getByTestId("map");
  await page.goto("/");
  const textbox = page.getByRole("textbox", { name: "文字" });
  await textbox.fill("HI");
  await textbox.blur();

  // 預設偏好 fast：選項顯示失敗，但不另顯示提示。選描形版後偏好保留，路線仍退回 fast。
  const variants = page.getByRole("group", { name: "路線版本" });
  await expect(variants.getByRole("button", { name: "描形版 失敗" })).toBeVisible();
  await expect(page.getByText(REFINE_FAILED_NOTICE)).toHaveCount(0);
  await variants.getByRole("button", { name: "描形版 失敗" }).click();
  await expect(page.getByText(REFINE_FAILED_NOTICE)).toBeVisible();
  // 描形版錯誤不當成吸附失敗（沒有錯誤訊息、沒有「重新吸附」）。
  await expect(map).toHaveAttribute("data-route-segments", String(HI_STROKES));
  await expect(page.getByRole("button", { name: "下載 GPX" })).toBeEnabled();
  await expect(page.getByText("吸附超過時間上限，請縮小圖形或減少筆畫")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "重新吸附" })).toHaveCount(0);
  await expect(page.getByText("描形版計算中…")).toHaveCount(0);

  // 不自動重送：等一下，同一個擺放的 fine 仍然只有一筆
  const hiStrokes = seen.filter((s) => !s.fine).at(-1)?.strokes;
  await page.waitForTimeout(1500);
  expect(seen.filter((s) => s.fine && s.strokes === hiStrokes)).toHaveLength(1);

  // 下一次操作：新的 fine 在途時提示消失；偏好仍是描形版，但先顯示新 fast。
  const open = holdFine(gate);
  const fineRequest = page.waitForRequest((r) => isFineRequest(r) && JSON.stringify(r.postDataJSON().strokes) !== hiStrokes);
  await page.getByRole("group", { name: "目標圖形，方向鍵微調位置" }).focus();
  await page.keyboard.press("ArrowRight");
  await fineRequest;
  await expect(page.getByText(REFINE_FAILED_NOTICE)).toHaveCount(0);
  await expect(page.getByText("描形版計算中…")).toBeVisible();
  await expect(map).toHaveAttribute("data-route-segments", String(HI_STROKES));
  // 失敗之後回來：提示再出現，路線仍是新擺放的快速版
  open();
  await expect(page.getByText(REFINE_FAILED_NOTICE)).toBeVisible();
  await expect(map).toHaveAttribute("data-route-segments", String(HI_STROKES));
  await expect(page.getByRole("button", { name: "下載 GPX" })).toBeEnabled();
});

test("planner_ready 為 false：只送 fast（不帶 quality），改文字、拖曳、鍵盤微調都不送 fine", async ({ page }) => {
  const { seen } = await mockRefineEngine(page, { ...STATUS, planner_ready: false });
  const map = page.getByTestId("map");
  await page.goto("/");
  const textbox = page.getByRole("textbox", { name: "文字" });
  await textbox.fill("HI");
  await textbox.blur();
  await expect(map).toHaveAttribute("data-route-segments", String(HI_STROKES));

  const box = await page.getByTestId("handle-scale").first().boundingBox();
  if (!box) throw new Error("找不到縮放把手");
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width / 2 + 60, box.y + box.height / 2 - 60, { steps: 8 });
  await page.mouse.up();
  await page.getByRole("group", { name: "目標圖形，方向鍵微調位置" }).focus();
  await page.keyboard.press("ArrowRight");

  await page.waitForLoadState("networkidle");
  await page.waitForTimeout(1000);
  await expect(map).toHaveAttribute("data-route-segments", String(HI_STROKES));
  await expect(page.getByRole("button", { name: "下載 GPX" })).toBeEnabled();
  expect(seen.length).toBeGreaterThan(3);
  expect(seen.filter((s) => s.hasQuality)).toEqual([]);
  await expect(page.getByRole("group", { name: "路線版本" })).toHaveCount(0);
  await expect(page.getByText("描形版計算中…")).toHaveCount(0);
});
