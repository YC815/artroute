# artroute

Strava GPS 藝術：文字／圖片 → 地圖上移動旋轉縮放 → 吸附到台灣真實、合法的自行車路線 → 匯出 GPX。`engine/`（Python 3.13、uv、FastAPI、pyvalhalla 3.9.1）+ `web/`（Next.js 16、shadcn/ui、MapLibre、pnpm）。

## 指令
- 引擎驗證：`cd engine && uv run pytest -q -W error`（必須零警告全過）。
- 網站驗證：`cd web && pnpm exec tsc --noEmit && pnpm lint && pnpm test && pnpm build && pnpm test:e2e`。

## 不可放寬的安全不變式
- 不逆向騎單行道；不走被標記的快速道路與樓梯（`engine/src/engine/data/bike_patch.py`）。
- 絕不畫直線：輸出相鄰兩點必須來自同一個 Valhalla 結果（或其切片），否則相距 ≤ `JOIN_TOLERANCE_M`（1 m）；接不上就斷開或丟部件並給 warning。
- 轉角 > 30° 的接點必須 Valhalla 會直接騎過去（`engine/src/engine/matching/turns.py`、`join.py`）。
- bench 的安全斷言失敗永遠算失敗、不得寫進 baseline。
- `/match` 契約以 spec §4.7 為準；改契約要重新產生 `engine/openapi.json` 並跑 `cd web && pnpm gen:engine-types`（`openapi-sync.test.ts` 會檢查）。

## 環境限制
- 部署在使用者的 homelab（主機 m1，工具 `hl`）。雲端 session 連不到 m1：不部署、不跑真實台灣路網的 bench、不建全台 tiles。需要的事寫進 PR 說明的「本機待辦」。
- 真實路網相關的程式用合成地圖測試（`engine/tests/osm_fixture.py` + `@pytest.mark.valhalla`）。

## 文件
- 設計：`docs/superpowers/specs/2026-10-10-strava-art-design.md`（§8 是每個設計決策與理由；新決策要加一列）。
- P2.5 相似度設計：`docs/superpowers/specs/2026-10-11-p2-5-likeness-design.md`。
- 交接與下一步：`docs/handoff.md`。待辦：`docs/backlog.md`。
- 實測紀錄：`docs/p0-findings.md`、`docs/p1-engine-findings.md`、`docs/p1-quality-investigation.md`、`docs/p1-5-quality-findings.md`、`docs/p2-search-findings.md`。

## 工作方式
- 設計抉擇自己決定、寫進 spec §8 並附理由，使用者可推翻。
- 安全相關的改動要有真正的審查與非空洞的測試（拿掉功能測試要會紅）。
