# 交接：P2 自動搜尋完成、精算候選數調整（2026-10-11）

## 目前狀態

- P0、P1、P1.5、P2 都已完成並部署在 m1（PR #1，merge commit `0478462`）。P2 的真實路網量測見 `docs/p2-search-findings.md`。
- 完成條件（R = 5 km 時 30 秒內前 5 名）達成：搜尋 bench 12 個案例都有 5 個候選，最慢 13.5 s、中位數 4.7 s。粗篩最多 1.3 s。記憶體峰值 1.345 GiB。60 個候選全部通過安全斷言、樓梯 0 m。`SEARCH_ROUTERS` 維持預設 2。
- 部署中：
  - 網站 `artroute`：https://artroute.fiir.fyi ，**私人**（Cloudflare Access），含自動搜尋面板。
  - 引擎 `artroute-engine`：只在內網（`http://artroute-engine:8080`），2 個 uvicorn worker，`/status` 的 `search_ready: true`。部署的是本分支 `d69e0ca` 的程式（16 個全部精算，2026-10-11 04:44Z）。
  - 路網 build `20261011T040535Z`：有可騎道路索引，654,328 條 way、5,777,571 個路段、93.9 MB。pbf 與 `20261010T235143Z` 相同，回滾用 `20261010T235143Z`。
- 吸附 bench 的 baseline 已換成評分 v2：路線與 P1.5 完全相同，v2 overall 中位數 61、平均 53.6；不安全 0、不能直騎的接點 0、樓梯 0 m。
- **本分支（`claude/p2-search-validation-jn70st`）已部署並量完**（`docs/p2-search-findings.md` 第 8 節）：精算改成 16 個候選全部跑完（spec §8 第 29 列）。12 個案例仍全部合格，最慢 19.9 s、中位數 8.3 s，記憶體峰值 2.145 GiB，安全斷言全過。前 5 名有 18／60 個來自粗篩第 11–16 名，平均 overall 比 K = 10 實際部署的結果高（中位數 +2.1）。判讀：建議評估 `FINE_KEEP` 24。`/match` 與 API 契約都沒變，網站沒有重新部署。

## 這個分支做了什麼

- **精算全部候選**（`engine/src/engine/search/fine.py`，§4.6、§8 第 29 列）：粗篩與精算排名的 Spearman 中位數只有 +0.04，最慢的案例只用了 30 秒預算的 45%，所以把 NMS 後的 16 個全部精算（原本 K = 10 而且有 5 個合格就停）。25 秒且已有 5 個起點在圈內仍提前停。已有 5 個之後才開始的吸附，時限縮到第 28 秒為止，避免一個慢候選把搜尋拖過 30 秒。
- **搜尋 bench 的新量測**（`engine/src/engine/bench/search.py`，§6.3）：每個候選記粗篩名次（`origin`）；每個案例算 `late`（前 5 名裡來自粗篩第 11 名以後的個數）與 `gain`（前 5 名平均 overall 比只精算前 10 名高多少，從同一次搜尋的結果篩出，是下限）；最後印一行彙總；`--json` 另外輸出每一次精算的粗篩名次與分數（`evaluations`）。
- 文件：spec §4.6、§6.3、§7（P2 改成已完成）、§8 第 29 列；本檔；`docs/backlog.md`。
- 確認本機的 `dc48f8a`（預設文字 fixture 的座標比到 1e-12）：測試仍逐項比對文字、字型、closed、點數，座標容差對正規化座標（約 1 單位大小）足夠嚴，雲端也通過。

## P2 做了什麼（細節見 spec §4.2、§4.4–§4.7、§6.1、§6.3、§8 第 21–28 列）

- **評分 v2**（§4.5）：乾淨度只看看得到的線（5 m 格子去重），0 分距離下限 60 m。`/match` 回傳的 score 改成 v2；**路線本身不變**。bench 同時記 v1 分數，對 v1 baseline 用 v1 比。
- **可騎道路索引**（§6.1）：建圖流程從修補後的 pbf 產生 `rideable_*.npy`，`validate_build` 檢查它。**需要重建路網。**
- **`POST /search`**（SSE，§4.6、§4.7）：距離場粗篩 → NMS 與微調 → 多 Router 平行精算（帶起點提示）→ 前 5 名。全引擎同時 1 個搜尋（檔案鎖），搜尋用自己的 Router，不佔 `/match` 的名額。`SEARCH_ROUTERS` 環境變數（預設 2）。
- **`/match` 只新增**：可選的 `start_circle`（不帶時行為完全相同）；`/status` 多 `search_ready`。
- **網站**：自動搜尋面板（範圍、公里數、旋轉、進度、前 5 名、套用），`/api/search` 串流代理；套用候選後的手動調整與 GPX 照舊。
- **搜尋 bench**：`python -m engine.bench.search`（12 個案例，R = 5 km）。

## 使用者已做的決定

