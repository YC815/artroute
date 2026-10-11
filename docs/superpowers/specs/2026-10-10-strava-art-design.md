# StravaArt 設計文件

- 日期：2026-10-10
- 狀態：P0 完成（見 `docs/p0-findings.md`）；P1 前依 P0 發現修訂（2026-10-10，使用者授權 Claude 依最佳實踐決定）；P1.5 依吸附品質調查修訂（2026-10-11）；P2 自動搜尋與評分 v2（2026-10-11，雲端實作，真實路網的量測待本機）；修訂摘要見 §8
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
               └─ /data：OSM pbf、Valhalla tiles、可騎道路索引（rideable_*.npy，§6.1）
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
  - 拖曳中最多一筆吸附請求在途：節流計時器到期（兩次送出至少相隔 300 ms）時，若前一筆還沒回來，不取消它，只記住最新位置；它回來（成功或失敗）後，最新位置與它不同才補送，補送同樣遵守 300 ms 間隔。因此單次吸附超過 300 ms 時，拖曳過程中仍會逐筆看到更新。前端沒有自己的逾時：卡住的請求最久擋住拖曳中的更新到代理的逾時（10 s），放開時會立刻取消它。
  - 放開、鍵盤微調、改文字、換輪廓立刻送出，並用 `AbortController` 取消在途的請求（最終位置優先；引擎在連線中斷時取消計算，回 499）。回應用遞增序號比對，被取消的結果丟棄，舊結果不會覆蓋新結果；失敗的請求可以再送，但在途失敗而最新位置與它相同時不自動重送，要等下一次操作（再動一下、重新吸附）才重送。
- 資訊面板：目標長度、實際路線長度、貼合度（覆蓋、乾淨兩個指標分開顯示）、目前路網資料的日期。
- 被禁行或單行道迫使偏離的路段，在地圖上醒目標示：來源是 `/match` 回應中 `kind` 為 `detour` 的路段（§4.3），連接線（`connector`）用另一種較淡的樣式。
- 部件完全吸附不到時（`warnings`），該部件的目標線標紅並顯示原因，其餘部件照常顯示。
- 前端取消請求（放開、微調、離開頁面）時，引擎會偵測到連線中斷：排隊中的立刻離開佇列，吸附中的在下一次檢查（每次呼叫 Valhalla 之前、每一輪 CPU 計算之間）停下來，回 499。499 是呼叫端已經離開時才有的回應，不會有人收到，所以不列入 §4.7 契約與 OpenAPI。正在跑的那一次 Valhalla 呼叫本身無法中斷，§3 的多 worker 讓新請求不必排在整筆舊請求後面。
- 目前作品自動存在 `localStorage`，GPX 可以下載。
- 版面以桌機優先，手機也能用。
- **自動搜尋**（P2，`web/src/components/studio/search-panel.tsx`，契約見 §4.7）：
  - 按「設定搜尋範圍」才建立範圍：圓心是目前圖形的中心、半徑 3 km（0.5–10 km 滑桿，放開才生效）；可以改成地圖中心或目前圖形的位置。範圍不存進 `localStorage`。
  - 範圍存在時，每個即時 `/match` 都帶 `start_circle`（起點提示），資訊面板多一列「起點離圓心」（超出半徑標「（圈外）」）；「清除範圍」移除範圍與提示。
  - 參數：公里數預設是目標長度的 1.2–2.5 倍（取到 0.1 km，夾在 1–300、至少差 1 km，使用者改過就固定）；旋轉範圍是以正北為 0 的絕對角度 ±N°（文字 15°、圖片「不限」= 180°）。
  - 搜尋中顯示排隊位置、階段與進度（粗篩 0–40%、微調 40–55%、精算 55–100%，比例待真實量測調整）、地圖上的最佳預覽；結果列出最多 5 個候選（整體貼合度、公里數、起點離圓心，公里數不符的標記），點一個就把擺放套用到編輯器（等同放開拖曳），之後的手動調整、即時吸附與 GPX 匯出完全沿用。
  - 搜尋跟著圖形走：圖形換了就取消進行中的搜尋、隱藏結果。`/status` 的 `search_ready` 為 false（舊引擎、舊 build）或引擎離線時停用並說明；`no_search_data` 不算引擎離線（吸附照常）。

### 4.3 部件吸附（engine）

- 網站把每個 `Stroke` 依照目前的擺放轉成經緯度後送出；引擎每隔約 25m 取一個點。封閉部件在尾端補上起點。
- 基本呼叫：Valhalla `trace_route`，`costing=bicycle`、`shape_match=map_snap`。
  - `search_radius`：預設依圖形對角線取 `clamp(對角線 × 0.035, 30, 150)` m，請求可以覆寫（30–150）。上限 150 m 需要 `service_limits.trace.max_search_radius` 與 `meili.default.max_search_radius` 兩個設定（P0 已處理）。
  - `turn_penalty_factor`：meili 對 bicycle 預設已是 140；由基準測試集調整，不是「開啟」。
- **P0 發現**：`map_snap` 不會繞路。部件含逆向段、或點落在半徑外時，結果會退化或被截斷，而且截斷點之後的合法段也一起消失；`Router.trace()` 會把這些情況變成 `RoutingError`。所以「正反兩個方向都算」不足以處理同時穿過兩段相反單行道的部件，改用**分治吸附**：
  1. 先整段 `trace`；成功就結束（`kind=stroke`）。
  2. 失敗就把部件從中間對半切開，兩半依序遞迴；左半 trace 成功時，右半的第一個點換成左半吸附後的實際終點（串接），兩半在切點自然接上（左半最後是失敗的小段時，右半照原本的輸入點開始）。
  3. 切到短於 `MIN_PIECE_M`（預設 `2 × search_radius`）仍失敗的小段，改用 `route(小段起點, 小段終點)` 取得合法路徑（`kind=detour`）。這是唯一允許偏離圖形的地方，而且一定是合法的自行車路線。
  4. 相鄰兩段可以直接接的條件是：接點相差 ≤ 1 m，而且接點轉角超過 30° 時 Valhalla 會直接騎過去（從接點前 8 m route 到接點後 8 m，長度不比沿原路線多 3 m 以上；細節見下方「接點轉彎檢查」）。不符合就用 `route(前段終點, 後段起點)` 補（`kind=detour`），補上的兩個新接點照樣檢查；補不上，先把沒接上的那一端沿既有路段裁掉 20／50／100 m 再試，仍不行就斷開成不同的 chain（不畫直線）。
  - 繞路、補橋、connector 與轉彎檢查的 `route` 都用 bicycle `costing_options.shortest=true`（只換成本，通行規則不變）；`trace` 不變。
  - 接點轉彎檢查（`matching/turns.py`）：從接點沿路線往回、往前各走約 8 m 得到兩個探針點，探針會跨過路段邊界（短路段不會讓轉彎量不到）。兩條臂的轉角 ≤ 30° 就放行；超過時，兩個探針點之間的 shortest route 必須兩端都落在探針點 1 m 內，長度不比「沿原路線經過接點」多 3 m 以上，否則這個轉彎 Valhalla 不會直接騎（轉向限制、立體交叉在平面上剛好是同一點……），視同沒接上。原地迴轉（兩個探針落在同一處）沒辦法用 route 量，一側可走的路線不到 1 m（路線的頭或尾）也量不出來，這兩種不檢查；探針走了一段路卻折回接點本身（走的路比直線距離多出超過 1 m）時量不出通過接點的方向，但這不是迴轉，視同不能騎，兩邊都折回更是。接上新幾何時，沿路線（以幾何長度算）離新接點不到 8 m 的既有接點，探針會伸進新幾何，也一併重量，沒過的算那一側沒接上。沒過的接點一律走「裁切重試，否則斷開」，絕不輸出。只有轉角 > 30° 的接點會多一次短 route。
  - 好處：大部分合法的部件只呼叫一次 `trace`；只在真正違規的地方才局部繞路，形狀其餘部分完整保留；不依賴 `trace_attributes` 的 `matched_points` 細節（文件不完整，已知有 edge_index 異常值的 bug）。
  - 小段的起點或終點附近 `route` 也找不到路（例如 150 m 內沒有可騎道路）時，該小段略過並在 `warnings` 記錄，**不畫直線**；兩側的段落斷成不同的 chain（不拿 `route` 補同一個缺口，否則被拒絕的塌縮繞路會換個名字被收下），交給串接器（§4.4）當成不同的部件接起來。封閉筆畫是例外：§4.4 的縫合會用 connector 把相鄰的 chain 接回去，可能補上這裡刻意斷開的缺口（標 `connector`，不是 `detour`；該筆畫仍有 `partial` 警告）。
