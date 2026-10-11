import { readFile } from "node:fs/promises";
import { expect, type Locator, type Page } from "@playwright/test";
import { flattenSegments } from "@/lib/edit/edit-bridge";
import {
  editRequestSchema,
  matchRequestSchema,
  verifyRequestSchema,
  type EditRequest,
  type MatchRequest,
  type MatchResponse,
  type VerifyRequest,
} from "@/lib/engine/contract";
import { SAMPLE_MATCH_RESPONSE } from "@/lib/engine/fixtures";
import { matchResponseFor, mockMapAndStatus, test } from "./support";

// ---- 假引擎：/api/status、/api/match、/api/edit、/api/verify ----

/** 路網版本和 /match 回應的 data_version 相同：新吸附的路線不是舊路網。 */
const BUILD = SAMPLE_MATCH_RESPONSE.data_version;
const BUILD_DATE = "2026-10-10";
const statusOf = (buildId: string) => ({ data: { build_id: buildId, osm_md5: "e2e", created_at: "2026-10-10T00:00:00Z" } });
/** 引擎重建路網之後的新版本（每週重建）：草稿的 BUILD 變成舊路網。 */
const NEW_BUILD = "20261101T000000Z";
const NEW_BUILD_DATE = "2026-11-01";

const LIVE_KM = "2.40 km";
const LIVE_SCORE = 78;
/** 第 n 次成功的微調之後，路線長度少 150 m、分數 85%：用畫面上的數字分得出是哪一個狀態。 */
const editedLengthM = (n: number) => SAMPLE_MATCH_RESPONSE.route.length_m - 150 * n;
const EDITED_KM = "2.25 km";
const EDITED_SCORE = 85;
/** 被微調的那一段整段往北平移約 33 m：GPX 的點會和即時吸附的不同。 */
const EDIT_SHIFT_DEG = 0.0003;

type Reply = { status?: number; json: unknown };

type FakeEngine = {
  matches: MatchRequest[];
  /** 最近一次 /match 回的路線（畫面上即時吸附的那條）。 */
  lastMatch: MatchResponse | null;
  edits: EditRequest[];
  verifies: VerifyRequest[];
  /** 有值時 /edit 等它 resolve 才回應（測「等待引擎」的畫面）。 */
  editGate: Promise<void> | null;
  /** 預設的成功回應（editedResponseFor）送過幾次；失敗的回應不算。 */
  edited: number;
  /** 回傳 null 就用預設的成功回應。 */
  editReply: ((request: EditRequest, n: number) => Reply | null) | null;
  verifyReply: ((request: VerifyRequest) => Reply | null) | null;
  /** 有值時 /verify 等它 resolve 才回應（測驗證進行中的畫面）。 */
  verifyGate: Promise<void> | null;
};

const editedSegment = (request: EditRequest): number =>
  request.op.type === "cut" ? request.op.at.segment : request.op.type === "drag" ? request.op.grab.segment : request.op.from.segment;

/** /edit 的成功回應：被點到的那一段換成 kind "edit"（平移），簽章依序是 sig-edit-n。 */
function editedResponseFor(request: EditRequest, n: number): MatchResponse {
  const at = editedSegment(request);
  const segments = request.segments.map((segment, i) =>
    i === at
      ? {
          ...segment,
          kind: "edit" as const,
          points: segment.points.map(([lon, lat]): [number, number] => [lon, lat + EDIT_SHIFT_DEG]),
        }
      : segment,
  );
  return {
    ...SAMPLE_MATCH_RESPONSE,
    score: { ...SAMPLE_MATCH_RESPONSE.score, overall: EDITED_SCORE },
    route: { points: flattenSegments(segments), length_m: editedLengthM(n) },
    segments,
    data_version: request.data_version,
    route_sig: `sig-edit-${n}`,
  };
}

/** /verify 的成功回應：同一條路線在新路網上重新簽章。 */
function verifiedResponseFor(request: VerifyRequest, build: string): MatchResponse {
  return {
    ...SAMPLE_MATCH_RESPONSE,
    score: { ...SAMPLE_MATCH_RESPONSE.score, overall: EDITED_SCORE },
    route: { points: flattenSegments(request.segments), length_m: editedLengthM(1) },
    segments: request.segments,
    data_version: build,
    route_sig: "sig-verified",
  };
}

async function mockEditEngine(page: Page, status: object = statusOf(BUILD)): Promise<FakeEngine> {
  const engine: FakeEngine = {
    matches: [],
    lastMatch: null,
    edits: [],
    verifies: [],
    editGate: null,
    edited: 0,
    editReply: null,
    verifyReply: null,
    verifyGate: null,
  };
  await mockMapAndStatus(page, status);
  await page.route("**/api/match", async (route) => {
    const request = matchRequestSchema.parse(route.request().postDataJSON());
    engine.matches.push(request);
    engine.lastMatch = matchResponseFor(request);
    await route.fulfill({ json: engine.lastMatch });
  });
  await page.route("**/api/edit", async (route) => {
    // app 送出的請求必須符合契約（含簽章欄位）
    const request = editRequestSchema.parse(route.request().postDataJSON());
    engine.edits.push(request);
    if (engine.editGate) await engine.editGate;
    const custom = engine.editReply?.(request, engine.edits.length);
    const reply = custom ?? { json: editedResponseFor(request, ++engine.edited) };
    // 被瀏覽器取消的請求（Esc、完成）fulfill 會失敗，忽略即可
    await route.fulfill(reply).catch(() => {});
  });
  await page.route("**/api/verify", async (route) => {
    const request = verifyRequestSchema.parse(route.request().postDataJSON());
    engine.verifies.push(request);
    if (engine.verifyGate) await engine.verifyGate;
    const reply = engine.verifyReply?.(request) ?? { json: verifiedResponseFor(request, NEW_BUILD) };
    await route.fulfill(reply).catch(() => {});
  });
  return engine;
}

/** 讓 /edit 停在途中，回傳放行的函式。 */
function holdEdit(engine: FakeEngine): () => void {
  let open = () => {};
  engine.editGate = new Promise<void>((resolve) => {
    open = resolve;
  });
  return () => {
    engine.editGate = null;
    open();
  };
}

// ---- 地圖座標 → 螢幕座標（地圖固定 zoom 14、不旋轉；中心是地圖容器的正中央） ----

/** studio.tsx 的預設擺放中心；新的瀏覽器 context 沒有存檔，地圖一定從這裡開始。 */
const MAP_CENTER: [number, number] = [121.5654, 25.033];
const WORLD_PX = 512 * 2 ** 14;

const mercator = ([lon, lat]: [number, number]) => ({
  x: ((lon + 180) / 360) * WORLD_PX,
  y: (0.5 - Math.log(Math.tan(Math.PI / 4 + (lat * Math.PI) / 360)) / (2 * Math.PI)) * WORLD_PX,
});

