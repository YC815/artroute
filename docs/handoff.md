# 交接：P2 起點（2026-10-11）

## 目前狀態

- P0（引擎可行性）、P1（`/match` 引擎 + 網站）、P1.5（吸附品質）都已完成並合進 `main`。
- 部署在使用者的 homelab 主機 m1（工具 `hl`）：
  - 網站 `artroute`：https://artroute.fiir.fyi ，**私人**（Cloudflare Access）。
  - 引擎 `artroute-engine`：只在內網（`http://artroute-engine:8080`），2 個 uvicorn worker。
  - 路網 build `20261010T235143Z`：禁行樓梯 16,359 條、快速道路 2,399 條，人行道 dismount 4,994 條；回滾用 `20261010T183449Z`。
- 真實路網 bench（25 案例）：overall 中位數 62、平均 53.6；不安全 0、不能直騎的接點 0、樓梯 0 m。預設文字「I ♥ TAIPEI」56/72/39，單次吸附約 260 ms。數字細節見 `docs/p1-5-quality-findings.md`。

## 使用者已做的決定

- 自行車；不可逆向；快速道路與樓梯禁行；只做台灣；不做登入；必須能匯出 GPX（Strava「我的路線 → 建立路線 → 匯入」）。
- 「人行道盡量避免」→ 決策 A6：沒有標示可騎的人行道 Valhalla 本來就不走；明確標示可騎的共用人行道照用。使用者仍可改成全面禁走（spec §8 第 19 列有量測，代價是品質下降）。
- 加快開發速度：能平行就平行，小修正直接驗證。

## 下一階段 P2：自動搜尋

- 規格：spec §4.6（圈內找最佳擺放：中心點、半徑 R ≤ 10 km、最小/最大公里數、旋轉範圍；起點必須在圈內；公里數只算圖本身）。完成條件（spec §7）：R = 5 km 時 30 秒內回傳前 5 名。
- 一起做的品質項目：`docs/backlog.md` 的「P2」一節（評分調整與折返移除、跨水域警告、多部件開放筆畫整條反向、小圖形評分等）。評分是自動搜尋的目標函數，先定評分再做搜尋。
- 資料：§4.6 的粗篩需要「可騎道路」的空間資料（FlatGeobuf）。它要由建圖流程（`engine/src/engine/data/`）產生；雲端只能用合成地圖開發與測試，全台資料的產生與量測是本機待辦。
- 契約：P2 會新增 API（例如自動搜尋的 endpoint），照 §4.7 的方式寫進 spec、產生 OpenAPI、讓 web 型別同步。

## 本機才能做的事（雲端 session 寫進 PR 的「本機待辦」）

- 部署引擎：`cd engine && hl deploy artroute-engine --internal`。
- 重建路網（在 m1 的容器內背景跑，完成後才會驗證並切換 `current`）：`docker exec -d artroute-engine sh -c "uv run --no-sync python -m engine.data.cli > /data/build.log 2>&1; echo EXIT=\$? >> /data/build.log"`。
- 真實路網 bench：容器內 `uv run --no-sync python -m engine.bench --baseline /data/baseline.json`；更新 baseline 用 `--update-baseline`（安全失敗永遠不會被寫入）。
- 部署網站：`cd web && hl deploy artroute`（環境變數 `ENGINE_URL=http://artroute-engine:8080` 已設定）。
- 禁止：對其他 app 下 `hl rm`／`hl public`／`hl private`；`hl logs -f`；手動刪 build 或改 `current`。`hl public artroute` 已授權，但排在 P3。

## 已知限制

- 「trace 吸得到、route 進不去」的路段（例如匝道）會讓部件只能排在路線最前面，多一條長 connector（預設文字約 2.3 km）。
- `letter_l@taipei_daan`：轉角卡單行道，完整的 L 要繞約 3 km（P2：整條反向）。
- 轉角 ≤ 30° 的接點不檢查；edge_walk 在兩條 way 共用端點時可能配錯（bench 的禁行斷言是第二道防線，tiles 的 access 才是真正的防線）。

## 過去的流程紀錄

P0–P1.5 的逐 task 紀錄（brief、審查、裁決 A1–A7 的原始討論）只存在使用者本機（git-ignored），不在 repo。需要的結論都已寫進 spec §8 與各 findings 文件。
