# 交接：P2 自動搜尋（2026-10-11）

## 目前狀態

- P0、P1、P1.5 已合進 `main` 並部署在 m1。
- **P2（自動搜尋＋評分 v2）在雲端 session 完成實作與合成地圖測試，還沒有在真實路網上量測、還沒部署。** 分支 `claude/artroute-p2-development-d8henn`，PR 到 `main`。
- 部署中（P1.5）：
  - 網站 `artroute`：https://artroute.fiir.fyi ，**私人**（Cloudflare Access）。
  - 引擎 `artroute-engine`：只在內網（`http://artroute-engine:8080`），2 個 uvicorn worker。
  - 路網 build `20261010T235143Z`（沒有可騎道路索引，搜尋要重建後才能用）；回滾用 `20261010T183449Z`。

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

順序重要。完整的步驟、接受條件與停止條件寫在 PR 說明的「給本機 Agent 的 prompt」。

1. 合併 PR（或 checkout 分支），部署引擎：`cd engine && hl deploy artroute-engine --internal`；容器測試 `docker build --target test .`。
2. **在舊 build 上**跑吸附 bench：P2 沒有改 `/match` 的路線，v1 分數必須與 committed baseline 逐案例相同；不同就是程式行為改變，停下回報。
3. 重建路網（產生可騎道路索引）：`docker exec -d artroute-engine sh -c "uv run --no-sync python -m engine.data.cli > /data/build.log 2>&1; echo EXIT=\$? >> /data/build.log"`。驗證沒過就停，不准強制。量建圖的記憶體峰值（萃取估計約 1.5 GB，在建 tiles 之前）。
4. 新 build 上跑吸附 bench（安全全過），接受後 `--update-baseline`（baseline 換成 v2＋v1 欄位）並 commit。
5. 搜尋 bench：`uv run --no-sync python -m engine.bench.search --json /data/p2-search.json`。完成條件：12 個案例都 ≤ 30 s、5 個候選、安全全過。
6. 部署網站：`cd web && hl deploy artroute`（維持私人），在 hl 網路內驗證 `/api/status`（`search_ready: true`）、首頁、worker、`/api/search`。
7. 實測數字寫進 `docs/p2-search-findings.md`。

- 禁止：對其他 app 下 `hl rm`／`hl public`／`hl private`；`hl logs -f`；手動刪 build 或改 `current`。`hl public artroute` 已授權，但排在 P3。

## 已知限制與風險

- 搜尋的時間只在合成資料上量過粗篩（R = 5 km：1–3 s，大圖形約 6.5 s）；精算（每個候選一次完整吸附）只能在真實路網量。超標時先試 `SEARCH_ROUTERS=3`。
- 粗篩參數（繞路係數、截斷 × 3、方向權重、K）只在合成地圖調過。
- 「trace 吸得到、route 進不去」的部件（ramp）可能讓搜尋的起點跑出圈外而被淘汰。
- 排隊位置只算同一個 worker 內的等待者（近似值）。
- 雲端環境的 Playwright 1.64 要 `chromium_headless_shell-1248`，映像只有 1194：e2e 是用 symlink 指到 1194 跑的（`PLAYWRIGHT_BROWSERS_PATH`）；m1 或 CI 照常。
- 其餘見 `docs/backlog.md` 的 P2 一節（每項附延後原因）。

## 過去的流程紀錄

P0–P1.5 的逐 task 紀錄只存在使用者本機（git-ignored）。需要的結論都已寫進 spec §8 與各 findings 文件。
