# 交接：P2.5「像不像」（2026-10-11）

## 目前狀態

- P0、P1、P1.5、P2 已完成。P2.5 的設計：`docs/superpowers/specs/2026-10-11-p2-5-likeness-design.md`；實作計畫：`docs/superpowers/plans/2026-10-11-p2-5-likeness.md`。
- **PR A**（#3，分支 `claude/p2-5-likeness-rl4bgr`）：Q1＋Q2。評分 v3、搜尋排名 rank、A 參數（轉彎懲罰 500）、剪小刺與小圈、旋轉 ±15°、隱藏原圖、bench 的 v3／v2／SVG／改前旗標、標註樣本工具。校正沒達到 80%，**使用者決定改成防退步**（findings §1、主 spec §8 第 38、39 列）。**本機已驗證並部署**（2026-10-11，findings §2）：安全全過、v3 平均 68.1 → 67.9、搜尋最慢 28.5 s；baseline 已換成 v3。
- **PR B**（#4，分支 `claude/p2-5-planner-rl4bgr`，疊在 PR A 上）：Q3。可騎路網圖、描形規劃器、`/match` 的 `quality: "fast" | "fine"`、`/status` 的 `planner_ready`。**本機已驗證並部署**（2026-10-11，findings §4）：
  - fine 沒有全面贏過 fast（v3 好 3、差 19；盲測有表態 8 組只有 3 組選 fine）。但使用者看圖認為有的案例 fine 好很多，所以網站兩版都算、可切換，預設快速版（主 spec §8 第 60 列）。
  - bench 的逆向誤報已修（合法重現，§8 第 61 列）；fast／fine／搜尋 bench 全部安全。
- 部署中（m1），程式都是 `d7f6283`：
  - 網站 `artroute`：https://artroute.fiir.fyi ，**私人**（Cloudflare Access）。
  - 引擎 `artroute-engine`：只在內網（`http://artroute-engine:8080`），2 個 uvicorn worker。`/status` 的 `search_ready`、`planner_ready` 都是 true。
  - 路網 build `20261011T095232Z`（有可騎路網圖），回滾用 `20261011T040535Z`。
- **PR C**（分支 `claude/route-editing-engine`）：手動微調引擎。`POST /edit`（剪掉、重畫、拖曳）、`POST /verify`（舊路網草稿重新檢查）、`route_sig`（整條路線一個 HMAC 簽章，金鑰 `/data/edit-key`）、`matching/safety.py` 與 `matching/replay.py`（bench 與 `/verify` 共用）。**尚未部署、未在真實路網上驗證**（雲端連不到 m1）；決定見主 spec §8 第 62 列。PR D（網站）要先 merge 此分支才能做 D3 以後。
  - 本機待辦：
    1. 部署：引擎先、網站後（新欄位與新端點都是只增加）。
    2. `heart@taipei_daan` 剪右側繞路要多久（剪掉不套用圈長上限、不限次數，只受 5 秒期限）。
    3. 每個 bench 案例都拖曳一次，量時間，並確認 200 m 的拖曳範圍合不合適（spec §3.3）。
    4. 用 m1 的兩個 build 驗 `/verify`：舊 build 的路線在新 build 上的誤報率與耗時。`same_geometry` 是 O(n·m) 的純 Python，長 edge 可能超過 15 秒而回 504（`docs/backlog.md`）。
    5. `/verify` 與 `/match`（與 `/edit`）共用 `Semaphore(1)`：`/verify` 進行期間（最長 15 秒）`/match` 會排隊，要實測影響。
- 下一步：
  1. 手動微調 PR D（網站）；PR C 的本機待辦見上。spec：`docs/superpowers/specs/2026-10-11-route-editing-design.md`（主 spec §8 第 56–58、62 列）；計畫：`docs/superpowers/plans/2026-10-11-route-editing.md`。
  2. Q4（P2.5 spec §7）：含嘗試性的「長直線優先」（§7.4，要盲測驗收）。規劃器的問題（折回的 detour、細碎階梯、河濱變長、退回 fast 的比例沒量）先查，見 `docs/backlog.md`。
- P3（每週重建、rate limiting、公開）排在 P2.5 之後。

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

## 本機已做（2026-10-11，P2.5 開始前）

- `engine/tests/data/likeness/routes.json.gz`：156 條真實路線；`labels.json`：使用者對 36 組盲測配對的判斷。格式見同目錄 `README.md`。

## 雲端的工作（三個 PR，每個 PR 一輪本機驗證）

1. **PR A：Q1＋Q2** — 完成：本機驗證並部署（2026-10-11，findings §2）。
2. **PR B：Q3 描形規劃器** — 完成：本機驗證並部署（2026-10-11，findings §4）。fine 沒有全面贏過 fast，網站改成兩版可切換、預設快速版。
3. **Q4**（§7）：變形、連接線輕量版、搜尋改用規劃器與 60 秒預算、長直線優先（嘗試性）。可以開始；手動微調（PR C／D）與它互不依賴，順序由使用者決定。

每個 PR：
- `cd engine && uv run pytest -q -W error` 零警告全過；有改網站就跑 `cd web && pnpm exec tsc --noEmit && pnpm lint && pnpm test && pnpm build && pnpm test:e2e`。
- 安全相關的改動要有真正的審查，測試要做到拿掉功能會紅。
- PR 說明要有「本機待辦」與「給本機 Agent 的 prompt」，內容依下一節。

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

- 雲端不能在真實路網上驗證；真實路網的數字都在 `docs/p2-5-likeness-findings.md`。
- v3 只當診斷參考，不當部署門檻：第一輪一致 10/15，第二輪 41 組只有 10 組有表態、一致 6/10。下一輪標註要想辦法讓有表態的配對變多（`docs/backlog.md`）。
- 規劃器的權重只在合成地圖上定過；真實路網上 fine 中位數 239 ms，但像不像沒有全面贏過 fast（findings §4.2）。
- 粗篩排名幾乎預測不了精算分數（Spearman 中位數約 0）。Q4 改用規劃器精算後要重新量。
- 建圖仍在服務中的容器裡跑（`docs/backlog.md` P3）；加了路網圖後重建的記憶體峰值 3.54 GiB／7.817 GiB。
- 其餘見 `docs/backlog.md`。

## 過去的紀錄

- P0–P2 的實測：`docs/p0-findings.md`、`docs/p1-engine-findings.md`、`docs/p1-quality-investigation.md`、`docs/p1-5-quality-findings.md`、`docs/p2-search-findings.md`。
- P0–P1.5 的逐 task 紀錄只存在使用者本機（git-ignored）；結論都已寫進主 spec §8 與各 findings 文件。
