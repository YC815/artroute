# 相似度校正資料（P2.5）

P2.5 spec（`docs/superpowers/specs/2026-10-11-p2-5-likeness-design.md`）§1、§3.1、§4.4 用的真實路網樣本與使用者標註。2026-10-11 在 m1 上產生，build `20261011T040535Z`，引擎程式是 PR #2 的 `d69e0ca`。

路線幾何來自 OpenStreetMap 資料，© OpenStreetMap contributors，以 ODbL 授權（https://www.openstreetmap.org/copyright），經 Valhalla 產生。

## routes.json.gz

gzip 的 JSON 陣列，每個元素是一個搜尋 bench 案例（`engine/src/engine/bench/search_cases.py`，R = 5 km）：

- `case`、`center`（[lon, lat]）、`radius_m`
- `shape`：正規化圖形（外框中心為原點、長邊 = 1；與網站 `normalizeStrokes` 相同），`[{points: [[x, y], …], closed}]`
- `routes`：每條路線
  - `variant`、`rank`：見下表；`rank` 是搜尋結果的名次（0 = 第 1 名）
  - `placement`：`{center, size_m, rotation_deg}`（與 `engine.search.types.Placement` 相同，旋轉順時針、0 = 北）
  - `score_v2`：`{overall, coverage, cleanliness}`
  - `route_m`、`target_m`、`ms`（V1–V4 的吸附時間；V0 是搜尋的一部分，為 null）
  - `warnings`、`unsafe`（null = 安全斷言通過）、`bad_turns`、`steps_m`：全部樣本都安全
  - `segments`：`[{kind: "stroke" | "detour" | "connector", stroke, points: [[lon, lat], …]}]`，點取到小數 6 位
  - 吸附失敗時只有 `{variant, rank, error}`（這批沒有）

| variant | 怎麼產生 | 哪些名次 |
|---|---|---|
| V0 | 正式搜尋（`run_search`）的結果，現行吸附 | 0–4 |
| V1 | 同一個擺放用 `match_shape` 重新吸附，`trace_options.gps_accuracy` = 搜尋半徑 / 3 | 0–1 |
| V2 | 同上，`gps_accuracy` = 半徑 / 2，`turn_penalty_factor` = 500 | 0–1 |
| V3 | 同上，設定檔 `meili.default.sigma_z` = 15，`turn_penalty_factor` = 500 | 0–1 |
| V4 | 同上，`sigma_z` = 30，`turn_penalty_factor` = 500 | 0–1 |

V1 的路線與 V0 完全相同（`gps_accuracy` 沒有作用）。

## labels.json

使用者在盲測標註頁（36 組，左右順序隨機、看不出版本）的判斷：

- `pairs`：`[{n, case, left, right, choice}]`；`left`／`right` 是 `<variant><rank>`（例如 `V02` = V0 第 3 名），`case` 是案例名稱去掉 `@R5`；`choice` 是 `L`、`R` 或 `same`（差不多）
- 判斷尺度：手機 Strava 動態牆縮圖。頁面上每條路線各自縮放到 240 px 方框內、北朝上，上方附原圖（不旋轉）。

## source/

產生這些檔案的腳本，照原樣保存以便重現（不是測試、不是套件的一部分）：

- `samples.py`：在引擎容器內跑，產生 V0–V2（stdout 是 JSON）。
- `sigma.py`：讀 `samples.py` 的輸出（stdin），產生 V3、V4；合併後就是 `routes.json.gz`。
- `pairs.py`：讀合併後的 JSON（檔名 `all.json`），產生 36 組配對的 SVG 路徑（`pairs.json`）。
- `label.tmpl.html`：標註頁樣板，`__PAIRS__` 換成 `pairs.json` 的內容後，用 Artifact 發佈並宣告 `db` capability；選擇存在 `votes` 集合（doc id `p01`…，欄位 `pair`、`choice`、`n`、`at`）。
