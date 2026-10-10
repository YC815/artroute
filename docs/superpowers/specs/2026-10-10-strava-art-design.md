# StravaArt 設計文件

- 日期：2026-10-10
- 狀態：P0 完成（見 `docs/p0-findings.md`）；P1 前依 P0 發現修訂（2026-10-10，使用者授權 Claude 依最佳實踐決定，修訂摘要見 §8）
- 代號：網站 `artroute`、引擎 `artroute-engine`

## 1. 目標

做一個騎車用的 GPS art 路線規劃網站：使用者把**文字**或**圖片**轉成的圖形放到地圖上，自由移動、旋轉、縮放，系統自動算出**最貼合圖形、而且合法可騎（不逆向）**的真實路線；並提供**自動搜尋**，在指定範圍內找出最貼合的擺法。

### 已確認的需求

| 項目 | 決定 |
|---|---|
| 交通方式 | 自行車。必須遵守單行道（含 `oneway:bicycle`）、禁止騎車的道路、轉向限制 |
| 地區 | 只有台灣 |
| 使用者 | 朋友小圈子；不做登入，網址自行保管 |
| 圖形來源 | 英文文字、圖片。不做中文字、不做手繪、不做 AI 生成 |
| 自動搜尋的範圍 | 使用者指定中心點與半徑 R，**最終路線的起點**必須落在圈內；作品本身可延伸到圈外 |
| 自動搜尋的公里數 | 最小、最大公里數只算作品路線本身，不含騎過去的路 |
| 輸出 | GPX 下載 |

### 不做（YAGNI）

帳號系統、雲端存檔、分享連結、中文字、手繪、AI 生成、海拔資訊、直接上傳到 Strava/Garmin。

## 2. 參考：RouteSketcher 研究摘要

2026-10-10 實際操作 routesketcher.com，並讀了它前端吸附演算法（`3omc-u0-r66kj.js`）。以下是我們要避開的問題：

- **道路圖是雙向的，完全不讀 `oneway`**。快速道路沒有禁止，只是加權 3 倍；實測路線會走上洲美快速道路。官方 FAQ 也承認「snapping follows the map, not the traffic rules」。
- 吸附是貪婪的逐段 A*：每 60m 取一點，最多往前看 600m，迭代上限 1000 次。找不到路就直接拉直線。實測 5.15 km 的愛心吸附後變成 10.82 km，路線有大量鋸齒。
- 道路只載入最初畫面的範圍。圖形移出範圍後就不會吸附。
- Snapping 模式和 Freestyle 模式的區別不明顯，使用者容易以為它沒有幫忙算。
- 沒有自動搜尋擺放位置的功能。
- 文字使用 Hershey 單筆畫字型，字母從底部接到下一個字母。

它的技術：Next.js、MapLibre GL + OpenFreeMap、Supabase；後端 `/api/load-roads` 代理 Overpass 格式的 OSM 資料，吸附和 potrace 描邊都在瀏覽器裡跑。

## 3. 架構

```
瀏覽器 ──HTTPS──▶ Cloudflare Tunnel（公開，加 rate limiting）
                      │
                      ▼
              artroute（Next.js）                  ← 對外
                      │ Docker 內網 http://artroute-engine:8080
                      ▼
              artroute-engine（Python）            ← 只在內網，不對外
               ├─ FastAPI：POST /match、POST /search（SSE）、GET /status
               ├─ pyvalhalla：在同一程序內呼叫 Valhalla
               └─ /data：OSM pbf、Valhalla tiles、可騎道路 FlatGeobuf
```

### 併發模型（P0 發現：`valhalla.Actor` 不是 thread-safe）

