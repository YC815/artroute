# 交接：P2.5「像不像」（2026-10-11）

## 目前狀態

- P0、P1、P1.5、P2 已完成。PR #2（精算 16 個候選全部跑完）已合併（`7fdb57f`），真實路網量測見 `docs/p2-search-findings.md` 第 8 節。
- 部署中（m1）：
  - 網站 `artroute`：https://artroute.fiir.fyi ，**私人**（Cloudflare Access）。程式是 P2（`dc48f8a`），PR #2 沒有改網站。
  - 引擎 `artroute-engine`：只在內網（`http://artroute-engine:8080`），2 個 uvicorn worker。程式是 PR #2 的 `d69e0ca`（與 main 的引擎程式相同）。`/status` 的 `search_ready: true`。
  - 路網 build `20261011T040535Z`（有可騎道路索引），回滾用 `20261010T235143Z`。
- 下一階段：**P2.5「像不像」**。設計：`docs/superpowers/specs/2026-10-11-p2-5-likeness-design.md`（使用者逐段核可）；決策列在主 spec §8 第 30–37 列。P3（每週重建、rate limiting、公開）排在 P2.5 之後。

## 為什麼做 P2.5

使用者說自動搜尋與吸附「拉出來的都不像」。真實路網診斷的結論是演算法問題：吸附用的 Valhalla 軌跡比對是 GPS 糾錯的設定，會產生鋸齒、小刺、突刺；文字的連接線佔 36%；v2 評分量不到這些；圖片旋轉不限。細節見 P2.5 spec §1。

## 使用者的決定（P2.5 spec §2）

- 以手機 Strava 動態牆縮圖（約 300 px）判斷像不像。
- 旋轉預設 ±15°，越正越加分。
- 可以微調圖形：局部平移與轉角位置、長寬比、文字字距與位置、曲線改折線。
- 拖曳快、放開 1–3 秒換精緻版、搜尋上限 60 秒。
- 核心做法：自建描形規劃器為主線，先試 A（Valhalla 參數）當基準。
- 連接線少量可以接受（輕量版）。
- 網站加「隱藏原圖」按鈕。
- 既有決定照舊：自行車、不逆向、快速道路與樓梯禁行、只做台灣、不登入、必須能匯出 GPX、人行道決策 A6（主 spec §8 第 19 列）。

## 本機已做（2026-10-11）

- `engine/tests/data/likeness/routes.json.gz`：156 條真實路線（12 個搜尋案例 × 前 5 名的現行路線，加上前 2 名擺放用 A 參數 V1–V4 重新吸附）。
- `engine/tests/data/likeness/labels.json`：使用者對 36 組盲測配對的判斷。
- 格式與 V0–V4 的定義見 `engine/tests/data/likeness/README.md`。A 實驗的初步觀察見 P2.5 spec §1。

## 雲端的工作（建議切成三個 PR，每個 PR 一輪本機驗證）

1. **PR A：Q1＋Q2**（P2.5 spec §4、§5）
   - 評分 v3＋用 `labels.json` 校正（一致 ≥ 80%，校正測試進 CI）；`/match` 的 score 加 `direction`（重新產生 `engine/openapi.json`，跑 `cd web && pnpm gen:engine-types`）；網站顯示三項。
   - bench 記 v3 與 v2，並為每個案例輸出 SVG 疊圖（不加 matplotlib）。
   - 下一輪標註樣本的產生工具：在引擎容器內跑，輸出與 `routes.json.gz` 相同格式的樣本，以及一個自含的盲測標註頁（HTML 樣板，使用 Artifact 的 `db` capability 存選擇，參考本機這次的做法：一次一組、← ↓ → 快捷鍵、doc id `p01`…、欄位 `pair`、`choice`（`L`／`R`／`same`）、`n`、`at`）。
   - A 參數可設定（`sigma_z` 走設定檔覆寫、`turn_penalty_factor` 走請求）；依標註，fast 預設改成 `turn_penalty_factor` 500，`sigma_z` 不動（§5.1）。
   - 剪小刺與小圈（含使用者指出的「直線上多繞的小圈」）、旋轉 ±15° 與排名扣分、隱藏原圖按鈕。
2. **PR B：Q3 描形規劃器**（§6）：路網圖萃取（建圖流程，需要本機重建路網）、規劃器、`quality` 欄位、`planner_ready`。合成地圖測試涵蓋 §8 的場景。
3. **PR C：Q4**（§7）：變形、連接線輕量版、搜尋改用規劃器與 60 秒預算。

每個 PR：
- `cd engine && uv run pytest -q -W error` 零警告全過；有改網站就跑 `cd web && pnpm exec tsc --noEmit && pnpm lint && pnpm test && pnpm build && pnpm test:e2e`。
- 安全相關的改動（剪小刺與小圈、規劃器輸出、Valhalla 照著走）要有真正的審查，測試要做到拿掉功能會紅。
- PR 說明要有「本機待辦」與「給本機 Agent 的 prompt」（格式同 PR #2），內容依下一節。

## 寫給本機的 prompt 要包含什麼

本機 Agent 不知道雲端做了什麼，prompt 要自足。依序寫：

