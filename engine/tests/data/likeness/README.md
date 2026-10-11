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
- `label.tmpl.html`：標註頁樣板，`__PAIRS__` 換成 `pairs.json` 的內容後，用 Artifact 發佈並宣告 `db` capability；選擇存在 `votes` 集合（doc id `p01`…，欄位 `pair`、`choice`、`n`、`at`）。組數寫死 36；下一輪用套件裡的通用版（見下）。

## 下一輪樣本

`python -m engine.bench.likeness_samples`（`engine/src/engine/bench/likeness_samples.py`）產生下一輪的樣本、配對與標註頁。使用者決定只產生差異明顯的配對（v3 rank 差距 ≥ 門檻，或現行 vs 規劃器），本機下一輪目標至少 40 組有表態。

### 1. 在引擎容器內產生樣本（current build）

```sh
uv run --no-sync python -m engine.bench.likeness_samples samples --out /tmp/likeness-next \
  [--data-dir /data] [--case heart …] [--top 3] [--min-gap 3.0] [--max-pairs 60] [--seed 7] [--variants Q2,P2]
```

- 每個搜尋 bench 案例跑一次正式搜尋（`run_search`，同 `bench/search.py`；Router 數讀 `SEARCH_ROUTERS`，預設 2），取前 `--top` 名擺放；每個擺放、每個變體各吸附一次（`place` → `match_shape`，帶與搜尋相同的起點提示）。
- 變體（`VARIANTS`，`--variants` 選要跑哪些，未知名稱是錯誤）：

  | variant | 怎麼產生 |
  |---|---|
  | Q2 | 現行預設：Router 預設的 `turn_penalty_factor`（`FAST_TURN_PENALTY_FACTOR`）、`match_shape` 剪小刺（`prune=True`） |
  | P2 | 改前：Router 用 `turn_penalty_factor=None`（不帶）、`match_shape(..., prune=False)` |

- 配對候選（每個案例）：同擺放的 Q2 vs P2（每個名次）、Q2 第 1 名 vs Q2 第 3 名（`Q20` vs `Q22`，不同擺放），只留 |`rank_score` 差| ≥ `--min-gap` 的；`ALWAYS_PAIRS` 裡的變體配對（PR B 的 F vs Q2）不看差距、一律保留。安全斷言沒過的路線（`unsafe`、`bad_turns` > 0、`steps_m` > 0）照樣記在 `routes.json.gz`，但不進配對（stderr 會警告）。
- 挑選：每個案例的候選依差距由大到小（一律保留的排最前），各案例輪流（round-robin）各拿一組，挑到 `--max-pairs`；左右隨機、最後整體打亂（`random.Random(--seed)`，結果確定），`n` 從 1 編號。
- 輸出（`--out` 目錄，不存在就建立）：
  - `routes.json.gz`：格式同上面的 `routes.json.gz`，多出來的欄位見下；
  - `pairs.json`：`[{id, ref, l, r, n}]`，`id` = `<案例去掉 @R5>|<左 variant><rank>|<右 variant><rank>`（例如 `heart@taipei_daan|P21|Q21`），`ref` 是原圖的 SVG path（110 px、不旋轉），`l`／`r` 是路線的 SVG path（240 px，各自縮放到框內、北朝上）；
  - `label.html`：標註頁（`engine/src/engine/bench/label.tmpl.html` 的 `__PAIRS__`、`__TOTAL__` 換掉）。沒有任何配對時不寫。
- stdout 是摘要：每個案例各變體各名次的 `rank_score`、候選數、達門檻數、選入的配對數與差距範圍，最後是全部配對的差距中位數與分布。

`routes.json.gz` 多出來的欄位：

- `score_v3`：`{overall, coverage, cleanliness, direction, exact}`（`score()` 的 v3；`exact` 是沒取整數的 overall，取到小數 3 位）；
- `rank_score`：`score.rank(score_v3, rotation_deg)`，配對比較用（取到小數 3 位）；
- `ms`：每條路線重新吸附的時間（Q2 也是重新吸附，不是搜尋的結果，所以都有值）；
- 案例層級的 `error`：只有搜尋本身失敗時才有（例外類別名），這時 `routes` 是空的。

`variant` 是上表的代號；`score_v2` 是同一條路線用 `score_v2` 算的分數。

### 2. 發佈標註頁

把 `label.html` 從容器拷出來，用 Artifact 發佈並宣告 `db` capability。頁面一次一組，快捷鍵 ← 左邊、↓ 差不多、→ 右邊；選擇存在該 artifact 的 `votes` 集合：doc id `p01`、`p02`…（100 組以上補零到三位），欄位 `pair`（`pairs.json` 的 `id`）、`choice`（`L`／`R`／`same`）、`n`、`at`（ISO 時間）。重新開啟會讀回已標的組。

### 3. votes 轉成 labels

用 ArtifactData 讀回該 artifact 的 `votes` 集合，存成 `votes.json`。接受兩種形狀：

- 列表：`[{"id": "p01", "data": {"pair": …, "choice": …, "n": …, "at": …}}, …]`（list／query 讀回的文件）；
- 物件：`{"p01": {"pair": …, "choice": …, "n": …, "at": …}, …}`（以 doc id 為鍵）。

```sh
uv run python -m engine.bench.likeness_samples labels --votes votes.json --pairs pairs.json --date 2026-10-20 \
  > tests/data/likeness/labels-2026-10-20.json
```

輸出是 `labels.json` 的格式（`collected_at`、`pairs: [{n, case, left, right, choice}]`，依 `n` 排序）。`pair` 對不上 `pairs.json`、`n` 和 `pairs.json` 不符、`choice` 不是 `L`／`R`／`same`、同一組標兩次都是錯誤（結束碼 1，不悄悄略過）；已標與沒標的組數印到 stderr。要算一致率：把這一輪的 `routes.json.gz` 與 labels（改名成 `labels.json`）放在同一個目錄，`uv run python -m engine.bench.likeness --data 該目錄`。