type Box = { x: number; y: number; width: number; height: number };

class MapView {
  readonly cx: number;
  readonly cy: number;
  private readonly origin = mercator(MAP_CENTER);

  constructor(readonly box: Box) {
    this.cx = box.x + box.width / 2;
    this.cy = box.y + box.height / 2;
  }

  toScreen(lonLat: [number, number]) {
    const m = mercator(lonLat);
    return { x: this.cx + m.x - this.origin.x, y: this.cy + m.y - this.origin.y };
  }

  toLonLat(x: number, y: number): [number, number] {
    const mx = this.origin.x + x - this.cx;
    const my = this.origin.y + y - this.cy;
    const lon = (mx / WORLD_PX) * 360 - 180;
    const lat = (2 * Math.atan(Math.exp((0.5 - my / WORLD_PX) * 2 * Math.PI)) - Math.PI / 2) * (180 / Math.PI);
    return [lon, lat];
  }

  inside(p: { x: number; y: number }, margin: number): boolean {
    return (
      p.x > this.box.x + margin &&
      p.x < this.box.x + this.box.width - margin &&
      p.y > this.box.y + margin &&
      p.y < this.box.y + this.box.height - margin
    );
  }
}

async function mapView(page: Page): Promise<MapView> {
  const box = await page.getByTestId("map").boundingBox();
  if (!box) throw new Error("找不到地圖");
  return new MapView(box);
}

type Hit = { segment: number; x: number; y: number };

/**
 * 路線上離畫面中心最近的一點（線段的中點，一定在線上），離 apart 裡的點至少 minGapPx。
 * 路線（HI 兩個字約 460 px 寬）在手機寬度會超出畫面，所以只挑畫面裡的點。
 */
function pickOnRoute(view: MapView, response: MatchResponse, apart: Hit[] = [], minGapPx = 0): Hit {
  let best: (Hit & { dist: number }) | null = null;
  response.segments.forEach(({ points }, segment) => {
    for (let i = 0; i + 1 < points.length; i++) {
      const a = view.toScreen(points[i]);
      const b = view.toScreen(points[i + 1]);
      const mid = { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 };
      if (!view.inside(mid, 40)) continue;
      if (apart.some((p) => Math.hypot(p.x - mid.x, p.y - mid.y) < minGapPx)) continue;
      const dist = Math.hypot(mid.x - view.cx, mid.y - view.cy);
      if (!best || dist < best.dist) best = { segment, ...mid, dist };
    }
  });
  if (!best) throw new Error("畫面裡沒有路線");
  return best;
}

// ---- 畫面上的零件 ----

const tool = (page: Page, name: "剪掉" | "重畫" | "拖曳") => page.getByRole("radio", { name });
const toolbar = (page: Page) => page.getByTestId("edit-toolbar");
const mapEl = (page: Page) => page.getByTestId("map");
const draftsPanel = (page: Page) => page.getByRole("region", { name: "我的路線" });
const lockedFrame = (page: Page) => page.getByRole("group", { name: "目標圖形（微調路線中，已鎖住）" }).locator("polygon");
const freeFrame = (page: Page) => page.getByRole("group", { name: "目標圖形，方向鍵微調位置" }).locator("polygon");
const HI_STROKES = 4;

/** 開頁、把文字改成 HI（4 筆）、等吸附結果出來（預設文字是 15 筆）。 */
async function loadHi(page: Page) {
  await page.goto("/");
  const textbox = page.getByRole("textbox", { name: "文字" });
  await textbox.fill("HI");
  await textbox.blur();
  await expect(mapEl(page)).toHaveAttribute("data-route-segments", String(HI_STROKES));
  await expect(page.getByText(LIVE_KM, { exact: true })).toBeVisible();
}

async function enterEdit(page: Page) {
  await page.getByRole("button", { name: "微調路線" }).click();
  await expect(toolbar(page)).toBeVisible();
}

async function pointsOf(frame: Locator): Promise<string> {
  const points = await frame.getAttribute("points");
  if (!points) throw new Error("找不到外框");
  return points;
}

async function savedTransform(page: Page): Promise<unknown> {
  return page.evaluate(() => JSON.parse(window.localStorage.getItem("artroute:work") ?? "null")?.transform);
}

/** 選「剪掉」、點路線上的一點，等引擎回應；回傳點的位置。 */
async function cutOnRoute(page: Page, engine: FakeEngine, hit: Hit) {
  await tool(page, "剪掉").click();
  await page.mouse.click(hit.x, hit.y);
  await expect.poll(() => engine.edits.length).toBeGreaterThan(0);
}

const gpxPointsOf = (response: MatchResponse) =>
  flattenSegments(response.segments).map(([x, y]) => `<trkpt lat="${y.toFixed(6)}" lon="${x.toFixed(6)}"/>`);

async function downloadGpxPoints(page: Page): Promise<string[]> {
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "下載 GPX" }).click();
  const gpx = await readFile(await (await download).path(), "utf8");
  return gpx.match(/<trkpt [^>]*\/>/g) ?? [];
}

// ---- 劇本 ----