- 所有參數（`MIN_LENGTH_RATIO`、`MIN_PIECE_M`、半徑公式、`turn_penalty_factor`）都以基準測試集（§6.3）的量化結果為準來調整。

### 4.4 串接器（engine）

在地圖階段，把各部件的吸附結果串成**一條連續路線**：

- 決策變數：部件的順序、每個部件的方向、封閉圈的進出點（沿圈每隔 max(50 m, 周長 / 24) 一個候選點，所以大圈最多約 24 個）。
- 連接線：用 Valhalla 算兩點之間的合法自行車路線，並取 `alternates` 增加選擇。
- 目標函數：最小化「**多出來的可見線條長度**」，也就是連接線中沒有和已畫路線重疊的部分（以 15m 緩衝區判定），總長度只當次要權重。
- 起點限制：自動搜尋時，起點必須落在圈內，而且越靠近中心越好。
- 演算法：先貪婪建構，再用 2-opt 局部優化。部件預期不超過 20 個。
- **P1 的範圍（v1）**：
  - 開放筆畫只吸出一條完整的 chain（單一 chain、沒有警告）時可以反向走：串接器選了反向，就把整筆畫的點倒過來重新吸附（不是把現成路線倒過來，所以單行道照樣遵守）。重新吸附只對整段 trace 一次，不對半切、不補繞路，每個被選上反向的部件最多多 1 次 trace；這一次 trace 失敗（含逆向被截斷、吸附不完整）就照畫的方向走。第 0 個部件（規劃的起點）固定正向；封閉筆畫、斷成多個部件的開放筆畫不反向。
  - 組裝從錨點開始：錨點是吸附後 `stroke` 段長度（貼著畫線騎的公尺數）合計最長的部件，繞路（`detour`）與連接線不算，長度差不到 0.1 m 視為一樣長、取索引小的。排在錨點後面的部件依規劃順序接在尾端，前面的由近到遠接在開頭。原定那一端接不上的部件先擱著，其他部件照規劃接完再補：原定那一端後來接了別的部件才在原端重試，再試另一端，兩端都接不上才丟（裁決 A7，預設文字 48 → 56）。不能立刻改接另一端：那會佔掉規劃中接在那一端的部件的位置。順序與進出點的規劃不變。
  - 封閉部件可以從圈上任一點進出：沿吸附後的圈每隔 max(50 m, 周長 / 24) 一個候選點（大圈的 2-opt 才不會失控），進出點相同（騎完一圈回到原點）。
  - 封閉筆畫斷成多條 chain 時，先依筆畫順序把相鄰的 chain 接起來（與 connector 相同的規則）；全部接上、而且首尾相鄰（≤ 1 m，接縫的轉彎能直接騎）才是圈；收尾不另外補路。中間有接不上的地方就從那裡斷開，最後一條與第一條相鄰時合成一條（斷點只剩真正接不上的地方）。縫合是封閉筆畫對 §4.3「不補同一個缺口」的例外：它用 connector 接相鄰的 chain，其中可能包含 §4.3 因 `MIN_LENGTH_RATIO` 拒絕塌縮或切角繞路而刻意斷開的缺口；補上的是 Valhalla 的合法路線，標 `connector`（不是 `detour`），而且那個筆畫仍然有 `partial` 警告。
  - 順序與進出點的選擇用直線距離估計（貪婪＋2-opt），選定後才對實際採用的接點呼叫 `route()` 取得連接線（`kind=connector`）。
  - 「與已畫路線重疊」的連接線成本折減、`alternates` 留到基準測試證明有需要時再做（YAGNI）；`connector` 長度照實計入評分的「乾淨」指標。
  - 自動搜尋的起點限制（P2，§8 第 25 列）：`compose(start=StartHint)` 規劃時從圈內離圓心最近的進入點出發（沒有就取整體最近的），2-opt 固定它；組裝照舊從錨點往兩端接，排第一的部件最後接在開頭，所以路線從它開始。它被擱置、改接另一端時起點會移走，由呼叫端檢查（搜尋淘汰起點在圈外的候選）。`start=None`（`/match` 沒帶 `start_circle`）時與 P1.5 完全相同。

### 4.5 貼合度評分（engine）

目標圖形和最終路線都在以圖形中心為原點的局部平面（公尺）上取樣，計算兩個指標：

- **覆蓋**：目標每隔 10 m 取樣，量取樣點到路線折線的距離，衡量「該畫的有沒有畫到」。
- **乾淨**：只看**看得到的線**（v2）：路線每隔 2.5 m 取樣、量化到 5 m 格子，每格留一點（取該格座標字典序最小的取樣點，與騎的方向無關），量這些點到目標折線的距離，衡量「有沒有多出不該有的線」。同一條街來回騎兩次在地圖上只是一條線，只算一次。

兩個指標各取平均和 90 百分位：`e = (平均 + P90) / 2`；`分數 = round(100 × max(0, 1 − e / Z))`，`Z = max(0.05 × 對角線, 60 m)`。`overall = round((覆蓋 + 乾淨) / 2)`。路線方向不影響分數。

- v1（P1–P1.5）的乾淨度用路線每 10 m 的取樣（重複騎的部分重複計入），`Z = 0.05 × 對角線`。保留成 `score_v1`，只給 bench 跟 v1 baseline 比較用（§6.3）。改版理由見 §8 第 21 列。
- 評分是自動搜尋精算的目標函數（§4.6）。

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


#### P2 新增（只增加，`/match` 不帶新欄位時行為與上面完全相同）

- `POST /match` 請求可以多帶 `"start_circle": { "center": [lon, lat], "radius_m": 300–10000 }`：串接器的起點提示（§4.4）。不保證做得到、不會因此報錯；網站自己用 `route.points[0]` 判斷起點在不在圈內。
- `GET /status` 多一個 `"search_ready": bool`：目前的 build 有沒有自動搜尋用的可騎道路索引（舊 build 沒有，要重建路網）。

