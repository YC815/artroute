import { readFile } from "node:fs/promises";
import { expect, test } from "@playwright/test";
import { SAMPLE_MATCH_RESPONSE } from "../src/lib/engine/fixtures";

// 不連外：OpenFreeMap 的 style 換成只有背景的空白 style（路線圖層只用 icon，不需要 glyphs）
const BLANK_STYLE = {
  version: 8,
  sources: {},
  layers: [{ id: "background", type: "background", paint: { "background-color": "#f1f5f9" } }],
};

const STATUS = { data: { build_id: "20261010T145928Z", osm_md5: "e2e", created_at: "2026-10-10T15:00:09Z" } };

test("文字 → 放到地圖 → 吸附 → 下載 GPX", async ({ page }) => {
  await page.route("https://tiles.openfreemap.org/**", (route) => route.fulfill({ json: BLANK_STYLE }));
  await page.route("**/api/status", (route) => route.fulfill({ json: STATUS }));
  await page.route("**/api/match", (route) => route.fulfill({ json: SAMPLE_MATCH_RESPONSE }));

  await page.goto("/");
  // 開頁時預設文字也會送一次吸附；這裡等的是「HI」那一次（futural：H 三筆、I 一筆，共 4 筆）
  const hiRequest = page.waitForRequest(
    (r) => r.url().endsWith("/api/match") && r.postDataJSON()?.strokes?.length === 4,
  );
  // 用 role 找：Radix 的分頁面板也以「文字」為名（aria-labelledby），getByLabel 會撞到兩個
  await page.getByRole("textbox", { name: "文字" }).fill("HI");
  const body = (await hiRequest).postDataJSON() as { strokes: { points: [number, number][] }[] };

  // 每一筆各自是一條目標線
  await expect(page.getByTestId("target-stroke")).toHaveCount(4);

  // 吸附結果：路線長度、分數、路網日期、地圖上的段落
  await expect(page.getByText("2.40 km")).toBeVisible();
  await expect(page.getByText("78%")).toBeVisible();
  await expect(page.getByText("2026-10-10")).toBeVisible();
  await expect(page.getByTestId("map")).toHaveAttribute("data-route-segments", "3");
  // warnings 的部件在資訊面板列出
  await expect(page.getByText("第 3 筆：這一筆附近 150 m 內沒有可騎的道路")).toBeVisible();

  // 送出的經緯度在預設位置（台北 101）附近
  const [lon, lat] = body.strokes[0].points[0];
  expect(lon).toBeGreaterThan(121.4);
  expect(lon).toBeLessThan(121.7);
  expect(lat).toBeGreaterThan(24.9);
  expect(lat).toBeLessThan(25.2);

  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "下載 GPX" }).click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toBe("artroute-hi.gpx");
  const gpx = await readFile(await download.path(), "utf8");
  expect(gpx).toContain('<trkpt lat="25.030000" lon="121.500000"/>');
  expect(gpx.match(/<trkpt /g)).toHaveLength(SAMPLE_MATCH_RESPONSE.route.points.length);
});