1. **目標與停止規則**：要驗證什麼、接受條件（數字）、任何一步沒過就停下回報，不在現場改程式或常數。
2. **本機驗證**：checkout 分支，`cd engine && uv run pytest -q -W error`；有改網站就跑網站全套（上一節的指令）。
3. **容器測試**（m1，linux arm64）：
   - 本機不是 m1，也沒有 docker。docker 指令一律寫成 `ssh m1 'export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.orbstack/bin:$PATH"; docker …'`。
   - 把 `engine/` rsync 到 m1 的暫存目錄（過濾規則與 `hl deploy` 相同：`source ~/homelab/lib/hl-lib.sh` 之後用 `"${HL_RSYNC_ARGS[@]}"`），在那裡 `docker build --target test --progress=plain . > build.log 2>&1; echo EXIT=$?`，再 `grep passed build.log`。m1 的遠端 shell 是 zsh，`${PIPESTATUS[0]}` 拿不到值。跑完刪掉暫存目錄與 image。
4. **部署**：
   - 引擎：`cd engine && hl deploy artroute-engine --internal`（只能內網，絕不 `hl public`）。網站：`cd web && hl deploy artroute`（維持私人）。
   - Claude Code 的 auto mode 會把部署當成正式環境動作擋下：部署前先向使用者取得授權，被擋就停下說明，不要繞過。
   - 部署後驗證：容器裡沒有 curl。引擎用 `docker exec artroute-engine python3 -c "import urllib.request as u; print(u.urlopen('http://127.0.0.1:8080/status').read().decode())"`；網站容器 `artroute` 有 node（`fetch`）與 busybox `wget`，要測 hl 網路內的呼叫就從 `artroute` 容器打 `http://artroute-engine:8080` 或 `http://artroute:8080`。
   - 要寫明 `/status` 應該看到什麼（例如 `planner_ready: true`）。
5. **重建路網**（只有改了建圖流程時）：
   - `docker exec -d artroute-engine sh -c "uv run --no-sync python -m engine.data.cli > /data/build.log 2>&1; echo EXIT=\$? >> /data/build.log"`，等 `build.log` 出現 `EXIT=`。
   - 建圖期間每 2–3 秒在背景啟動一次 `docker stats --no-stream`（不等上一次結束），帶 UTC 時間寫進 m1 的暫存檔，記錄記憶體峰值；VM 上限 7.817 GiB。
   - `validate_build` 沒過就停，不准強制。不手動刪 build、不改 `/data/current`。
6. **bench**（在引擎容器內）：
   - 吸附 bench `docker exec artroute-engine uv run --no-sync python -m engine.bench`，搜尋 bench `… python -m engine.bench.search --json /data/<名稱>.json`（同時每 2 秒取樣 `docker stats`）。
   - 寫明接受條件（安全斷言全過、不能直騎的接點 0、樓梯 0 m、v3 不退步、時間上限）與要記錄的數字。
   - 安全斷言失敗永遠算失敗，不得寫進 baseline。
7. **標註與驗收**：
   - 需要使用者標註時：在容器內跑樣本產生工具，把標註頁用 Artifact 發佈（帶 `db` capability），請使用者標完，用 ArtifactData 讀回 `votes`，存成 `engine/tests/data/likeness/labels-<日期>.json` 並 commit。
   - 部署前挑 4 個代表案例的改前／改後縮圖（bench 的 SVG）給使用者看，使用者同意才部署成預設。
8. **寫回**：
   - 實測數字寫進 `docs/p2-5-likeness-findings.md`（新增一節，標明分支與 commit），沒跑的步驟照實寫。
   - 更新本檔「目前狀態」與下一輪雲端的起點，commit 到同一個分支並 push。
9. **禁止事項**：對其他 app 下 `hl rm`／`hl public`／`hl private`；`hl logs -f`；前景跑不會結束的指令（dev server、watch、`tail -f`，要跑就背景、用完關掉）；手動刪 build 或改 `current`；`hl public artroute` 已授權但排在 P3，P2.5 不做。

## 已知限制與風險

- 雲端不能在真實路網上驗證規劃器，所以規劃器先以 `quality: "fine"` 選用實作（主 spec §8 第 34 列）。
- 校正資料只有 36 筆、12 個案例、3 種圖形：v3 可能對其他圖形（圖片描邊、長文字）不準。每次大改後多標一輪。
- 粗篩排名幾乎預測不了精算分數（Spearman 中位數約 0）。Q4 改用規劃器精算後要重新量。
- 建圖仍在服務中的容器裡跑（`docs/backlog.md` P3）；Q3 會加大建圖的記憶體，要量峰值。
- 其餘見 `docs/backlog.md`。

## 過去的紀錄

- P0–P2 的實測：`docs/p0-findings.md`、`docs/p1-engine-findings.md`、`docs/p1-quality-investigation.md`、`docs/p1-5-quality-findings.md`、`docs/p2-search-findings.md`。
- P0–P1.5 的逐 task 紀錄只存在使用者本機（git-ignored）；結論都已寫進主 spec §8 與各 findings 文件。