`POST /search`（由 web 的 `/api/search` 轉發，回應是 SSE）：

```jsonc
// 請求
{
  "strokes": [{ "points": [[x, y], ...], "closed": false }],  // 圖形的正規化座標（網站的 normalizeStrokes：外框中心為原點、長邊 = 1、y 向上），1–40 筆、每筆 2–2000 點，|x|、|y| ≤ 1
  "center": [lon, lat],          // 圓心（台灣範圍內）
  "radius_m": 5000,              // 300–10000
  "min_km": 10, "max_km": 25,    // 作品路線本身（route.length_m）的公里數，不含騎過去的路；1 ≤ min ≤ max ≤ 300
  "rotation_min_deg": -15, "rotation_max_deg": 15  // 順時針、0 = 圖形上方朝北；-180 ≤ min ≤ max ≤ 180（不限 = -180、180）
}
// 回應 200（text/event-stream）：每個事件 `event: <type>` + `data: <JSON>`，另有每 15 秒的 `: ping`
{ "type": "queued", "position": 1 }                                   // 排隊中（前面還有幾個，含正在跑的）
{ "type": "progress", "stage": "coarse" | "refine" | "match", "done": 3, "total": 6 }
{ "type": "best", "candidate": SearchCandidate }                       // 目前最好的候選（即時預覽）
{ "type": "result", "candidates": [SearchCandidate, ...],              // 最後一個事件：最多 5 個，已排序
  "coarse_candidates": 123456, "evaluated": 12, "elapsed_ms": 18234, "notice": "…" | null }
{ "type": "error", "code": "timeout" | "busy" | "no_data" | "internal", "message": "繁中說明" }  // 或者以失敗結束
// SearchCandidate
{ "placement": { "center": [lon, lat], "size_m": 2400.0, "rotation_deg": 355.0 },  // 與網站的 Transform 相同（rotation 0–360）
  "match": MatchResponse,          // 這個擺放帶 start_circle 的吸附結果（與 /match 200 同形）
  "start_distance_m": 820.5,       // 路線起點離圓心；一定 ≤ radius_m（圈外的不回傳）
  "length_ok": true }              // route.length_m 在 [min_km, max_km] 內
```

- 串流開始前的錯誤是一般 JSON（與 `/match` 同形）：`400 outside_taiwan`（圓心）、`422 too_small`（所有點幾乎重合）或請求格式錯誤、`503 no_data`、`503 no_search_data`（目前的 build 沒有可騎道路索引）。
- 排序：公里數合格的在前，依 overall；不合格的依離範圍多遠、再依 overall；最後看起點離圓心多近。沒有合格的就回最接近的並附 `notice`。
- 引擎的 OpenAPI 把各事件的 schema 放在 components（`SearchQueued`…`SearchFailed`），web 自己組成 union 並用 zod 驗證；未知的事件類型略過（向前相容）。

### 4.6 自動搜尋（engine）

**輸入**：正規化圖形、中心點、半徑 R（300 m–10 km）、最小／最大公里數、旋轉範圍（網站預設：文字 ±15°、圖片不限，使用者可以調整）。契約見 §4.7。程式：`engine/src/engine/search/`。

1. **縮放範圍**（`coarse.size_bounds`）：路線長度 ≈ 大小 × (圖形長 × 繞路係數 + 部件間距)。繞路係數取 1.15–1.8（P1.5 bench 的 length/target 中位數 1.71 含連接線，連接線這裡另外估），部件間距用貪婪最近鄰估計。反推大小上下限，夾在 200 m–20 km（網站拖得出來的範圍）。
2. **粗篩**（numpy 向量化，`field.py`、`coarse.py`）
   - 從可騎道路索引（§6.1）取出範圍內的路段，在以圓心為原點的局部平面上畫成網格（10 m；範圍太大時放大格子，每邊最多 3000 格），用 EDT（scipy）得到每格到最近道路的距離與那條路的方向。
   - 每個擺放的成本：圖形上等距取樣，每點 `d = min(距離, T) / T`，`c = d + 0.5 × (1 − d) × |sin(圖形切線與道路方向的夾角)|`，取平均。`T` = 該大小的吸附搜尋半徑（與 `/match` 預設相同公式）× 3（§8 第 23 列）。
   - 候選網格：位置每 150 m、角度每 10°（整圈 36 個）、大小 6 級（幾何級數）。兩階段：A 對全部擺放只算距離項（48 個取樣點），B 對 A 的前 2000 名算完整成本（256 個取樣點）。
   - 起點條件放寬成：至少有一個可以當起點的位置（開放筆畫的兩端、封閉筆畫上的點）落在圈內。
3. **去重與微調**：非極大值抑制（大小差 < 15%、角度差 < 15°、中心距離 < 大小的 25% 算「差不多」）留下 30 個，再用雙線性內插的距離做 pattern search 微調位置、角度、大小（守住旋轉範圍、大小範圍與起點條件），再抑制一次留 16 個。
4. **精算**（`fine.py`）
   - 依粗篩順序跑完整的吸附、串接、評分，帶起點提示（`StartHint` = 搜尋的圓，§4.4）。路線起點不在圈內的直接淘汰（硬條件）。
   - 公里數不在範圍內的，依實際比例修正大小（目標放在範圍內側 10% 處）後重算一次，排在佇列最前面；同一個粗篩候選只留比較好的那個結果。
   - 平行：`SEARCH_ROUTERS` 個 Router（預設 2），每個 thread 固定用自己的 Router（Actor 不能共用）；Router 每次搜尋重建、搜尋結束就釋放（不常駐 tile 快取）。搜尋不佔 `/match` 的名額，即時吸附不必等搜尋。
   - 停止：跑滿 K = 10 個而且已有 5 個合格；或超過 25 秒而且已有 5 個起點在圈內的；或候選用完；或整個搜尋到期（60 秒，有結果就回傳已有的）。
   - 回傳前 5 名，附上分數、公里數、起點離中心的距離。

- **進度回報**：SSE（§4.7）回報排隊位置、目前步驟與進度，並即時預覽目前最佳解。
- **併發**：整個引擎同時 1 個搜尋（`search/gate.py`：檔案鎖跨 uvicorn worker 共用，同一個 worker 內先到先得），其餘排隊並回報位置（只算得到同一個 worker 內的等待者，是近似值）；排隊最多 120 秒。
- **目標時間**：R ≤ 5 km 時 30 秒內完成（§7）。雲端用合成資料量到粗篩 1–3 秒（R = 5 km、35 萬條路段，大圖形最多約 6.5 秒），精算的時間只能在真實路網量（`python -m engine.bench.search`，§6.3）。

## 5. 錯誤處理