test("進入微調 → 剪掉 → 復原 → 重做 → 完成 → 移動圖形 → 我的路線 → 打開 → 路線與擺放還原", async ({ page }) => {
  const engine = await mockEditEngine(page);
  await loadHi(page);
  const live = engine.lastMatch!;
  const view = await mapView(page);
  const hit = pickOnRoute(view, live);
  const frameBefore = await pointsOf(freeFrame(page));

  await enterEdit(page);
  await expect(page.getByText("選一個操作：剪掉、重畫或拖曳")).toBeVisible();
  // 微調中：資訊面板不再有「微調路線」按鈕、圖形把手收起來
  await expect(page.getByRole("button", { name: "微調路線" })).toHaveCount(0);
  await expect(draftsPanel(page).getByText("還沒有草稿")).toBeVisible();

  // 剪掉：請求帶著簽章與原封不動的路線；回應換上 kind "edit" 的段
  await cutOnRoute(page, engine, hit);
  await expect(page.getByText(EDITED_KM, { exact: true })).toBeVisible();
  await expect(page.getByText(`${EDITED_SCORE}%`).first()).toBeVisible();
  await expect(page.getByText("已微調")).toBeVisible();
  expect(engine.edits).toHaveLength(1);
  const [first] = engine.edits;
  expect(first.route_sig).toBe(live.route_sig);
  expect(first.data_version).toBe(live.data_version);
  expect(first.segments).toEqual(live.segments);
  expect(first.op.type).toBe("cut");
  if (first.op.type !== "cut") throw new Error("op 不是 cut");
  expect(first.op.at.segment).toBe(hit.segment);
  expect(first.op.at.offset_m).toBeGreaterThan(0);
  expect(first.op.at.offset_m).toBeLessThanOrEqual(live.segments[hit.segment].length_m);
  expect(first.strokes).toHaveLength(HI_STROKES);
  // 微調的路線畫在地圖上、不再吸附（沒有新的 /match）
  await expect(mapEl(page)).toHaveAttribute("data-route-segments", String(HI_STROKES));
  const matchesDuringEdit = engine.matches.length;

  // 第一次成功的編輯就建立草稿
  await expect(draftsPanel(page).getByRole("listitem")).toHaveCount(1);
  await expect(draftsPanel(page).getByText("編輯中")).toBeVisible();

  // 復原（不呼叫引擎）→ 重做
  await expect(page.getByRole("button", { name: "重做" })).toBeDisabled();
  await page.getByRole("button", { name: "復原" }).click();
  await expect(page.getByText(LIVE_KM, { exact: true })).toBeVisible();
  await expect(page.getByText(`${LIVE_SCORE}%`).first()).toBeVisible();
  await expect(page.getByRole("button", { name: "復原" })).toBeDisabled();
  await page.getByRole("button", { name: "重做" }).click();
  await expect(page.getByText(EDITED_KM, { exact: true })).toBeVisible();
  expect(engine.edits).toHaveLength(1);

  // 完成：路線留著、圖形解鎖（把手回來）、可以繼續微調、GPX 是微調後的路線
  await page.getByRole("button", { name: "完成" }).click();
  await expect(toolbar(page)).toHaveCount(0);
  await expect(page.getByRole("button", { name: "繼續微調" })).toBeVisible();
  await expect(page.getByText(EDITED_KM, { exact: true })).toBeVisible();
  await expect(page.getByTestId("handle-scale")).toHaveCount(4);
  expect(await pointsOf(freeFrame(page))).toBe(frameBefore);
  const edited = editedResponseFor(first, 1);
  expect(await downloadGpxPoints(page)).toEqual(gpxPointsOf(edited));
  expect(gpxPointsOf(edited)).not.toEqual(gpxPointsOf(live));

  // 移動圖形（拖曳縮放把手）：回到即時吸附，微調的路線不見，草稿留在「我的路線」
  const box = await page.getByTestId("handle-scale").first().boundingBox();
  if (!box) throw new Error("找不到縮放把手");
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width / 2 + 40, box.y + box.height / 2 - 40, { steps: 6 });
  await page.mouse.up();
  await expect(page.getByText(LIVE_KM, { exact: true })).toBeVisible();
  await expect(page.getByText("已微調")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "微調路線" })).toBeVisible();
  expect(engine.matches.length).toBeGreaterThan(matchesDuringEdit);
  const frameMoved = await pointsOf(freeFrame(page));
  expect(frameMoved).not.toBe(frameBefore);

  // 改成別的文字：來源也要在打開草稿時還原
  const textbox = page.getByRole("textbox", { name: "文字" });
  await textbox.fill("L");
  await textbox.blur();
  await expect(page.getByTestId("target-stroke")).not.toHaveCount(HI_STROKES);

  // 我的路線：這份草稿（名稱、公里數、路網日期）
  const item = draftsPanel(page).getByRole("listitem");
  await expect(item).toHaveCount(1);
  await expect(item.getByText("HI", { exact: true })).toBeVisible();
  await expect(item.getByText(EDITED_KM)).toBeVisible();
  await expect(item.getByText(`路網 ${BUILD_DATE}`)).toBeVisible();
  await expect(item.getByText("舊路網")).toHaveCount(0);

  // 打開：來源、擺放、微調後的路線都還原，進入微調（草稿的圖形與擺放就是基準，不會馬上被當成「圖形被移動」而結束）
  const matchesBeforeOpen = engine.matches.length;
  await item.getByRole("button", { name: "打開 HI" }).click();
  await expect(toolbar(page)).toBeVisible();
  await expect(textbox).toHaveValue("HI");
  await expect(page.getByTestId("target-stroke")).toHaveCount(HI_STROKES);
  await expect(page.getByText(EDITED_KM, { exact: true })).toBeVisible();
  await expect(page.getByText("已微調")).toBeVisible();
  await expect(item.getByText("編輯中")).toBeVisible();
  expect(await pointsOf(lockedFrame(page))).toBe(frameBefore);
  await expect(page.getByTestId("handle-scale")).toHaveCount(0);
  // 等一下：結束微調的 effect 若有問題，會在幾個 render 之後才發生
  await page.waitForTimeout(800);
  await expect(toolbar(page)).toBeVisible();
  await expect(page.getByText(EDITED_KM, { exact: true })).toBeVisible();
  expect(engine.matches.length).toBe(matchesBeforeOpen);
  expect(await downloadGpxPoints(page)).toEqual(gpxPointsOf(edited));

  // 擺放存回 localStorage 的是草稿的擺放；重新載入後草稿還在
  await page.reload();
  await expect(draftsPanel(page).getByRole("listitem")).toHaveCount(1);
  await expect(draftsPanel(page).getByText(EDITED_KM)).toBeVisible();
});

test("進入微調後圖形鎖住：把手收起來，拖曳與方向鍵都不能移動圖形，地圖拖曳只移動畫面", async ({ page }) => {
  const engine = await mockEditEngine(page);
  await loadHi(page);
  const corner = await page.getByTestId("handle-scale").first().boundingBox();
  if (!corner) throw new Error("找不到縮放把手");
  const transformBefore = await savedTransform(page);
  const frameBefore = await pointsOf(freeFrame(page));

  await enterEdit(page);
  await expect(page.getByTestId("handle-scale")).toHaveCount(0);
  await expect(page.getByTestId("handle-rotate")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "圖形移到畫面中央" })).toHaveCount(0);
  const frame = lockedFrame(page);
  await expect(frame).toBeAttached();
  // 目標線與「隱藏原圖」照常
  await expect(page.getByTestId("target-stroke")).toHaveCount(HI_STROKES);
  await page.keyboard.press("h");
  await expect(page.getByTestId("target-stroke")).toHaveCount(0);
  await page.keyboard.press("h");
  await expect(page.getByTestId("target-stroke")).toHaveCount(HI_STROKES);

  // 方向鍵：圖形不能取得焦點、也不會移動
  await page.getByRole("group", { name: "目標圖形（微調路線中，已鎖住）" }).focus();
  await page.keyboard.press("ArrowRight");
  await page.keyboard.press("Shift+ArrowUp");
  // 在原本把手的位置按下拖曳：圖形不動（沒有新的擺放、沒有新的 /match），地圖被平移
  const matchesBefore = engine.matches.length;
  const at = { x: corner.x + corner.width / 2, y: corner.y + corner.height / 2 };
  await page.mouse.move(at.x, at.y);
  await page.mouse.down();
  await page.mouse.move(at.x + 70, at.y - 50, { steps: 8 });
  await page.mouse.up();
  await page.waitForTimeout(800);
  expect(engine.matches.length).toBe(matchesBefore);
  expect(await savedTransform(page)).toEqual(transformBefore);
  await expect(toolbar(page)).toBeVisible();
  await expect(page.getByText(LIVE_KM, { exact: true })).toBeVisible();
  // 地圖真的被平移了（外框是跟著地圖投影的）：證明上面「圖形不動」不是因為這一下什麼都沒發生
  expect(await pointsOf(frame)).not.toBe(frameBefore);
});