- `Router` 內部用鎖序列化對 Actor 的呼叫；同一個 `Router` 同時只處理一個請求。
- 平行靠**多行程**：uvicorn 開 `ENGINE_WORKERS` 個 worker（預設 2），每個 worker 有自己的 `Router`。
- 每個 worker 依 `/data/current` 指向的 build 快取 `Router`：每個請求先讀 symlink，build 變了就建新的 `Router` 再替換（舊的請求在舊實例上跑完）。這同時就是 §6.1 的熱重載，不需要重啟。
- 載入時覆寫設定（不必重建 tiles）：`mjolnir.tile_dir` 改成相對 `valhalla.json` 所在目錄的 `tiles/`、`mjolnir.max_cache_size` 設為 256 MiB（每個 worker 一份）。
- 吸附逾時（§5）從進入佇列算起，包含等鎖的時間。

### Repo 結構（monorepo）

```
StravaArt/
  web/       Next.js（App Router）+ shadcn/ui + MapLibre GL，TypeScript，pnpm
  engine/    Python + FastAPI，uv 管理；numpy、scipy、shapely、pyvalhalla
  docs/
```

- 底圖使用 OpenFreeMap，免費、不需要金鑰。
- 瀏覽器不會直接存取引擎。`web` 的 Route Handler 負責轉發 `/api/match`、`/api/search`，並把 SSE 串流原樣轉回瀏覽器。
- 前後端契約：FastAPI 產生 OpenAPI，`web` 用 `openapi-typescript` 產生型別。

### 部署（m1，透過 hl）

- `artroute`：一般 hl app，最後以 `hl public` 公開。使用者已於 2026-10-10 預先授權公開（P3 加上 rate limiting 後執行）。
- `artroute-engine`：hl app，使用**新增的 `hl deploy --internal`**。不建 Tunnel 路由和 DNS，只掛在 hl 的共用 Docker 網路上。
- **hl 需要修改**：新增 `--internal` 模式（compose 照常產生，跳過 Cloudflare 路由、DNS、Access 設定，以及 proxy site）。

## 4. 元件

### 4.1 圖形來源（web，Web Worker）

兩種來源都輸出同一種資料結構，**不做任何連接**：

```ts
type Stroke = { points: [number, number][]; closed: boolean }  // 正規化座標
type Shape = { strokes: Stroke[]; kind: "text" | "image" }
```

- **文字**
  - 支援英文字母、數字、`♥ ! ? & . -`，最多 20 字、1 到 2 行（每行最多 10 字）。
  - 字型為 Hershey Sans（Roman Simplex）和 Script。每個字母的每一筆，各自是一個 `Stroke`。
- **圖片**
  - 支援 PNG/JPG/WebP，最大 5 MB。
  - 處理流程：灰階化 → Otsu 二值化（門檻可以用滑桿調）→ `d3-contour`（marching squares）抽輪廓 → Douglas-Peucker 簡化。
  - 不用 potrace：它是 GPL-2.0，前端打包會讓整個網站受 GPL 約束；`d3-contour` 是 ISC 授權，輪廓品質對路線用途足夠。
  - 預設選最大的外框輪廓；使用者可以點選加入其他輪廓。

### 4.2 地圖編輯器（web）

- 操作：拖曳本體可以移動，拉角落把手可以等比縮放，拉旋轉把手可以旋轉。使用 Pointer Events，滑鼠和觸控通用；鍵盤方向鍵可以微調。
- 畫面同時顯示目標圖形（虛線）和吸附後的路線（實線），路線上有方向箭頭和起點、終點標記。
- 即時吸附：
  - 拖曳中每 300ms 節流送出一次，放開時立即送出。
  - 舊請求用 `AbortController` 取消，回應用遞增序號比對，避免舊結果覆蓋新結果。
- 資訊面板：目標長度、實際路線長度、貼合度（覆蓋、乾淨兩個指標分開顯示）、目前路網資料的日期。
- 被禁行或單行道迫使偏離的路段，在地圖上醒目標示：來源是 `/match` 回應中 `kind` 為 `detour` 的路段（§4.3），連接線（`connector`）用另一種較淡的樣式。
- 部件完全吸附不到時（`warnings`），該部件的目標線標紅並顯示原因，其餘部件照常顯示。
- 即時吸附時，被取消的前端請求不會中斷引擎正在跑的計算；靠 §3 的多 worker 讓新請求不必排在舊請求後面。
- 目前作品自動存在 `localStorage`，GPX 可以下載。
- 版面以桌機優先，手機也能用。