| 情況 | 處理方式 |
|---|---|
| 部件附近沒有可騎道路 | 該部件標紅並說明原因，其他部件照常計算。**絕不用直線補**，GPX 只含合法路段 |
| 部件與單行道方向衝突、或穿過街廓（trace 被截斷） | 不是錯誤：分治吸附（§4.3）在衝突處改用合法繞路（`detour`），地圖上醒目標示；只有連繞路都找不到時才記 `warnings` |
| 超出台灣範圍 | 擋下並說明 |
| 搜尋結果都不符合公里數 | 仍回傳最接近的幾個，標出實際公里數（`length_ok=false`），並以 `notice` 建議放寬範圍或半徑 |
| 搜尋的起點不在圈內 | 該候選淘汰，不回傳；少於 5 個時 `notice` 說明 |
| 目前的路網沒有可騎道路索引 | `/search` 回 503 `no_search_data`，`/status` 的 `search_ready=false`，網站停用搜尋並說明 |
| 引擎無法服務或正在換資料 | 網站顯示橫幅提示，停用吸附 |
| 輸入不合法 | 引擎用 pydantic、網站用 zod 驗證；點數、圖片大小、半徑都設上限 |
| 逾時 | 吸附 5 秒（含等 `Router` 鎖的時間）、搜尋 60 秒硬上限（不含排隊；排隊最多 120 秒，超過回 `busy`）；搜尋到期時已有結果就回傳已有的 |
| 使用者離開頁面 | SSE 連線中斷即取消搜尋 |
| 濫用 | Cloudflare rate limiting（P3），加上引擎搜尋的併發上限 1（跨 worker）、其餘排隊並顯示排隊位置 |

## 6. 資料與測試

### 6.1 路網資料更新

1. 每週日 04:00（台北時間）下載 Geofabrik `taiwan-latest.osm.pbf`，並驗證 md5。
2. **快速道路標記（P0 發現：台灣快速道路多標成 `highway=trunk`，Valhalla 對 bicycle 視為可騎）**：建圖前用 pyosmium 產生修補過的 pbf，把符合下列任一條件的 `highway=trunk`／`trunk_link` 加上 `bicycle=no`：
   - `motorroad=yes`；
   - `name` 含「快速道路」「快速公路」「快速路」或「高架」；
   - `ref` 是快速公路編號：台61、台61甲、台62、台62甲、台64、台65、台66、台68、台72、台74、台76、台78、台82、台84、台86、台88（含 `;` 分隔的多值）。OSM 實際多寫成不帶「台」的 `61`、`62甲`，兩種寫法（含「臺」）都算。
   - 另有 `engine/src/engine/data/bicycle_overrides.toml`：`deny`／`allow` 兩個 way id 清單，最後套用，供人工修正誤判。
   - 修補過的 way id 清單存在 build 目錄（`bicycle_denied_ways.json`，每筆帶 `highway`），供驗證查詢與 bench 的安全斷言使用；樓梯（下一條）也在這份清單裡。
   - **樓梯**（使用者決定「禁走樓梯」）：所有 `highway=steps` 一律加上 `bicycle=no`，沒有例外（有牽引道的樓梯、`allow` 清單也不例外）。移除會重新放行的 tag，做法同快速道路（`bicycle:*`、`cycleway*`、`oneway:bicycle`、`vehicle:forward／backward`，另加實測會蓋過 `bicycle=no` 的 `service=driveway`）。
   - **人行道**（使用者決定「盡量避免」）：Valhalla 3.9.1 對沒有自行車 tag 的 `footway`／`pedestrian`（含 `footway=sidewalk`）本來就禁止自行車，不處理；已經放行的（`bicycle=yes`／`designated` 等，或 `cycleway` 放行）標成 `bicycle=dismount`，仍可通行，但預設成本以牽車速度計價。`dismount` 只影響預設成本的 `route`；shortest 與 `trace`（map_snap）都不看，所以吸附實際上避不開人行道，政策見 §8 第 19 列。
3. 在 `/data/builds/<日期>/` 建出 Valhalla tiles 和可騎道路索引。建圖在子程序中以低優先權執行，同一時間只允許一個建圖。
   - **可騎道路索引**（P2，`engine/src/engine/data/rideable.py`）：從同一份修補過的 pbf（快速道路、樓梯已是 `bicycle=no`）萃取自行車可騎的路段（`is_rideable` 近似 Valhalla 3.9.1 的通行規則：一般道路、自行車道、`path`／`track`、放行自行車的人行道；排除 motorway、樓梯、`bicycle` 不放行或 `access`／`vehicle` 為 no／private 的路）。路段切到 ≤ 200 m，依中點落在 0.01° 的格子排序，寫成 `rideable_segments.npy`（float32 (N, 4)）、`rideable_offsets.npy`（每格的起點）、`rideable.json`（格網參數，最後寫）。查詢以 mmap 讀，各 worker 共用記憶體分頁。只用於粗篩的排名；路線一律來自 Valhalla。
4. 執行固定的驗證查詢：
   - 洲美街單行段不可逆向（P0 已有）；
   - 快速道路不可騎：取被標記的 way 中名稱為「洲美快速道路」者的首尾節點，bicycle `route` 必須存在、而且不得使用任何被標記的 way；
   - 樓梯不可騎：被標記的樓梯數必須大於 0；依 way id 等距抽 20 條，樓梯兩端各往外延伸 15 m 當起訖（`route` 的起訖不會吸附到樓梯上，從樓梯端點出發即使沒修補也不會走樓梯），兩個方向的 bicycle `route` 都不得使用任何被標記的 way，而且至少一半的路線要真的配得出經過的路段（route 找得到、路段配得出來）：配不出路段的路線不能證明避開了樓梯，不到一半就視為檢查失敗；
   - 標記數量合理：被標記的快速道路 way 數必須大於 0（資料或規則壞掉時明確失敗）。
   - 可騎道路索引讀得到、格式一致、而且路段數大於 0（只跑一次，與 route 設定無關）。
   - 以上查詢對預設的 bicycle route 與吸附實際使用的 shortest 設定（§4.3）各跑一次；shortest 的失敗訊息前綴「（shortest）」。
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
- 真實路網太大，不進 CI：基準測試在 m1 的引擎容器內執行（`python -m engine.bench`），結果與 repo 內的 `engine/src/engine/bench/baseline.json` 比較，分數退步超過門檻（預設 3 分）即以非零狀態結束。CI 只跑合成地圖上的同一套流程，確保基準測試程式本身沒壞。
- 每個案例都檢查安全不變式：相鄰路段 ≤ 1 m、不經過被禁行的 way、每個接點的轉彎 Valhalla 會直接騎。沒過（`UnsafeRoute`／`SafetyUndetermined`）一律算退步、以非零狀態結束，不論 baseline 怎麼寫；`--update-baseline --force` 也不會寫入。
- 評分改版（§4.5 v2）：每個案例同時記錄 v2 分數與同一條路線的 v1 分數（`legacy_*`）。baseline 條目沒有 `score_version`（v1 時代寫的）時，退步比較用 v1 分數，所以路線本身的退步與評分改版分得開；`--update-baseline` 之後條目帶 `score_version: 2`，改比 v2。
- **搜尋 bench**（P2）：`python -m engine.bench.search` 在 current build 上跑 12 個案例（4 個地點 × 網站預設文字「I ♥ TAIPEI」／heart（旋轉不限）／hi，R = 5 km），每個案例照正式流程（每次重建 `SEARCH_ROUTERS` 個 Router）。不合格：超過 30 秒、少於 5 個候選、任何候選沒過安全斷言（與吸附 bench 同一套）、接點轉彎不能直接騎、走樓梯。另外量粗篩成本排名與精算 overall 的 Spearman 相關（調整 K 的依據）。`--json` 輸出逐案例結果。
- 另外記錄每個案例走在 footway、steps、parking_aisle 上的公尺數。這是量測值，不是門檻：數字是 Valhalla edge 全長的加總，起訖的路段與切點會略為高估，edge_walk 遇到兩條 way 共用同一對端點時也可能配錯 way。steps 必須是 0（樓梯在禁行清單裡，經過就違反安全斷言）；footway 的政策見 §8 第 19 列，目前不設上限；parking_aisle 只量測。