test("拖曳：抓住路線拖動期間地圖不平移，放開才送 /edit；Esc 取消這次拖曳；沒抓到路線時地圖照常平移", async ({ page }) => {
  const engine = await mockEditEngine(page);
  await loadHi(page);
  const live = engine.lastMatch!;
  const view = await mapView(page);
  const hit = pickOnRoute(view, live);
  await enterEdit(page);
  await tool(page, "拖曳").click();
  await expect(page.getByText("抓住路線上任一點，拖到新位置再放開")).toBeVisible();
  const frame = lockedFrame(page);
  const frameBefore = await pointsOf(frame);

  // 抓住路線拖到 (+60, -40) px：拖動期間外框（跟著地圖投影）一動也不動 = dragPan 真的被擋住
  const target = { x: hit.x + 60, y: hit.y - 40 };
  await page.mouse.move(hit.x, hit.y);
  await page.mouse.down();
  await page.mouse.move(target.x, target.y, { steps: 10 });
  expect(await pointsOf(frame)).toBe(frameBefore);
  expect(engine.edits).toHaveLength(0);
  await page.mouse.up();
  await expect.poll(() => engine.edits.length).toBe(1);
  const [request] = engine.edits;
  expect(request.op.type).toBe("drag");
  if (request.op.type !== "drag") throw new Error("op 不是 drag");
  expect(request.op.grab.segment).toBe(hit.segment);
  const expected = view.toLonLat(target.x, target.y);
  expect(Math.abs(request.op.to[0] - expected[0])).toBeLessThan(2e-4);
  expect(Math.abs(request.op.to[1] - expected[1])).toBeLessThan(2e-4);
  await expect(page.getByText(EDITED_KM, { exact: true })).toBeVisible();
  expect(await pointsOf(frame)).toBe(frameBefore);

  // 放開之後 dragPan 恢復：下一次抓到路線以外的地方，地圖照常平移（也證明上面的「不平移」不是 dragPan 一直關著）
  // 抓點選在畫面右側的空地（HI 約 460 px 寬、在畫面中央；左上角有按鈕）
  const empty = { x: view.box.x + view.box.width - 40, y: view.cy };
  await page.mouse.move(empty.x, empty.y);
  await page.mouse.down();
  await page.mouse.move(empty.x - 80, empty.y + 50, { steps: 8 });
  await page.mouse.up();
  expect(await pointsOf(frame)).not.toBe(frameBefore);
  expect(engine.edits).toHaveLength(1);
});

test("Esc 取消這次拖曳：不送 /edit，操作維持選著，地圖不平移", async ({ page }) => {
  const engine = await mockEditEngine(page);
  await loadHi(page);
  const view = await mapView(page);
  const hit = pickOnRoute(view, engine.lastMatch!);
  await enterEdit(page);
  await tool(page, "拖曳").click();
  const frame = lockedFrame(page);
  const frameBefore = await pointsOf(frame);

  await page.mouse.move(hit.x, hit.y);
  await page.mouse.down();
  await page.mouse.move(hit.x + 70, hit.y + 30, { steps: 8 });
  await page.keyboard.press("Escape");
  await page.mouse.move(hit.x + 90, hit.y + 40, { steps: 4 });
  await page.mouse.up();
  await page.waitForTimeout(500);
  expect(engine.edits).toHaveLength(0);
  await expect(tool(page, "拖曳")).toBeChecked();
  expect(await pointsOf(frame)).toBe(frameBefore);
  await expect(page.getByText(LIVE_KM, { exact: true })).toBeVisible();
});

test("Esc：取消正在選的操作（重畫的點 → 工具），等引擎時 Esc 放棄這次操作、晚到的結果不套用", async ({ page }) => {
  const engine = await mockEditEngine(page);
  await loadHi(page);
  const view = await mapView(page);
  const a = pickOnRoute(view, engine.lastMatch!);
  const b = pickOnRoute(view, engine.lastMatch!, [a], 90);
  await enterEdit(page);

  // 重畫：點第一個點 → 再點第二個點 → Esc 清掉兩個點 → Esc 取消工具
  await tool(page, "重畫").click();
  await expect(page.getByText("點路線上重畫的第一個點")).toBeVisible();
  await expect(page.getByText("Esc 取消")).toBeVisible();
  await page.mouse.click(a.x, a.y);
  await expect(page.getByText("再點第二個點")).toBeVisible();
  await page.mouse.click(b.x, b.y);
  await expect(page.getByText("選「最短」或「貼圖形」")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByText("點路線上重畫的第一個點")).toBeVisible();
  await expect(tool(page, "重畫")).toBeChecked();
  await page.keyboard.press("Escape");
  await expect(page.getByText("選一個操作：剪掉、重畫或拖曳")).toBeVisible();
  await expect(tool(page, "重畫")).not.toBeChecked();
  expect(engine.edits).toHaveLength(0);

  // 等引擎：按鈕停用、顯示處理中；Esc 放棄，之後引擎才回來的結果丟掉
  const release = holdEdit(engine);
  await tool(page, "剪掉").click();
  await page.mouse.click(a.x, a.y);
  await expect(page.getByText("引擎處理中…")).toBeVisible();
  await expect(tool(page, "剪掉")).toBeDisabled();
  await expect(page.getByRole("button", { name: "完成" })).toBeDisabled();
  await page.keyboard.press("Escape");
  await expect(page.getByText("引擎處理中…")).toHaveCount(0);
  await expect(tool(page, "剪掉")).toBeEnabled();
  release();
  await page.waitForTimeout(500);
  await expect(page.getByText(LIVE_KM, { exact: true })).toBeVisible();
  await expect(draftsPanel(page).getByText("還沒有草稿")).toBeVisible();
});

test("⌘/Ctrl+Z 復原、Shift+⌘/Ctrl+Z 重做（只在微調中攔）", async ({ page }) => {
  const engine = await mockEditEngine(page);
  await loadHi(page);
  const view = await mapView(page);
  const hit = pickOnRoute(view, engine.lastMatch!);

  // 沒在微調：不攔，什麼都不會發生
  await page.keyboard.press("ControlOrMeta+z");
  await expect(page.getByText(LIVE_KM, { exact: true })).toBeVisible();

  await enterEdit(page);
  await cutOnRoute(page, engine, hit);
  await expect(page.getByText(EDITED_KM, { exact: true })).toBeVisible();
  await page.keyboard.press("ControlOrMeta+z");
  await expect(page.getByText(LIVE_KM, { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "重做" })).toBeEnabled();
  await page.keyboard.press("ControlOrMeta+Shift+z");
  await expect(page.getByText(EDITED_KM, { exact: true })).toBeVisible();
  // 復原／重做不叫引擎
  expect(engine.edits).toHaveLength(1);
});