### 4.3 部件吸附（engine）

- 網站把每個 `Stroke` 依照目前的擺放轉成經緯度後送出；引擎每隔約 25m 取一個點。封閉部件在尾端補上起點。
- 基本呼叫：Valhalla `trace_route`，`costing=bicycle`、`shape_match=map_snap`。
  - `search_radius`：預設依圖形對角線取 `clamp(對角線 × 0.02, 30, 150)` m，請求可以覆寫（30–150）。上限 150 m 需要 `service_limits.trace.max_search_radius` 與 `meili.default.max_search_radius` 兩個設定（P0 已處理）。
  - `turn_penalty_factor`：meili 對 bicycle 預設已是 140；由基準測試集調整，不是「開啟」。
- **P0 發現**：`map_snap` 不會繞路。部件含逆向段、或點落在半徑外時，結果會退化或被截斷，而且截斷點之後的合法段也一起消失；`Router.trace()` 會把這些情況變成 `RoutingError`。所以「正反兩個方向都算」不足以處理同時穿過兩段相反單行道的部件，改用**分治吸附**：
  1. 先整段 `trace`；成功就結束（`kind=stroke`）。
  2. 失敗就把部件從中間對半切開，兩半各自遞迴。
  3. 切到短於 `MIN_PIECE_M`（預設 `2 × search_radius`）仍失敗的小段，改用 `route(小段起點, 小段終點)` 取得合法路徑（`kind=detour`）。這是唯一允許偏離圖形的地方，而且一定是合法的自行車路線。
  4. 相鄰兩段的接點相差超過 1 m 時，用 `route(前段終點, 後段起點)` 接起來（`kind=detour`）。
  - 好處：大部分合法的部件只呼叫一次 `trace`；只在真正違規的地方才局部繞路，形狀其餘部分完整保留；不依賴 `trace_attributes` 的 `matched_points` 細節（文件不完整，已知有 edge_index 異常值的 bug）。
  - 小段的起點或終點附近 `route` 也找不到路（例如 150 m 內沒有可騎道路）時，該小段略過並在 `warnings` 記錄，**不畫直線**；兩側的段落用 `route` 接起來。
- 所有參數（`MIN_LENGTH_RATIO`、`MIN_PIECE_M`、半徑公式、`turn_penalty_factor`）都以基準測試集（§6.3）的量化結果為準來調整。

### 4.4 串接器（engine）

在地圖階段，把各部件的吸附結果串成**一條連續路線**：

- 決策變數：部件的順序、每個部件的方向、封閉圈的進出點（沿圈每隔約 50m 一個候選點）。
- 連接線：用 Valhalla 算兩點之間的合法自行車路線，並取 `alternates` 增加選擇。
- 目標函數：最小化「**多出來的可見線條長度**」，也就是連接線中沒有和已畫路線重疊的部分（以 15m 緩衝區判定），總長度只當次要權重。
- 起點限制：自動搜尋時，起點必須落在圈內，而且越靠近中心越好。
- 演算法：先貪婪建構，再用 2-opt 局部優化。部件預期不超過 20 個。
- **P1 的範圍（v1）**：
  - 每個部件只用它吸附後的方向（分治吸附已處理單行道），不另外算反方向。
  - 封閉部件可以從圈上任一點進出：沿吸附後的圈每 50 m 一個候選點，進出點相同（騎完一圈回到原點）。
  - 順序與進出點的選擇用直線距離估計（貪婪＋2-opt），選定後才對實際採用的接點呼叫 `route()` 取得連接線（`kind=connector`）。
  - 「與已畫路線重疊」的連接線成本折減、`alternates` 留到基準測試證明有需要時再做（YAGNI）；`connector` 長度照實計入評分的「乾淨」指標。
  - 自動搜尋的起點限制屬於 P2。