## 7. 分期

| 階段 | 內容 | 完成條件 |
|---|---|---|
| P0 驗證 | pyvalhalla 在 m1（linux arm64 容器）的可用性；台灣 tiles 建置；`bicycle` 的 `trace_route` 是否遵守單行道；hl `--internal` | ✅ 2026-10-10 完成（`docs/p0-findings.md`） |
| P1 手動模式 | 引擎：分治吸附、串接器、評分、`/match`、多 worker 與熱換、快速道路標記與驗證、基準測試集；網站：文字/圖片、移動/旋轉/縮放、即時吸附、GPX。兩條線依 §4.7 契約平行開發 | 可以在 m1 上完整做出一條作品並匯出 GPX（網站先維持 hl 預設鎖定） |
| P2 自動搜尋 | §4.6 全部內容；評分 v2（§4.5） | R=5 km 時 30 秒內回傳前 5 名（2026-10-11 雲端完成實作與合成地圖測試；真實路網的量測是本機待辦，見 `docs/handoff.md`） |
| P3 上線收尾 | 每週自動更新資料、rate limiting、公開部署 | 公開前經使用者確認 |

### P0 的退路

如果 pyvalhalla 沒有 linux arm64 wheel，或者沒有附帶建 tiles 的工具：改用官方 Valhalla image，作為第三個內網容器，引擎以 HTTP 呼叫它。其他設計不變。

## 8. 設計修訂摘要

### P0 後（2026-10-10）

使用者去睡前授權 Claude 依最佳實踐自行決定；以下每項都可以被推翻。

| # | 修訂 | 理由（P0 發現） | 被否決的替代方案 |
|---|---|---|---|
| 1 | 部件吸附改為分治：整段 trace → 失敗就對半切 → 最小段改用合法 `route` 繞路（§4.3） | `map_snap` 遇到逆向段或半徑外的點會退化／截斷，且截斷點之後的合法段一起消失 | 只在部件兩端點間 `route`：會丟掉形狀（L 形被切角）。解析 `trace_attributes` 的 `matched_points` 找截斷點：文件不完整、已知 edge_index 異常值，P1 先不依賴 |
| 2 | 快速道路在建圖前標成 `bicycle=no`（名稱、`ref`、`motorroad` 規則＋人工覆寫清單），並加入驗證查詢（§6.1） | 單純 `highway=trunk` 對 bicycle 可騎；台灣快速道路多標 trunk，洲美快速道路尚未在真實資料上驗證 | 全面禁止 trunk：台 1 線等一般省道也是 trunk，會誤殺大量可騎道路。只靠 OSM 原有標籤：漏標風險無法控制 |
| 3 | 多行程平行：每個 uvicorn worker 一個 `Router`，依 `/data/current` 自動換新（§3） | Actor 不是 thread-safe，兩個 thread 共用會 segfault | 單一行程＋鎖：即時吸附時新舊請求互相排隊。每個請求建新 Actor：載入成本高 |
| 4 | 圖片描邊改用 `d3-contour`（§4.1） | potrace 是 GPL-2.0，會讓前端程式碼受 GPL 約束 | potrace WASM、OpenCV.js（數 MB，殺雞用牛刀） |
| 5 | 串接器 v1 只用吸附後的方向，連接線成本用直線距離估計（§4.4） | 分治吸附已處理單行道；YAGNI，等基準測試證明需要再加 | 正反方向都算、`alternates`：吸附呼叫數加倍 |
| 6 | 基準測試在 m1 上對真實路網跑，與 repo 內 baseline 比較；CI 只跑合成地圖（§6.3） | 台灣 tiles 336 MiB，不適合放進 CI | 在 CI 下載並建台灣路網：每次 3 分鐘以上、依賴 Geofabrik 頻寬 |

### P1.5 吸附品質（2026-10-11）

由使用者決定；執行中遇到的問題，在使用者授權 Claude 自行決策的期間以裁決 A1、A3、A4、A5、A6、A7 處理（下表標明），都可以被推翻。理由的數字：「原型」是品質調查在 `b436026` 上的實測（`docs/p1-quality-investigation.md`），「檢查點 A」是正式程式在 m1 上的實測（`docs/p1-5-quality-findings.md`；T2–T7 是依序累加各項修訂的量測階段，T3 錨點、T4 封閉筆畫縫合、T5 shortest、T6 半徑、T7 串接，T7+fix 是 T7 加上錨點改取 `stroke` 長度；分數寫成 overall/coverage）。檢查點 A 在反向吸附（V）與接點轉彎檢查之前量，這兩項沒有各自的前後對照，只列原型數字；第 19 列量測表的 base 已經含這兩項，是它們的累積結果。最終部署後的前後對照見 findings。