test("引擎拒絕操作：保留原路線並顯示說明", async ({ page, allowedErrors }) => {
  allowedErrors.push(/status of 422.*\/api\/edit/);
  const engine = await mockEditEngine(page);
  engine.editReply = () => ({
    status: 422,
    json: { detail: { code: "nothing_to_cut", message: "這裡沒有可以剪掉的圈，可以改用「重畫」的最短" } },
  });
  await loadHi(page);
  const view = await mapView(page);
  await enterEdit(page);
  await cutOnRoute(page, engine, pickOnRoute(view, engine.lastMatch!));
  await expect(page.getByText("這裡沒有可以剪掉的圈，可以改用「重畫」的最短")).toBeVisible();
  await expect(page.getByText(LIVE_KM, { exact: true })).toBeVisible();
  await expect(draftsPanel(page).getByText("還沒有草稿")).toBeVisible();
});

// ---- 舊路網的草稿、重新驗證 ----

const VERIFY_FAILED_MESSAGE = "有些段落在新路網上不能騎，請回到自動吸附重做";
const verifyFailed = (segments: number[]): Reply => ({
  status: 422,
  json: { detail: { code: "verify_failed", message: VERIFY_FAILED_MESSAGE, segments } },
});
const OLD_BUILD_LABEL = "這是 10 月 10 日路網上的路線";

/** route-layers.ts 的 edit-bad 圖層線色 #dc2626（終點圓點也是這個顏色，所以比較前後的差）。 */
const RED_RGB = [0xdc, 0x26, 0x26] as const;

const countRedPixels = (page: Page, decoder: Page) => countPixels(page, decoder, RED_RGB);

/** 地圖畫面裡與 rgb 相近的像素數（螢幕截圖丟給另一個分頁解碼；測試用來看路線、紅色標段畫在哪）。 */
async function countPixels(page: Page, decoder: Page, color: readonly [number, number, number]): Promise<number> {
  const png = await mapEl(page).screenshot();
  return decoder.evaluate(
    async ({ base64, rgb }) => {
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
        if (data[i + 3] === 255 && rgb.every((c, k) => Math.abs(data[i + k] - c) <= 12)) count += 1;
      }
      return count;
    },
    { base64: png.toString("base64"), rgb: [...color] },
  );
}

/** 微調一次（剪掉）、按完成：草稿存在「我的路線」。回傳即時吸附的路線與微調後的路線。 */
async function makeDraft(page: Page, engine: FakeEngine) {
  await loadHi(page);
  const live = engine.lastMatch!;
  const view = await mapView(page);
  await enterEdit(page);
  await cutOnRoute(page, engine, pickOnRoute(view, live));
  await expect(page.getByText(EDITED_KM, { exact: true })).toBeVisible();
  await expect(draftsPanel(page).getByRole("listitem")).toHaveCount(1);
  await page.getByRole("button", { name: "完成" }).click();
  await expect(toolbar(page)).toHaveCount(0);
  return { live, edited: editedResponseFor(engine.edits[0], 1), view };
}

/** 引擎重建了路網：之後讀到的 /status 是新版本，重新載入頁面。 */
async function reloadOnNewBuild(page: Page) {
  await page.route("**/api/status", (route) => route.fulfill({ json: statusOf(NEW_BUILD) }));
  await page.reload();
  await expect(draftsPanel(page).getByRole("listitem")).toHaveCount(1);
}

/** 草稿的路網（10/10）不是引擎目前的（11/01）：打開之後顯示「這是 X 月 X 日路網上的路線」。 */
async function openOldDraft(page: Page) {
  const item = draftsPanel(page).getByRole("listitem");
  await expect(item.getByText("舊路網")).toBeVisible();
  await item.getByRole("button", { name: "打開 HI" }).click();
  await expect(page.getByTestId("reverify-notice")).toBeVisible();
}

test("舊路網的草稿：顯示路網日期與「重新驗證」，不能編輯；沒過標紅、說明、可再試；通過後換成新路網、能繼續編輯", async ({
  page,
  allowedErrors,
}) => {
  allowedErrors.push(/status of 422.*\/api\/verify/);
  const engine = await mockEditEngine(page);
  const { edited, view } = await makeDraft(page, engine);
  const decoder = await page.context().newPage();
  await reloadOnNewBuild(page);
  await expect(draftsPanel(page).getByText(`路網 ${BUILD_DATE}`)).toBeVisible();
  await openOldDraft(page);

  // 舊路網：路網日期、重新驗證；工具列（微調操作）不在、圖形仍鎖住；路線照常顯示、可以下載 GPX
  const notice = page.getByTestId("reverify-notice");
  await expect(notice.getByText(OLD_BUILD_LABEL)).toBeVisible();
  await expect(notice.getByRole("button", { name: "重新驗證" })).toBeEnabled();
  await expect(notice.getByRole("button", { name: "回到自動吸附" })).toBeEnabled();
  await expect(toolbar(page)).toHaveCount(0);
  await expect(page.getByTestId("handle-scale")).toHaveCount(0);
  await expect(page.getByText(EDITED_KM, { exact: true })).toBeVisible();
  expect(await downloadGpxPoints(page)).toEqual(gpxPointsOf(edited));
  // 地圖上點路線不會有任何操作（沒有工具可選，點了也不送 /edit）
  const hit = pickOnRoute(view, edited);
  await page.mouse.click(hit.x, hit.y);
  expect(engine.edits).toHaveLength(1);

  // 驗證沒過：說明、有問題的段在地圖上標紅、可以再試；請求帶舊版本與簽章
  const redBefore = await countRedPixels(page, decoder);
  engine.verifyReply = () => verifyFailed([1, 2]);
  await notice.getByRole("button", { name: "重新驗證" }).click();
  await expect(notice.getByText(VERIFY_FAILED_MESSAGE)).toBeVisible();
  await expect(notice.getByText("有 2 段在新路網上不能騎")).toBeVisible();
  await expect(notice.getByRole("button", { name: "再驗證一次" })).toBeEnabled();
  expect(engine.verifies).toHaveLength(1);
  expect(engine.verifies[0].data_version).toBe(BUILD);
  expect(engine.verifies[0].route_sig).toBe("sig-edit-1");
  expect(engine.verifies[0].segments).toEqual(edited.segments);
  expect(engine.verifies[0].strokes).toHaveLength(HI_STROKES);
  await expect.poll(() => countRedPixels(page, decoder), { message: "有問題的段沒有在地圖上標紅" }).toBeGreaterThan(redBefore + 300);
  await expect(toolbar(page)).toHaveCount(0);
  await expect(notice.getByText(OLD_BUILD_LABEL)).toBeVisible();

  // 再試：這次通過 → 換成新路網的版本，紅色消失、說明消失、工具列出現
  engine.verifyReply = null;
  await notice.getByRole("button", { name: "再驗證一次" }).click();
  await expect(notice).toHaveCount(0);
  await expect(toolbar(page)).toBeVisible();
  await expect.poll(() => countRedPixels(page, decoder)).toBeLessThan(redBefore + 100);
  const item = draftsPanel(page).getByRole("listitem");
  await expect(item.getByText(`路網 ${NEW_BUILD_DATE}`)).toBeVisible();
  await expect(item.getByText("舊路網")).toHaveCount(0);
  expect(engine.verifies).toHaveLength(2);

  // 之後的操作用新版本與新簽章
  await cutOnRoute(page, engine, hit);
  expect(engine.edits).toHaveLength(2);
  expect(engine.edits[1].route_sig).toBe("sig-verified");
  expect(engine.edits[1].data_version).toBe(NEW_BUILD);
});