### 4.5 貼合度評分（engine）

目標圖形和最終路線都每隔 10m 取樣，計算兩個指標：

- **覆蓋**：目標取樣點到路線的距離，衡量「該畫的有沒有畫到」。
- **乾淨**：路線取樣點到目標圖形的距離，衡量「有沒有多出不該有的線」。

兩個指標各取平均和 90 百分位，再除以圖形對角線長度做正規化，最後換算成 0–100%。路線方向不影響分數。

- 計算方式（v1，參數由基準測試集調整）：距離在以圖形中心為原點的局部平面（公尺）上算點到折線的最短距離；`e = (平均 + P90) / 2 / 對角線`；`分數 = round(100 × max(0, 1 − e / 0.05))`，也就是平均偏離達對角線 5% 時為 0 分。
- `overall = round((覆蓋 + 乾淨) / 2)`。

### 4.7 引擎 API 契約（P1）

`POST /match`（由 web 的 `/api/match` 轉發）：

```jsonc
// 請求
{
  "strokes": [{ "points": [[121.5, 25.03], ...], "closed": false }],  // 經緯度 (lon, lat)，1–40 筆，每筆 2–2000 點
  "search_radius_m": 60          // 可省略；30–150
}
// 回應 200
{
  "route": { "points": [[lon, lat], ...], "length_m": 5230.4 },      // 一條連續、合法的自行車路線
  "segments": [                                                         // route 依序切成的段落，首尾相接
    { "kind": "stroke" | "detour" | "connector", "stroke_index": 0 | null, "points": [[lon, lat], ...], "length_m": 812.0 }
  ],
  "score": { "coverage": 0-100, "cleanliness": 0-100, "overall": 0-100 },
  "target_length_m": 4980.2,
  "warnings": [{ "stroke_index": 2, "code": "no_route" | "partial", "message": "繁中說明" }],
  "data_version": "20261010T145928Z"
}
```

- 錯誤：`422` 輸入不合法（pydantic）、`400` 圖形不在台灣範圍（經度 118.1–122.1、緯度 21.8–26.4，含金門、馬祖）、`503` 沒有可用路網、`504` 超過 5 秒、`422` 所有部件都吸附失敗（附 `warnings`）。
- 型別來源：引擎的 OpenAPI 是唯一真相；`web` 用 `openapi-typescript` 從 `engine/openapi.json`（引擎測試會檢查它與程式一致）產生型別。

### 4.6 自動搜尋（engine）

**輸入**：`Shape`、中心點、半徑 R（上限 10 km）、最小/最大公里數、旋轉範圍（預設文字 ±15°、圖片不限，使用者可以調整）。

1. **縮放範圍**：用「圖形總長 × 繞路係數 1.1–1.4，再加上部件間距的估計」反推縮放倍率的上下限。
2. **粗篩**（numpy 向量化）
   - 從 FlatGeobuf 取出範圍內可騎的道路，以 10m 網格畫成距離場（EDT），同時記錄最近道路的方向。
   - 每個候選的成本 = 取樣點到道路的截斷距離，加上「圖形切線和道路方向不一致」的懲罰。
   - 候選網格：位置每 150m 一格、角度每 10° 一格、大小 6 級。R=3 km 時大約 27 萬個候選。
   - 起點條件放寬成：至少有一個可以當起點的位置落在圈內。
3. **去重與微調**：非極大值抑制（NMS）留下 30 個彼此差異夠大的候選，再用連續座標做局部最佳化，微調位置、角度、大小。
4. **精算**
   - 前 K=10 名跑完整的吸附、串接、評分。
   - 公里數不在範圍內的，依實際比例修正縮放倍率後重算一次。
   - 回傳前 5 名，附上分數、公里數、起點離中心的距離。