| # | 修訂 | 理由（量測） | 被否決的替代方案 |
|---|---|---|---|
| 7 | 串接器從 `stroke` 長度最長的部件（錨點）往兩端組裝，接不上原定那端的先擱著，其他接完再補（§4.4；補接的修正見第 20 列，裁決 A7） | 舊版固定從第 0 個部件往後接，它的尾巴 route 不出去時其餘部件全被丟掉：`star@taipei_daan@r30` coverage 66 → 0（P1 最終修正的 bench 量到）。檢查點 A：T2 → T3 只有 4 個案例的輸出不同，4 個在 T2 都有丟部件；`star@taipei_daan@r30` 34/0 → 39/66、`hi@taipei_riverside` 16/31 → 29/58。錨點先用路線總長挑時被繞路灌大，`letter_l@taipei_riverside` 30/0 → 0/0（A3）；改取 `stroke` 長度（繞路與連接線不算）後回到 30/0 | 每個部件都當一次起點、取直線成本最低：直線成本看不到出不去的端點，擋不住丟部件，還會改動每個多部件案例的順序。錨點取路線總長最長：被繞路灌大（A3） |
| 8 | 封閉筆畫斷成多條 chain 時依筆畫順序接回，首尾相鄰才是圈（§4.4） | 斷鏈後的封閉筆畫從不被當成圈，也不知道最後一條接得回第一條。檢查點 A：T4 相對 T3 bench 無變化（25 案例 overall、coverage、長度完全相同）：T4 時 15 個 heart／star／square 案例只有 `star@taipei_daan@r30` 收到 2 條 chain，而且沒接上，其餘都是 1 條，所以縫合的路徑只有單元測試覆蓋 | 收尾也補 route：只有一個部件時那段路沒有用，只會讓路線變長 |
| 9 | 繞路、補橋、connector 與轉彎檢查的 `route` 用 bicycle `shortest`（§4.3） | 預設成本偏好自行車道、繞遠：原型 `match_rect` 繞路＋補橋 3021 → 2653 m；`taiwan_checks` 在 shortest 下通過。檢查點 A：`validate_build`（預設與 shortest 各跑一次）回 `[]`；T4 → T5 overall 中位數 46 → 54、平均 47.3 → 47.4，預設文字 route 呼叫 61 → 41、median 462 → 297 ms。單獨加 S 有 3 個案例 overall 下降（`heart@taipei_riverside` 20 → 12、`square@taipei_riverside` 20 → 16、`hi@taipei_daan@r30` 62 → 58），加上 R035 與 C 後回升到 24、24、60 | trace 也用 shortest、`use_roads`、`bicycle_type`：原型在雜訊內或變差 |
| 10 | 預設搜尋半徑 0.02 → 0.035 × 對角線（§4.3） | 半徑太小，meili 斷段：原型 overall 中位數 46 → 64、平均 48.4 → 52.5；ms 最大約 ×2.2。檢查點 A：T5 → T6 overall 中位數 54 → 61、平均 47.4 → 52.2；單次 ms 中位數／最大 228／505 → 294／1117 | 0.05：riverside 與 r30 案例退步 3 個、ms 最大 1.6–1.8 s。自適應半徑：每案例多約 900 ms。改 `max_route_distance_factor`：匹配長度變畫線的 1.82 倍 |
| 11 | 分治改成串接（§4.3） | 兩半各自吸附，切點兩側落在不同的路，補橋繞一大圈：原型 `square@taipei_daan` 補橋 4557 m。檢查點 A（累積 T2–T7，含錨點修正）：overall 中位數 46 → 60、平均 48.1 → 53.6，length/target 中位數 1.90 → 1.71，預設文字 median 290–298 ms（4 次）；T6 → T7 單獨：overall 中位數 61 → 60、平均 52.2 → 52.4 | 縫合 trace 的 alternates、接受截斷前綴：partial 案例增加、`match_rect` 掉到 0–2 分。先在轉角切開：`match_rect` 掉到 0 |
| 12 | 開放筆畫可以反向走（重新吸附）（§4.4） | 開放筆畫只能照畫的方向走：品質調查 base 的 `hi@taipei_daan` connector 3 條共 5231 m，佔多出長度的 49%；C＋R035＋S 再加 V 平均 54.0 → 54.4。檢查點 A 之前沒有 V，正式程式的實測在部署後 | 把現成路線倒過來：會逆騎單行道 |
| 13 | 反向重新吸附只對整段 trace 一次，失敗就照畫的方向走（§4.4；裁決 A5） | 計畫原案是用 `match_stroke` 重新吸附（可對半切、補繞路），反向結果沒有任何檢查。審查在合成的單行道網格上量到：反向 trace 失敗 → 對半切 → 一條全是 `detour` 的反向本體換掉乾淨的正向 `stroke`，被反向的部件是錨點時 coverage/overall 100/50 → 0/0，最壞 trace 2 → 17 次。改成單次 trace 後保留正向（`stroke` 約 404 m），trace 2 → 3 次；每個反向部件最多多 1 次 trace。限制：反向結果與正向用同一個門檻接受，近平手時仍多花 1 次 trace | 反向也走完整分治（含繞路）：上述退步 |
| 14 | 接點轉角 > 30° 時用 Valhalla 確認能直接騎，不能騎視同沒接上；探針跨過路段邊界，新接點 8 m 內的既有接點一併重量（§4.3） | 接點轉彎沒人檢查：品質調查 base 的 855 個接點中 57 個轉角 > 30°、2 個 Valhalla 不會直接騎（`match_rect`）；立體交叉在平面上是同一點。只看新接點兩側時，儲存庫的 10,000 組固定種子 fuzz 有 201 組輸出 Valhalla 不會直接騎的接點，加上跨段探針、鄰近接點重量與折回規則後 0 組。本機網格一次檢查約 0.85 ms；台灣路網上的延遲與 bench 的不能騎接點數在部署後量 | 只在 bench 量、不在引擎擋：會輸出不能騎的轉彎。不先看轉角、每個接點都 route：route 呼叫多一倍，轉角小的接點量不出差別。輸出前對整條路線再驗一遍：多 N 次 route，違反延遲預算，改由 bench 驗每個接點 |
| 15 | bench：安全斷言沒過一律失敗（`--force` 也不寫）、接點轉彎計數、量 footway／steps／parking_aisle（§6.3） | P1 重新審查：寫進 baseline 的 `UnsafeRoute` 之後永遠抓不到。品質調查 base 的 `square@taipei_riverside` 走了 footway 1255 m、steps 16 m、parking_aisle 573 m；檢查點 A 的 25 案例合計（量測值，不是門檻）：footway／steps／parking_aisle 34447／53／2807 m（T2）→ 30423／316／1428 m（T7+fix）。steps 必須是 0，由第 18 列的樓梯禁行保證；footway 的政策是第 19 列 | 安全斷言併進 baseline 比較（舊行為）：baseline 裡的失敗不會再被抓到 |
| 16 | 拖曳中最多一筆吸附請求在途，不取消；回來後才補送最新位置，放開或微調時才取消（§4.2；裁決 A4） | 檢查點 A（T7+fix）：bench 單次吸附 ms 中位數 282、最大 1118，預設文字 median 290–298 ms，離舊做法每 300 ms 取消前一筆的節奏只差 2–10 ms。單次吸附超過 300 ms 的圖形，每一筆都會在回來前被下一筆取消，拖曳整段看不到更新（由節奏推論，沒有用真實瀏覽器實測；單元測試用假時鐘，另有 1500 組隨機序列的不變式測試） | 維持每 300 ms 取消舊請求（原 §4.2） |
| 17 | 檢查點 A 的驗收規則（裁決 A1）：重現原型已知取捨的 coverage 下降直接接受；`letter_l@taipei_daan` overall 44 → 34 是明確例外（裁決 A3） | A1：安全斷言全過、單案例 overall 下降 ≤ 3 且 coverage 下降 ≤ 8、整體 overall 中位數與平均都高於 baseline、`star@taipei_daan@r30` coverage ≥ 50、延遲在預算內（預設文字 median ≤ 300 ms、最慢案例 ≤ 2500 ms）。T7+fix 的下降清單 13 條（11 個案例）全部被接受，一條依 A3 例外、其餘符合 A1：overall 中位數 46 → 60、平均 48.1 → 53.6，`star@taipei_daan@r30` coverage 69，預設文字 median 290–298 ms，最慢 1118 ms，安全斷言不安全 0、無法判斷 0。例外：`letter_l@taipei_daan` baseline 的 coverage 是 0（只畫了片段），現在畫出完整的 L（coverage 0 → 67），代價是 overall 44 → 34（cleanliness 87 → 0）和一條 3027 m 的 connector：part0 終點 → part1 起點 14.7 m 的缺口正向 route 無解、反向有 603 m（推測是單行道，沒有查道路屬性）；這個案例 overall 最多可降 10 | 每次 coverage 下降都停下來等使用者決定（計畫原案；使用者不在時改由規則裁決）。退回 T3 之前只接原定那端的做法：這個案例只畫片段（coverage 0）。把斷成多個部件的開放筆畫整條反向：才是根本做法，延到 P2 |
| 18 | 樓梯一律標成 `bicycle=no`，沒有例外；`validate_build` 加樓梯檢查（§6.1） | 使用者決定（2026-10-11）：「禁走樓梯」。Valhalla 3.9.1 預設讓自行車走樓梯：品質調查 base 的 `square@taipei_riverside` 走了 steps 16 m，bench 25 案例合計 307 m。台灣資料共 16,359 條樓梯被標記。m1 上量測（scratch build，不啟用）：25 案例 overall 與禁行前完全相同（coverage 只有 2 案差 1），steps 307 → 0 m（改走別條路），安全斷言不安全 0、無法判斷 0；`validate_build` 回 `[]`（0.7 s）。檢查不是空洞的：同一份禁行清單拿到未修補的正式 build 上，預設成本 0 項、shortest 抓到 4 項（20 條抽樣裡 3 條的路線經過樓梯） | 只靠成本：預設成本本來就重罰樓梯（0.2 km 樓梯對 2.4 km 繞路仍繞路），但繞路用的 shortest 只看長度，會直接走樓梯。留例外（有牽引道的樓梯、`allow` 清單）：使用者說禁走，不留例外 |
| 19 | 人行道盡量避免，但不禁止：資料只把 Valhalla 本來就放行的 `footway`／`pedestrian`（4,994 條）標成 `bicycle=dismount`；吸附維持 (a)，繞路、橋接、connector 仍用 shortest（§6.1；裁決 A6）。使用者仍可改選禁止（S） | 使用者決定（2026-10-11）：「人行道盡量避免」。實測（Valhalla 3.9.1，m1 同一份 pbf，量測表見下）：① 沒有自行車 tag 的 `footway`／`pedestrian`（120,429 條）本來就不能騎；bench 走到的 30.6 km 人行道全是明確放行自行車的共用人行道（`footway=sidewalk`＋`bicycle=yes` 21.1 km、`designated` 5.1 km，其餘約 4.3 km），都合法。② `dismount` 只有預設成本會看，shortest 與 trace（map_snap）都不看；人行道公尺數的 76% 來自 trace（stroke 23.4 km、detour 4.1 km、connector 3.1 km），繞路的成本設定最多只能影響約 7 km。③ (b) 繞路改回預設成本只減 12.4%，卻讓 A1 不通過；(b2) 同樣不通過。④ (S) 禁止可減 86%，但 overall 中位數 62 → 58、平均 53.6 → 51.0，`square@taipei_daan@r30` 72 → 55，預設文字約慢 10%，而且是禁止，不是「盡量避免」。選 (a)：不合法的人行道本來就不會走，剩下的是明確允許自行車的人行道；要再避開只剩禁止，要付品質的代價 | (b) 繞路、橋接、connector 改回預設成本，(b2) 再加 `use_roads=1.0`：A1 不通過。(S) 禁止自行車本來能走的人行道：品質與延遲的代價，而且尚未評估連通性（例如橋上只有人行道可騎、車道又是 `bicycle=no`）；使用者想禁止時再做（`docs/backlog.md`）。照字面規則（沒有明確允許的都標 `dismount`）：會把約 12 萬條本來不能騎的人行道打開，而真正被走的人行道都有明確的允許 tag，一條也不會被避開 |
| 20 | 組裝時原定那一端接不上的部件先擱著，其他部件照規劃接完再補：原定那端後來換了部件才在原端重試，再試另一端，都不行才丟（§4.4；裁決 A7，2026-10-11） | 預設文字「I ♥ TAIPEI」從 58/72/43 掉到 48/72/23（overall/coverage/cleanliness），實測追到組裝的缺陷（`docs/p1-5-quality-findings.md`「預設文字與決策 A7」）：筆畫 12 是 106 m 的吸附結果，落在 `use=ramp` 的 way，route 出得去、進不來（往它的 route 終點停在 6.6–7.5 m 外，裁切重試也失敗）。舊的組裝在原定那端接不上時立刻改接另一端，把它搬到路線開頭（1,686 m connector），佔掉規劃中排第一的「I」的位置；「I」接開頭也得進它，只好橫越整張圖接到尾巴。反向（V）讓路線結尾移位後，那條 connector 離畫線平均 56 m → 108 m。修正後（`b8dc6ec`）「I」照規劃接在心形前面，只有筆畫 12 在最前面多一條 2,302 m 的 connector：48/72/23 → 56/72/39，16,884 → 16,049 m，route 呼叫 32 → 28，median 約 300 → 260 ms；bench 25 案例輸出不變（沒有「出得去、進不來」的部件），安全斷言不安全 0、無法判斷 0 | 接不上的小部件直接丟：會少畫（預設文字的筆畫 12 就是這種部件）。ramp 這類 trace 吸得到、route 進不去的路段本身另列 P2（`docs/backlog.md`），這一列只修組裝的順序 |