test("舊路網的草稿：「回到自動吸附」重新吸附，草稿保留", async ({ page }) => {
  const engine = await mockEditEngine(page);
  await makeDraft(page, engine);
  await reloadOnNewBuild(page);
  await openOldDraft(page);

  await page.getByRole("button", { name: "回到自動吸附" }).click();
  await expect(page.getByTestId("reverify-notice")).toHaveCount(0);
  await expect(toolbar(page)).toHaveCount(0);
  await expect(page.getByText(LIVE_KM, { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "微調路線" })).toBeVisible();
  await expect(page.getByTestId("handle-scale")).toHaveCount(4);
  await expect(draftsPanel(page).getByText("舊路網")).toBeVisible();
  await expect(draftsPanel(page).getByRole("listitem")).toHaveCount(1);
});

test("引擎回 409（路網已更新）：改顯示「重新驗證」，通過後繼續編輯；沒存過的微調不會因驗證而建草稿", async ({
  page,
  allowedErrors,
}) => {
  allowedErrors.push(/status of 409.*\/api\/edit/);
  const engine = await mockEditEngine(page);
  engine.editReply = () => ({
    status: 409,
    json: { detail: { code: "stale_data", message: "data_version 不是目前的路網" } },
  });
  await loadHi(page);
  const view = await mapView(page);
  await enterEdit(page);
  await cutOnRoute(page, engine, pickOnRoute(view, engine.lastMatch!));

  const notice = page.getByTestId("reverify-notice");
  await expect(notice.getByText(`路網已更新。${OLD_BUILD_LABEL}`)).toBeVisible();
  await expect(toolbar(page)).toHaveCount(0);
  await notice.getByRole("button", { name: "重新驗證" }).click();
  await expect(notice).toHaveCount(0);
  await expect(toolbar(page)).toBeVisible();
  expect(engine.verifies).toHaveLength(1);
  await expect(draftsPanel(page).getByText("還沒有草稿")).toBeVisible();
});

// ---- 刪除草稿 ----

test("刪除草稿要確認：取消不刪；確認才刪；刪掉正在編輯的那份會結束微調", async ({ page }) => {
  const engine = await mockEditEngine(page);
  await makeDraft(page, engine);
  const item = draftsPanel(page).getByRole("listitem");

  // 取消：對話框關掉，草稿還在
  await item.getByRole("button", { name: "刪除 HI" }).click();
  const dialog = page.getByRole("alertdialog");
  await expect(dialog.getByText("刪除這份草稿？")).toBeVisible();
  await expect(dialog.getByText("HI", { exact: false })).toBeVisible();
  await dialog.getByRole("button", { name: "取消" }).click();
  await expect(dialog).toHaveCount(0);
  await expect(item).toHaveCount(1);

  // 回到微調中再刪：確認 → 草稿沒了、結束微調、回到即時吸附，沒有「全部刪除」
  await page.getByRole("button", { name: "繼續微調" }).click();
  await expect(toolbar(page)).toBeVisible();
  await expect(page.getByRole("button", { name: /全部刪除/ })).toHaveCount(0);
  await item.getByRole("button", { name: "刪除 HI" }).click();
  await expect(dialog).toBeVisible();
  // 還沒確認：草稿與微調都還在（對話框開著時 Radix 把背景從無障礙樹拿掉，所以用 CSS 找清單項目）
  await expect(page.locator('section[aria-label="我的路線"] li')).toHaveCount(1);
  await expect(toolbar(page)).toBeAttached();
  await dialog.getByRole("button", { name: "刪除" }).click();
  await expect(dialog).toHaveCount(0);
  await expect(draftsPanel(page).getByText("還沒有草稿")).toBeVisible();
  await expect(toolbar(page)).toHaveCount(0);
  await expect(page.getByText(LIVE_KM, { exact: true })).toBeVisible();
  // 沒有被存檔重新建立回來；重新載入後仍然沒有
  await page.waitForTimeout(500);
  await expect(item).toHaveCount(0);
  await page.reload();
  await expect(draftsPanel(page).getByText("還沒有草稿")).toBeVisible();
});

// ---- 手機寬度 ----