- **進度回報**：用 SSE 回報目前步驟、進度百分比，並即時預覽目前最佳解。
- **目標時間**：R ≤ 5 km 時 30 秒內完成。

## 5. 錯誤處理

| 情況 | 處理方式 |
|---|---|
| 部件附近沒有可騎道路 | 該部件標紅並說明原因，其他部件照常計算。**絕不用直線補**，GPX 只含合法路段 |
| 部件與單行道方向衝突、或穿過街廓（trace 被截斷） | 不是錯誤：分治吸附（§4.3）在衝突處改用合法繞路（`detour`），地圖上醒目標示；只有連繞路都找不到時才記 `warnings` |
| 超出台灣範圍 | 擋下並說明 |
| 搜尋結果都不符合公里數 | 仍回傳最接近的幾個，標出實際公里數，並建議放寬範圍或半徑 |
| 引擎無法服務或正在換資料 | 網站顯示橫幅提示，停用吸附 |
| 輸入不合法 | 引擎用 pydantic、網站用 zod 驗證；點數、圖片大小、半徑都設上限 |
| 逾時 | 吸附 5 秒（含等 `Router` 鎖的時間）、搜尋 60 秒硬上限 |
| 使用者離開頁面 | SSE 連線中斷即取消搜尋 |
| 濫用 | Cloudflare rate limiting，加上引擎搜尋的併發上限 1、其餘排隊並顯示排隊位置 |

## 6. 資料與測試

### 6.1 路網資料更新

1. 每週日 04:00（台北時間）下載 Geofabrik `taiwan-latest.osm.pbf`，並驗證 md5。
2. **快速道路標記（P0 發現：台灣快速道路多標成 `highway=trunk`，Valhalla 對 bicycle 視為可騎）**：建圖前用 pyosmium 產生修補過的 pbf，把符合下列任一條件的 `highway=trunk`／`trunk_link` 加上 `bicycle=no`：
   - `motorroad=yes`；
   - `name` 含「快速道路」「快速公路」「快速路」或「高架」；
   - `ref` 是快速公路編號：台61、台61甲、台62、台62甲、台64、台65、台66、台68、台72、台74、台76、台78、台82、台84、台86、台88（含 `;` 分隔的多值）。
   - 另有 `engine/src/engine/data/bicycle_overrides.toml`：`deny`／`allow` 兩個 way id 清單，最後套用，供人工修正誤判。
   - 修補過的 way id 清單存在 build 目錄（`bicycle_denied_ways.json`），供驗證查詢使用。
3. 在 `/data/builds/<日期>/` 建出 Valhalla tiles 和可騎道路 FlatGeobuf。建圖在子程序中以低優先權執行，同一時間只允許一個建圖。
4. 執行固定的驗證查詢：
   - 洲美街單行段不可逆向（P0 已有）；
   - 快速道路不可騎：取被標記的 way 中名稱為「洲美快速道路」者的首尾節點，bicycle `route` 必須存在、而且不得使用任何被標記的 way；
   - 標記數量合理：被標記的 way 數必須大於 0（資料或規則壞掉時明確失敗）。
5. 通過後，原子切換 `/data/current` symlink；各 worker 在下一個請求時自動換用新 `Router`（§3）；保留兩版以便回滾。任何一步失敗，舊資料照常服務。

### 6.2 自動化測試

- **engine（pytest）**
  - 評分、串接器、搜尋各步驟的單元測試，使用合成棋盤路網，不需要 Valhalla。
  - 整合測試使用一小塊台北的 OSM 裁切，跑真的 Valhalla。
- **web（vitest）**：幾何轉換、Hershey 筆畫輸出、圖片描邊流程。
- **e2e 冒煙測試**：文字 → 放到地圖 → 吸附 → 下載 GPX。

### 6.3 基準測試集