#### 第 19 列的量測：人行道的四種做法

m1、同一份 pbf（build `20261010T183449Z`）上建 scratch build（不啟用，量完刪除）。base 是樓梯與人行道處理之前的程式，跑在正式 build 上。(a)：資料修補＋繞路、橋接、connector 維持 shortest；(b)：資料修補＋繞路、橋接、connector 改回預設 bicycle 成本（接點轉彎的探針仍用 shortest）；(b2)：(b) 再加 `use_roads=1.0`；(S)：`footway=sidewalk` 且自行車本來能走的改標 `bicycle=no`，其餘同 (a)。

| 變體 | footway m | steps m | overall 中位數／平均 | A1 | 對 committed baseline 的最大降幅 | 預設文字 median（4 次） | bench 最慢 |
|---|---|---|---|---|---|---|---|
| base | 30,580 | 307 | 62／53.6 | 通過 | −10（`letter_l@taipei_daan`，A3 例外） | 291–299 ms | 1134 ms |
| (a) | 30,766 | 0 | 62／53.6 | 通過 | 同 base | 303–309 ms | 1114 ms |
| (b) | 26,801（−12.4%） | 0 | 59／52.4 | 不通過 | −5（`square@taipei_daan@r30`）、`hi@taipei_daan@r30` −4 | 310–315 ms | 1128 ms |
| (b2) | 26,301（−14.0%） | 0 | 59／51.8 | 不通過 | −10（`hi@taipei_daan`）、`hi@taipei_daan@r30` −4 | 306–312 ms | 1123 ms |
| (S) | 4,167（−86.4%） | 0 | 58／51.0 | 不通過 | −17（`square@taipei_daan@r30`），另有 2 案 coverage −9 | 319–331 ms | 1045 ms |

- committed baseline 的 overall 中位數 46、平均 48.1；5 個變體的安全斷言都是 25/25 成功、不安全 0、無法判斷 0。
- (a) 對 base：25 案例 overall 完全相同，人行道沒有下降（+186 m：樓梯禁行後改道）。
- (a) 的預設文字 median 比 base 多約 4%，程式的吸附部分相同，差異來自路網（樓梯禁行改道）或 tile 快取狀態（推測）；已高於 300 ms 的預算，部署後以最終程式重量。
- (S) 只量了 bench，沒有評估連通性。

### P2 自動搜尋（2026-10-11）