test.describe("手機寬度（390×844）", () => {
  test.use({ viewport: { width: 390, height: 844 } });

  /** 沒有橫向捲動：頁面、側欄都不能比視窗寬。 */
  async function expectNoHorizontalScroll(page: Page) {
    const overflow = await page.evaluate(() => ({
      document: document.documentElement.scrollWidth - window.innerWidth,
      body: document.body.scrollWidth - window.innerWidth,
      aside: (() => {
        const aside = document.querySelector("aside");
        return aside ? aside.scrollWidth - aside.clientWidth : 0;
      })(),
    }));
    expect(overflow).toEqual({ document: 0, body: 0, aside: 0 });
  }

  /** 浮在地圖上的面板：整個在地圖裡，而且不蓋住地圖正中央（圖形與路線擺在那裡）。 */
  async function expectPanelLeavesMapCenter(page: Page, panel: Locator) {
    const map = await mapEl(page).boundingBox();
    const box = await panel.boundingBox();
    if (!map || !box) throw new Error("找不到地圖或面板");
    const info = JSON.stringify({ map, panel: box });
    expect(box.x, info).toBeGreaterThanOrEqual(map.x);
    expect(box.x + box.width, info).toBeLessThanOrEqual(map.x + map.width);
    expect(box.y, info).toBeGreaterThanOrEqual(map.y);
    expect(box.y + box.height, info).toBeLessThanOrEqual(map.y + map.height);
    expect(box.y, `面板蓋住了地圖中央 ${info}`).toBeGreaterThanOrEqual(map.y + map.height / 2);
  }

  test("微調工具列（含重畫選模式與引擎的說明）不遮住地圖中央、沒有橫向捲動，路線仍點得到", async ({ page, allowedErrors }) => {
    allowedErrors.push(/status of 422.*\/api\/edit/);
    const engine = await mockEditEngine(page);
    await loadHi(page);
    const view = await mapView(page);
    const a = pickOnRoute(view, engine.lastMatch!);
    const b = pickOnRoute(view, engine.lastMatch!, [a], 60);
    await enterEdit(page);
    await expectNoHorizontalScroll(page);
    await expectPanelLeavesMapCenter(page, toolbar(page));

    // 最高的狀態：重畫選好兩點（選模式的那一列）＋引擎拒絕的說明
    engine.editReply = () => ({
      status: 422,
      json: { detail: { code: "no_road", message: "這兩點之間沒有可以騎的路，請換個位置或改用「最短」" } },
    });
    await tool(page, "重畫").click();
    await page.mouse.click(a.x, a.y);
    await page.mouse.click(b.x, b.y);
    await page.getByRole("button", { name: "貼圖形" }).click();
    await expect(page.getByText("這兩點之間沒有可以騎的路，請換個位置或改用「最短」")).toBeVisible();
    await expect(page.getByRole("button", { name: "最短" })).toBeVisible();
    await expectNoHorizontalScroll(page);
    await expectPanelLeavesMapCenter(page, toolbar(page));

    // 路線沒被面板蓋住：最短 → 成功，路線更新
    engine.editReply = null;
    await page.getByRole("button", { name: "最短" }).click();
    await expect(page.getByText(EDITED_KM, { exact: true })).toBeVisible();
    expect(engine.edits).toHaveLength(2);
    expect(engine.edits.at(-1)?.op).toMatchObject({ type: "reroute", mode: "shortest" });
    await expectNoHorizontalScroll(page);
  });

  test("舊路網的提示（含驗證沒過的說明）不遮住地圖中央、沒有橫向捲動", async ({ page, allowedErrors }) => {
    allowedErrors.push(/status of 422.*\/api\/verify/);
    const engine = await mockEditEngine(page);
    await makeDraft(page, engine);
    await reloadOnNewBuild(page);
    await openOldDraft(page);
    const notice = page.getByTestId("reverify-notice");
    await expectNoHorizontalScroll(page);
    await expectPanelLeavesMapCenter(page, notice);

    engine.verifyReply = () => verifyFailed([1, 2]);
    await notice.getByRole("button", { name: "重新驗證" }).click();
    await expect(notice.getByText(VERIFY_FAILED_MESSAGE)).toBeVisible();
    await expect(notice.getByText("有 2 段在新路網上不能騎")).toBeVisible();
    await expectNoHorizontalScroll(page);
    await expectPanelLeavesMapCenter(page, notice);
  });
});

// ---- 觸控與儲存失敗 ----

test.describe("觸控", () => {
  test.use({ hasTouch: true });

  /** 一根手指：按下、移動、放開（Playwright 的 touchscreen 只有 tap，所以直接送 CDP 的觸控事件）。 */
  async function touchDrag(page: Page, from: { x: number; y: number }, to: { x: number; y: number }, onMove?: () => Promise<void>) {
    const cdp = await page.context().newCDPSession(page);
    const send = (type: "touchStart" | "touchMove" | "touchEnd", point?: { x: number; y: number }) =>
      cdp.send("Input.dispatchTouchEvent", { type, touchPoints: point ? [{ x: point.x, y: point.y }] : [] });
    await send("touchStart", from);
    const steps = 8;
    for (let i = 1; i <= steps; i++) {
      await send("touchMove", { x: from.x + ((to.x - from.x) * i) / steps, y: from.y + ((to.y - from.y) * i) / steps });
    }
    await onMove?.();
    await send("touchEnd");
    await cdp.detach();
  }

  test("拖曳：手指抓住路線時地圖不平移，放開才送 /edit；沒抓到路線時手指拖曳平移地圖", async ({ page }) => {
    const engine = await mockEditEngine(page);
    await loadHi(page);
    const view = await mapView(page);
    const hit = pickOnRoute(view, engine.lastMatch!);
    await enterEdit(page);
    await tool(page, "拖曳").click();
    const frame = lockedFrame(page);
    const frameBefore = await pointsOf(frame);

    const target = { x: hit.x + 60, y: hit.y - 40 };
    await touchDrag(page, hit, target, async () => {
      expect(await pointsOf(frame)).toBe(frameBefore);
      expect(engine.edits).toHaveLength(0);
    });
    await expect.poll(() => engine.edits.length).toBe(1);
    expect(engine.edits[0].op.type).toBe("drag");
    await expect(page.getByText(EDITED_KM, { exact: true })).toBeVisible();
    expect(await pointsOf(frame)).toBe(frameBefore);

    // 對照：抓在路線以外的地方，手指拖曳會平移地圖
    await touchDrag(page, { x: view.box.x + view.box.width - 40, y: view.cy }, { x: view.box.x + view.box.width - 120, y: view.cy + 50 });
    await expect.poll(() => pointsOf(frame)).not.toBe(frameBefore);
    expect(engine.edits).toHaveLength(1);
  });
});

test("草稿存不了（瀏覽器不允許儲存）：微調照常、工具列提示這次沒存成草稿，我的路線說明不能儲存", async ({ page }) => {
  await page.addInitScript(() => {
    indexedDB.open = () => {
      throw new DOMException("storage blocked", "SecurityError");
    };
  });
  const engine = await mockEditEngine(page);
  await loadHi(page);
  const view = await mapView(page);
  await enterEdit(page);
  await cutOnRoute(page, engine, pickOnRoute(view, engine.lastMatch!));
  await expect(page.getByText(EDITED_KM, { exact: true })).toBeVisible();
  await expect(toolbar(page).getByText("這次修改沒有存成草稿（瀏覽器不允許儲存）：離開頁面會消失，請先下載 GPX")).toBeVisible();
  await expect(draftsPanel(page).getByText("這個瀏覽器不能儲存草稿，微調過的路線不會保留。")).toBeVisible();
  // 沒有草稿，但 GPX 仍是微調後的路線
  await expect(page.getByRole("button", { name: "下載 GPX" })).toBeEnabled();
});

// ---- 修正波次：審查發現的情境 ----

/** route-layers.ts 的路線線色 #2563eb（圖形段與微調換上的段）。 */
const ROUTE_BLUE = [0x25, 0x63, 0xeb] as const;