- 自行車；不可逆向；快速道路與樓梯禁行；只做台灣；不做登入；必須能匯出 GPX。
- 人行道：決策 A6（§8 第 19 列）。
- 加快開發速度：能平行就平行，小修正直接驗證。

## 本機待辦（雲端連不到 m1）

順序重要。完整的步驟、接受條件與停止條件寫在 PR 說明的「給本機 Agent 的 prompt」。**1–5 已在 2026-10-11 完成**，數字見 `docs/p2-search-findings.md` 第 8 節；剩第 6 項。

1. 本機驗證：`cd engine && uv run pytest -q -W error`（網站沒有改，不用跑網站全套）。
2. 部署引擎：`cd engine && hl deploy artroute-engine --internal`；先跑容器測試 `docker build --target test .`。不重建路網。
3. 搜尋 bench（重量）：`docker exec artroute-engine uv run --no-sync python -m engine.bench.search --json /data/p2-k16-search.json`，同時每 2 秒取樣 `docker stats`。
   - 接受條件：12 個案例都 ≤ 30 s、都有 5 個候選、安全斷言全過、不能直騎的接點 0、樓梯 0 m。和上次一樣。
   - 要記錄：每個案例的 total／coarse 秒數、精算次數、`late`、`gain`、Spearman，最後一行彙總，記憶體峰值。
   - 有案例超過 30 s：先試 `SEARCH_ROUTERS=3` 再跑一次；還是超過就停下回報（不要在現場改常數）。
   - 判讀（寫進 findings，不在現場改程式）：`late` 全是 0 而且 `gain` 中位數 ≤ 0 → 多精算沒有幫助，下個雲端 session 退回 K = 10；`late` 常不是 0、最慢案例 < 20 s → 下個雲端 session 評估 `FINE_KEEP` 24。
4. 吸附 bench 不用跑：`/match` 的程式沒有改。
5. 實測數字寫進 `docs/p2-search-findings.md`（新增一節「精算 16 個」），與上次 K = 10 的逐案例數字並列。
6. **下次重建路網時**（手動或 P3 的第一次排程）：建圖期間每 2–3 秒取樣一次記憶體，取代這次每 30 秒一次的量測（1.996 GiB，可能漏掉峰值；P1.5 每 3 秒量到 2.763 GiB）。
   - 照上次搜尋 bench 的做法，每 2 秒在背景啟動一次 `docker stats --no-stream`、不等上一次結束（`docker stats` 本身要 1–2 秒，串著跑會變成每 3–4 秒一次），帶 UTC 時間寫進主機上的暫存檔，建完就停。
   - 容器的 cgroup 有 `memory.peak` 的話（`docker exec artroute-engine cat /sys/fs/cgroup/memory.peak`），建圖前後各讀一次作為對照；它是容器啟動以來的峰值，建圖前剛重新部署才準。

- 禁止：對其他 app 下 `hl rm`／`hl public`／`hl private`；`hl logs -f`；手動刪 build 或改 `current`。`hl public artroute` 已授權，但排在 P3。

## 已知限制與風險

- 精算 16 個後，最慢的案例 19.9 s（K = 10 時 13.5 s），網站的等待時間中位數 4.7 → 8.3 s。最壞由 25 秒軟性預算與 28 秒的單一吸附時限擋住。`FINE_KEEP` 加到 24 的話，heart 推估會碰到 25 秒的軟性預算（findings 第 8 節）。
- 粗篩的排名幾乎預測不了精算分數（Spearman 中位數 +0.04）。粗篩參數（繞路係數、截斷 × 3、方向權重、`KEEP_A`）只在合成地圖調過；現在靠多精算彌補，粗篩本身變準要在真實資料上多量幾次。
- 建圖的記憶體峰值沒有精確量（每 30 秒取樣，1.996 GiB）。建圖仍在服務中的容器裡跑（`docs/backlog.md` P3）。
- 「trace 吸得到、route 進不去」的部件（ramp）可能讓搜尋的起點跑出圈外而被淘汰。
- 排隊位置只算同一個 worker 內的等待者（近似值）。
- 雲端環境的 Playwright 1.64 要 `chromium_headless_shell-1248`，映像只有 1194：e2e 是用 symlink 指到 1194 跑的（`PLAYWRIGHT_BROWSERS_PATH`）；m1 或 CI 照常。
- 其餘見 `docs/backlog.md` 的 P2 一節（每項附延後原因）。

## 下一步

- 雲端 session 依 findings 第 8 節的判讀評估 `FINE_KEEP` 24（或 `SEARCH_ROUTERS=3`），之後進入 P3：每週自動更新資料（先處理 `docs/backlog.md` P3 一節的建圖鎖、原子寫入、一次性容器）、Cloudflare rate limiting、公開部署（`hl public artroute` 已授權，但公開前要再經使用者確認）。

## 過去的流程紀錄

P0–P1.5 的逐 task 紀錄只存在使用者本機（git-ignored）。需要的結論都已寫進 spec §8 與各 findings 文件。