- 固定一組「圖形 × 地點」案例（台北棋盤區、台中、河濱等），記錄覆蓋、乾淨、公里數、耗時。
- 同時量測粗篩排名和精算排名的相關性，作為調整 K 值的依據（P2）。
- 真實路網太大，不進 CI：基準測試在 m1 的引擎容器內執行（`python -m engine.bench`），結果與 repo 內的 `engine/bench/baseline.json` 比較，分數退步超過門檻（預設 3 分）即以非零狀態結束。CI 只跑合成地圖上的同一套流程，確保基準測試程式本身沒壞。

## 7. 分期

| 階段 | 內容 | 完成條件 |
|---|---|---|
| P0 驗證 | pyvalhalla 在 m1（linux arm64 容器）的可用性；台灣 tiles 建置；`bicycle` 的 `trace_route` 是否遵守單行道；hl `--internal` | ✅ 2026-10-10 完成（`docs/p0-findings.md`） |
| P1 手動模式 | 引擎：分治吸附、串接器、評分、`/match`、多 worker 與熱換、快速道路標記與驗證、基準測試集；網站：文字/圖片、移動/旋轉/縮放、即時吸附、GPX。兩條線依 §4.7 契約平行開發 | 可以在 m1 上完整做出一條作品並匯出 GPX（網站先維持 hl 預設鎖定） |
| P2 自動搜尋 | §4.6 全部內容 | R=5 km 時 30 秒內回傳前 5 名 |
| P3 上線收尾 | 每週自動更新資料、rate limiting、公開部署 | 公開前經使用者確認 |

### P0 的退路

如果 pyvalhalla 沒有 linux arm64 wheel，或者沒有附帶建 tiles 的工具：改用官方 Valhalla image，作為第三個內網容器，引擎以 HTTP 呼叫它。其他設計不變。

## 8. P0 後的設計修訂摘要（2026-10-10）

使用者去睡前授權 Claude 依最佳實踐自行決定；以下每項都可以被推翻。

| # | 修訂 | 理由（P0 發現） | 被否決的替代方案 |
|---|---|---|---|
| 1 | 部件吸附改為分治：整段 trace → 失敗就對半切 → 最小段改用合法 `route` 繞路（§4.3） | `map_snap` 遇到逆向段或半徑外的點會退化／截斷，且截斷點之後的合法段一起消失 | 只在部件兩端點間 `route`：會丟掉形狀（L 形被切角）。解析 `trace_attributes` 的 `matched_points` 找截斷點：文件不完整、已知 edge_index 異常值，P1 先不依賴 |
| 2 | 快速道路在建圖前標成 `bicycle=no`（名稱、`ref`、`motorroad` 規則＋人工覆寫清單），並加入驗證查詢（§6.1） | 單純 `highway=trunk` 對 bicycle 可騎；台灣快速道路多標 trunk，洲美快速道路尚未在真實資料上驗證 | 全面禁止 trunk：台 1 線等一般省道也是 trunk，會誤殺大量可騎道路。只靠 OSM 原有標籤：漏標風險無法控制 |
| 3 | 多行程平行：每個 uvicorn worker 一個 `Router`，依 `/data/current` 自動換新（§3） | Actor 不是 thread-safe，兩個 thread 共用會 segfault | 單一行程＋鎖：即時吸附時新舊請求互相排隊。每個請求建新 Actor：載入成本高 |
| 4 | 圖片描邊改用 `d3-contour`（§4.1） | potrace 是 GPL-2.0，會讓前端程式碼受 GPL 約束 | potrace WASM、OpenCV.js（數 MB，殺雞用牛刀） |
| 5 | 串接器 v1 只用吸附後的方向，連接線成本用直線距離估計（§4.4） | 分治吸附已處理單行道；YAGNI，等基準測試證明需要再加 | 正反方向都算、`alternates`：吸附呼叫數加倍 |
| 6 | 基準測試在 m1 上對真實路網跑，與 repo 內 baseline 比較；CI 只跑合成地圖（§6.3） | 台灣 tiles 336 MiB，不適合放進 CI | 在 CI 下載並建台灣路網：每次 3 分鐘以上、依賴 Geofabrik 頻寬 |