test("打開草稿時，路線不在畫面裡就把地圖移過去（鎖住的圖形沒有「移到畫面中央」可用）", async ({ page }) => {
  const engine = await mockEditEngine(page);
  await makeDraft(page, engine);
  const decoder = await page.context().newPage();
  expect(await countPixels(page, decoder, ROUTE_BLUE)).toBeGreaterThan(300);

  // 把地圖拖到別處（從圖形外的左下角拖到右上；右下角是 MapLibre 的版權圖示）：路線離開畫面
  const box = await mapEl(page).boundingBox();
  if (!box) throw new Error("找不到地圖");
  for (let i = 0; i < 2; i++) {
    await page.mouse.move(box.x + 15, box.y + box.height - 15);
    await page.mouse.down();
    await page.mouse.move(box.x + 15 + 600, box.y + box.height - 15 - 450, { steps: 8 });
    await page.mouse.up();
  }
  await expect.poll(() => countPixels(page, decoder, ROUTE_BLUE), { message: "地圖沒有被拖走" }).toBeLessThan(30);

  await draftsPanel(page).getByRole("button", { name: "打開 HI" }).click();
  await expect(toolbar(page)).toBeVisible();
  await expect.poll(() => countPixels(page, decoder, ROUTE_BLUE), { message: "打開草稿後路線不在畫面裡" }).toBeGreaterThan(300);
});

test("路網比 /status 新（引擎在頁面開著時重建）：新的路線不被當成舊路網，並重新讀 /status", async ({ page }) => {
  const engine = await mockEditEngine(page);
  let statusCalls = 0;
  // /status 還是開頁時的舊版本（20260101），吸附結果已經是 20261010
  await page.route("**/api/status", (route) => {
    statusCalls += 1;
    return route.fulfill({ json: statusOf("20260101T000000Z") });
  });
  await loadHi(page);
  await expect.poll(() => statusCalls, { message: "看到比 /status 新的版本沒有重新讀" }).toBeGreaterThanOrEqual(2);
  const settled = statusCalls;
  const view = await mapView(page);
  await enterEdit(page);
  await expect(page.getByTestId("reverify-notice")).toHaveCount(0);
  await cutOnRoute(page, engine, pickOnRoute(view, engine.lastMatch!));
  await expect(page.getByText(EDITED_KM, { exact: true })).toBeVisible();
  await expect(toolbar(page)).toBeVisible();
  await expect(page.getByTestId("reverify-notice")).toHaveCount(0);
  await expect(draftsPanel(page).getByRole("listitem")).toHaveCount(1);
  await expect(draftsPanel(page).getByText("舊路網")).toHaveCount(0);
  // 版本字串沒變就不會一直重新讀
  await page.waitForTimeout(800);
  expect(statusCalls).toBe(settled);
});

test("驗證進行中改了圖形（結束微調）：在途的 /verify 被取消、提示消失", async ({ page }) => {
  const engine = await mockEditEngine(page);
  await makeDraft(page, engine);
  await reloadOnNewBuild(page);
  await openOldDraft(page);
  const failed: string[] = [];
  page.on("requestfailed", (request) => failed.push(request.url()));
  let open = () => {};
  engine.verifyGate = new Promise<void>((resolve) => {
    open = resolve;
  });
  await page.getByTestId("reverify-notice").getByRole("button", { name: "重新驗證" }).click();
  await expect.poll(() => engine.verifies.length).toBe(1);

  // 改文字 = 移動圖形：結束微調、回到即時吸附，/verify 取消
  const textbox = page.getByRole("textbox", { name: "文字" });
  await textbox.fill("L");
  await textbox.blur();
  await expect(page.getByTestId("reverify-notice")).toHaveCount(0);
  await expect.poll(() => failed.some((url) => url.includes("/api/verify")), { message: "/verify 沒有被取消" }).toBe(true);
  open();
});

test("引擎沒有回 route_sig（舊版引擎）：路線與 GPX 照常，只是不能微調並說明原因", async ({ page }) => {
  const engine = await mockEditEngine(page);
  await page.route("**/api/match", async (route) => {
    const request = matchRequestSchema.parse(route.request().postDataJSON());
    engine.matches.push(request);
    engine.lastMatch = { ...matchResponseFor(request), route_sig: undefined };
    await route.fulfill({ json: engine.lastMatch });
  });
  await loadHi(page);
  await expect(page.getByRole("button", { name: "微調路線" })).toBeDisabled();
  await expect(page.getByText("這份吸附結果沒有簽章（引擎版本較舊），不能微調；仍可下載 GPX。")).toBeVisible();
  await expect(page.getByRole("button", { name: "下載 GPX" })).toBeEnabled();
  // 假引擎的 route.points 就是 GPX 的點
  const expected = engine.lastMatch!.route.points.map(([x, y]) => `<trkpt lat="${y.toFixed(6)}" lon="${x.toFixed(6)}"/>`);
  expect(await downloadGpxPoints(page)).toEqual(expected);
});

test("按「完成」之後再打開同一份草稿：接著微調（路線與復原紀錄都在），不重建草稿", async ({ page }) => {
  const engine = await mockEditEngine(page);
  await makeDraft(page, engine);
  const item = draftsPanel(page).getByRole("listitem");
  await item.getByRole("button", { name: "打開 HI" }).click();
  await expect(toolbar(page)).toBeVisible();
  await expect(page.getByText(EDITED_KM, { exact: true })).toBeVisible();
  // 歷史還在：能復原剛才的剪掉（重新打開的話歷史會重來，復原是停用的）
  await expect(page.getByRole("button", { name: "復原" })).toBeEnabled();
  await expect(item).toHaveCount(1);
});

test("刪除失敗後再打開同一份草稿：之後的編輯照樣存（不被刪除前的 discard 擋掉）", async ({ page, allowedErrors }) => {
  allowedErrors.push(/刪除草稿失敗/);
  const engine = await mockEditEngine(page);
  const { view } = await makeDraft(page, engine);
  const item = draftsPanel(page).getByRole("listitem");
  await page.getByRole("button", { name: "繼續微調" }).click();
  await expect(toolbar(page)).toBeVisible();
  // 下一次 IndexedDB 的刪除丟錯（只這一次）
  await page.evaluate(() => {
    const original = IDBObjectStore.prototype.delete;
    IDBObjectStore.prototype.delete = function () {
      IDBObjectStore.prototype.delete = original;
      throw new DOMException("測試用的刪除失敗", "AbortError");
    };
  });
  await item.getByRole("button", { name: "刪除 HI" }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: "刪除" }).click();
  await expect(draftsPanel(page).getByRole("alert")).toContainText("刪除草稿失敗");
  await expect(item).toHaveCount(1);

  // 草稿還在：打開它、再微調一次，要存進去
  await item.getByRole("button", { name: "打開 HI" }).click();
  await expect(toolbar(page)).toBeVisible();
  await cutOnRoute(page, engine, pickOnRoute(view, editedResponseFor(engine.edits[0], 1)));
  await expect(page.getByText("2.10 km", { exact: true })).toBeVisible();
  await page.reload();
  await expect(draftsPanel(page).getByText("2.10 km")).toBeVisible();
});