使用者不在時授權 Claude 依最佳實踐決定（都可以被推翻）。雲端 session 連不到 m1，以下沒有真實路網的量測：理由的數字來自合成地圖或推理，真實路網的驗證列在 `docs/handoff.md` 的本機待辦。

| # | 修訂 | 理由 | 被否決的替代方案 |
|---|---|---|---|
| 21 | 評分 v2（§4.5）：乾淨度只看看得到的線（路線每 2.5 m 取樣、5 m 格子去重，每格取座標字典序最小的點）；0 分距離 `Z = max(0.05 × 對角線, 60 m)`；v1 保留給 bench 比較 | 評分是搜尋的目標函數，要量騎完在 Strava 上看到的樣子。v1 把來回騎的同一條街重複計入、而且距離接近 0，稀釋了真正偏離的部分：移除折返反而讓乾淨度下降（品質調查 §4.4：G／L 讓 overall 平均 −0.6／−1.0）。合成測試：多來回一趟加固定支線，v2 乾淨度不變、v1 差 37 分。小圖形：吸附到真實道路的無法消除的偏移約 8 m 平均／23 m P90（大安區），1 km 圖形的 v1 0 分距離只有約 48 m，完美路線也拿不到多少分；60 m 讓這個偏移最多吃掉約三分之一的分數，2 km 以上的圖形（0.05 × 對角線 > 60 m）不受影響。每格的代表點取字典序最小而不是第一個經過的點：後者隨騎的方向改變，會讓方向影響分數（差 1 分） | 只修重複（不加下限）：小圖形問題仍在。改成門檻式完成度／正確度（buffer 內的比例）：可解讀但不連續，搜尋的排名需要平滑的分數，而且與 P1.5 的數字完全不可比。加入長度效率項：使用者用公里數範圍直接控制長度，評分只管畫得像不像 |
| 22 | 可騎道路的空間資料用 numpy 分格索引（`rideable_*.npy`，0.01° 格子、路段 ≤ 200 m，mmap 讀），由建圖流程從修補後的 pbf 產生（§6.1）；`bicycle=private` 算不可騎 | 唯一的使用者是引擎的粗篩：只要「範圍內的路段」，不需要 GIS 互通。numpy 已是依賴，mmap 讓多個 worker 共用記憶體分頁；查詢是幾個連續切片（合成資料 750 萬段、40 km 範圍約 93 ms）。從修補後的 pbf 萃取，禁行的快速道路與樓梯與 tiles 同一個來源。private 只給起訖用，圖形不該擺在私人道路上（只影響排名，不影響安全） | FlatGeobuf（原 spec）：要 GDAL／pyogrio（映像大、m1 的 arm64 建置無法在雲端驗證），換來用不到的 GIS 互通。建圖時就把全台灣畫成距離場：0.5–2 GB，而且綁死格子大小。從 Valhalla tiles 匯出路段：pyvalhalla 沒有逐 edge 的通行權輸出 |
| 23 | 粗篩成本的截斷距離是吸附搜尋半徑 × 3，大小仍是 6 級 | 只截在吸附半徑時，大小差一級（約 26%）就讓每個取樣點都超出截斷、成本一片平坦，微調找不到方向。合成測試（1 km 正方形環混在 400 條隨機道路中）：最好的擺放偏 10% 大小、成本只比沒有環時好 0.1；改成 × 3 後完全找回（位置 0 m、大小 999 m、角度 8.0°，成本 0.021；不微調時偏 50 m／10%） | 大小加到 17 級（每級 7%）：粗篩時間約 3 倍。只靠精算修正：精算每個候選要秒級，拿來找位置太貴 |
| 24 | `/search` 用 SSE（FastAPI 0.143 內建的 `EventSourceResponse`），事件 schema 進 OpenAPI components；串流前的錯誤是一般 JSON | SSE 讓狀態留在一條連線上：引擎有多個 uvicorn worker，輪詢式的工作 id 會落到別的 worker 找不到；Cloudflare 與 nginx 不緩衝 `text/event-stream`；斷線就是取消。FastAPI 內建版本自帶 15 秒 keepalive，事件型別直接產生 web 的 TypeScript 型別 | 工作 id＋輪詢：多 worker 要共用狀態（檔案或 Redis）。NDJSON：Cloudflare 對非 SSE 的串流可能緩衝。自己寫 StreamingResponse：要手寫 OpenAPI |
| 25 | 自動搜尋的起點：串接器接受起點提示（§4.4），`/match` 也可以帶 `start_circle`；精算淘汰起點在圈外的候選 | 「起點必須在圈內」是硬條件，但原本的串接器固定從第 0 個部件規劃。只在規劃時換掉第一個部件，組裝規則（錨點、擱置後補接）不變，所以接點與安全規則完全沿用。網站套用候選後繼續手動調整時，即時吸附也帶同一個圓，起點才不會跑出圈外；不帶時行為與 P1.5 完全相同（所有既有測試不變） | 吸附完再挑起點：路線不能倒過來騎（單行道）。把圓外的部分改成騎過去的路：違反「公里數只算圖本身」 |
| 26 | 搜尋用自己的 Router（每次搜尋重建 `SEARCH_ROUTERS` 個、預設 2，結束就釋放），全引擎同時 1 個搜尋（檔案鎖跨 worker） | 搜尋若用 worker 的 `/match` Router，會佔住它 10–30 秒：落在那個 worker 的即時吸附全部逾時。各自的 Actor 可以平行（P0：不能共用的是同一個 Actor）。用完就釋放：每個 Actor 的 tile 快取上限 256 MiB，常駐會讓兩個 worker 多出最多 1 GiB。spec §5 的併發上限 1 原本沒說跨 worker；用 flock，行程崩潰時鎖自動釋放 | 共用 `/match` 的 Router：上述阻塞。常駐的搜尋 Router：記憶體。行程內的 Semaphore：兩個 worker 會同時跑兩個搜尋 |
| 27 | 精算的停止條件：K = 10 而且已有 5 個合格；或 25 秒且已有 5 個起點在圈內；公里數不符的依比例修正一次 | 完成條件是 30 秒內前 5 名：留 5 秒給串流與網路。單一候選的吸附上限 10 秒（大圖形比 `/match` 的 5 秒慢）。修正後的版本排最前面，同一個粗篩候選只留比較好的結果，前 5 名不會被同一個擺放的兩個大小佔掉 | 固定跑完 K 個：大圖形可能超過 30 秒。每個不合格的都修正到合格為止：時間無上限 |
| 28 | 網站的搜尋範圍要明確建立才存在，存在時即時吸附帶 `start_circle`；不存進 `localStorage`；套用候選走一般的 Transform | 範圍會改變吸附結果（起點），要有看得到的開關，不能在使用者不知情時生效；重新載入後也不該暗中帶著約束。套用候選只是設定擺放，之後由即時 `/match` 重新吸附：路線一律來自 `/match`，與手動模式同一條路徑（引擎與網站的擺放換算以共用的固定案例釘住，§8 第 25 列） | 打開搜尋面板就自動建立範圍：使用者不一定要搜尋。直接沿用搜尋回傳的路線、不重新吸附：多一條路線來源，GPX 下載的條件也要另外處理 |
