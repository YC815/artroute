# 手動微調路線：真實路網實測（本機，2026-10-11）

- 分支 `claude/route-editing-web`（含引擎 `claude/route-editing-engine`），commit `dcaaaf0`。引擎與網站都部署這個 commit。
- 路網：現行 build `20261011T095232Z`，舊 build `20261011T040535Z`（兩個 build 的 `osm_md5` 都是 `28cc8b1b…`，同一份 OSM）。
- 沒有改任何引擎／網站程式與常數。量測腳本在 `docs/scripts/measure_edit.py`、`docs/scripts/measure_verify.py`（在引擎容器內跑，讀線上引擎）。
- 量測機器：m1（8 核，沒有別的負載）；每個請求都是單次取樣，沒有預熱多次，數字只看量級。

## 1. 本機與容器驗證

| 項目 | 結果 |
|---|---|
| `cd engine && uv run pytest -q -W error` | 1680 passed，零警告 |
| 網站 `tsc`／`lint`／`test`／`build`／`test:e2e` | 全過；單元 716（54 檔）、e2e 32；跑完沒有殘留 node／next 行程 |
| m1 容器 `docker build --target test` | EXIT=0，`1680 passed in 51.03s`（linux arm64）；暫存目錄與 image 已刪 |

## 2. 部署

- 引擎 `hl deploy artroute-engine --internal`、網站 `hl deploy artroute`（引擎先、網站後；網站維持私人，引擎只在內網；`hl ls` 確認）。
- 引擎 `/status`：`search_ready`、`planner_ready` 都是 true，build 仍是 `20261011T095232Z`。
- 金鑰：`/data/edit-key` 32 bytes、權限 600，2 個 uvicorn worker（`/proc` 看到兩個 spawn 行程）。同一個簽章用 80 個並行 `/edit` 打（會分到兩個 worker），一個 `invalid_route` 都沒有（55 個 200、25 個 `turn_not_rideable`，都是操作本身的回應）。`hl restart artroute-engine` 之後金鑰的 sha256 不變、重啟前存下的簽章再打 80 次結果相同。
- 網站：從 `artroute` 容器打 `http://artroute:8080/api/status` 200、`/` 200；`/api/match` 的回應有 `route_sig`（64 hex）；`/api/edit`（經網站的代理）200，回應欄位同 `/match`；`http://artroute-engine:8080/status` 從網站容器用 node `fetch` 與 busybox `wget` 都通。

## 3. `/edit`（線上引擎，25 個 bench 案例，fast 路線當輸入）

### 3.1 `heart@taipei_daan` 剪右側繞路

原路線 10.5 km、14 段，v3 overall 68（baseline）。`detour` 段長 706／161／93／11／334 m；708 m 的那一段是第 1 段（706 m）。在那一段的 25%、50%、75% 各點一次：

| 點擊位置 | 結果 | 耗時 | 剪後長度 | 剪後分數 |
|---|---|---|---|---|
| 25% | 成功 | 12 ms | 10065 m（少 ~440 m） | coverage 88／cleanliness 76／direction 24／overall 68 |
| 50% | 成功（同上） | 12 ms | 10065 m | 同上 |
| 75% | 成功 | 12 ms | 10222 m（少 ~287 m） | 88／75／24／overall 67 |

- 三次都沒有 `nothing_to_cut`、`turn_not_rideable`；安全斷言都過。25%、50% 剪的是 439 m 那個圈（findings §2.5 診斷過的，超過自動剪的 300 m 上限），75% 剪的是 285 m 的那個圈（自動剪因為 v3 −0.20 沒剪；使用者指定的剪法不看 v3，剪後 overall 68 → 67）。

### 3.2 每個案例各一次拖曳、重畫

拖曳：抓路線中點（弧長 50%），`to` 是「垂直路線 60／100／150 m、兩側各試，用 `router.route` 吸到最近的路，吸到的點離原路線 ≥ 30 m」的點（腳本的定義，不是引擎的一部分）。`drag@raw100m` 另外做：垂直偏移 100 m、不吸附（使用者隨手拖到任意處）。重畫：路線中點前後各 200 m（總長 < 800 m 時取 1/4），最短與貼圖形（兩端筆畫相同時）。

| 操作 | 樣本 | 成功 | 中位數（全部） | 最慢（全部） | 成功的最慢 | 失敗的錯誤碼 |
|---|---|---|---|---|---|---|
| 剪掉（heart@taipei_daan） | 3 | 3 | 12 ms | 12 ms | 12 ms | — |
| 拖曳到平行路 | 24（`star@kaohsiung_lingya` 找不到平行路，略過） | 10（42%） | 11.5 ms | 175 ms | 21 ms | `turn_not_rideable` 12、`no_road` 2 |
| 拖曳到垂直 100 m（不吸附） | 25 | 13（52%） | 13 ms | 118 ms | 21 ms | `turn_not_rideable` 9、`no_road` 3 |
| 重畫・最短 | 25 | 22（88%） | 18 ms | 25 ms | 25 ms | `turn_not_rideable` 3 |
| 重畫・貼圖形 | 22（3 個案例兩點不在同一筆畫，略過） | 14（64%） | 29 ms | 408 ms | 51 ms | `turn_not_rideable` 5、`no_route` 3 |
| 全部合計 | 99 | 62 | 16 ms（p90 34 ms） | 408 ms | — | — |

- **耗時**：所有請求都遠低於 5 秒期限，沒有 504、沒有半成品。最慢的 408 ms 是 `square@taipei_riverside` 的貼圖形（失敗的 `no_route`）；失敗的拖曳 `no_road`（122–175 ms）比成功的慢，因為 Valhalla 要先搜尋到 `search_cutoff` 才放棄。
- **`no_road` 比例**：拖到平行路 2/24（8%）、拖到垂直 100 m 3/25（12%）。三個 `no_road` 案例都在 `taipei_riverside`（河濱、旁邊是快速道路與堤防，可騎的路少）與一個 `heart@taipei_daan@r30`。就 `no_road` 看，200 m 的範圍沒有造成問題。
- **`turn_not_rideable` 是失敗的大宗**：拖曳 12/24（50%）、貼圖形 5/22、最短 3/25。拖曳在抓點前後 200 m 換成經過放開位置的路，新接點用嚴格版轉彎檢查，台北大安的單行道棋盤常接不回去。本次沒有量不同的拖曳範圍，不能判斷調大或調小是否改善。
- **錯誤說明的問題**：引擎對 `turn_not_rideable` 的說明是「接回原路線的地方要迴轉或轉不過去，試試把範圍選大一點」（`edit/splice.py`）。「重畫」可以選範圍，但「拖曳」的範圍固定 200 m、使用者選不了，說明對拖曳沒有可行的建議。
- 注意：失敗不是 bug：安全檢查擋下的都是回 422 不輸出路線。

### 3.3 安全斷言

對 62 個成功的 `/edit` 輸出各跑一次 `matching.safety.unsafe_segments`（含 `load_denied` 的禁行清單 `bicycle_denied_ways.json`）、`bench.runner.bad_turns`（接點轉彎）、`use_meters` 的 `steps`：**62 個全部通過**（缺口 0、禁行／逆向 0、不能直騎的接點 0、樓梯 0 m）。

## 4. `/verify`（舊 build 的路線在現行 build 上）

做法：容器內腳本直接開舊 build 目錄的 `Router`（不切 `/data/current`、不動任何 build），`match_shape` 產生路線，用 `/data/edit-key` 與 `sign_route`（`data_version` = `20261011T040535Z`）簽章，POST 到線上引擎的 `/verify`。30 條路線：25 個 bench 案例，加 5 條大圖（47–88 km，`hi@taichung_west@6km`、`square@taipei_riverside@6km`、`heart@taipei_riverside@10km`、`star@taipei_daan@8km`、`square@kaohsiung_lingya@12km`）。

| 項目 | 結果 |
|---|---|
| 結果 | 30/30 回 200（重新簽章為現行版本）；`verify_failed` 0、504 0 |
| 耗時 | 中位數 31 ms、最慢 141 ms（`star@taipei_daan@8km`，58 km、69 段）；88 km／84 段 120 ms |
| 誤報 | 0。兩個 build 的 `osm_md5` 相同，舊路網的路線在新路網上本來就該全部通過；沒有任何誤報 |
| 對照組（確認檢查不是空洞的） | 簽章有效、但第 0 段是穿過街廓的直線 → 422 `verify_failed`，`segments: [0]`（23 ms）；兩段之間有 200 m 缺口 → 422，`segments: [0, 1]`（2 ms） |

- 限制：兩個 build 資料相同，所以「有差異的路線標出的段落合理」沒有真的被考驗（沒有真實的差異可標）；只有上面兩個人工對照組。要驗這一項要等下一次真的改了資料（例如每週重建後）。
- `same_geometry`（O(n·m) 純 Python）沒有在這 30 條裡被觸發：它只在 edge 被判逆向／禁行、要做「合法重現」時才跑，這 30 條沒有這種 edge。另外在容器內單獨量它（`REPLAY_SAMPLE_M = 0.5`，一條直線、10 m 一個頂點、與自己比較）：1 km 0.06 s、5 km 1.31 s、10 km 5.26 s、20 km 21.0 s——約 O(L²)。比的是單一 edge 的範圍；Valhalla 的 edge 通常 < 1 km，所以現實中應該遠低於 15 秒，但沒有在真實案例上量到。
- **`/match` 排隊**：`/verify` 進行時同時送 6 個 `/match`（letter_l@kaohsiung_lingya，單獨跑 36–40 ms），三次試驗：`/match` 延遲 43–164 ms、全部 200、沒有 504。這 6 個 `/match` 彼此也在排隊（每個 worker 一個 `Semaphore(1)`、每個約 40 ms），最慢的 128–164 ms 與「6 個 `/match` 分到 2 個 worker 互相排隊」的預期（~120 ms）差不多，看不出 `/verify` 額外造成多少延遲——因為這次 `/verify` 只花 ~145 ms。**`/verify` 長到 15 秒時的排隊沒有量到**（沒有找到那麼慢的真實路線），只能說：排隊延遲 = 同 worker 上 `/verify` 的剩餘時間，上限由 `/match` 自己的 5 秒期限截斷成 504。

## 5. 網站冒煙

- 已做（從 `artroute` 容器，不經過 Cloudflare Access）：`/api/status` 200、`/` 200、`/api/match` 含 `route_sig`、`/api/edit` 200（見 §2）。
- **沒做**：用瀏覽器載入已部署的 `artroute`、進入「微調路線」看有沒有錯誤。私人網站要 Cloudflare Access 的 Google 登入，登入頁停在「允許 cloudflareaccess.com 存取個人資料」的同意畫面——那是新的授權，沒有替使用者按；ssh 隧道繞過 Access 的做法被權限擋下，也沒有繞。已關閉瀏覽器工作區。e2e（假引擎）已覆蓋進入微調的流程（32 個全過），但沒有連過真引擎的瀏覽器操作。

## 6. 沒做與待使用者決定

- 沒做：瀏覽器冒煙（§5）；`/verify` 對「真的有差異的路網」的檢驗（§4）；`/verify` 長到 15 秒時的 `/match` 排隊；拖曳範圍不同值（100／300 m）的比較（不在現場改常數）。
- 使用者自己做：真實路網上的完整操作（剪掉、重畫最短／貼圖形、拖曳、復原／重做、完成、我的路線）；iOS Safari 實機；375×667 小螢幕工具列遮擋。
- 決定：
  1. 200 m 拖曳範圍：`no_road` 8–12% 看來可以接受；但 `turn_not_rideable` 佔拖曳失敗的大宗（50%），要不要調範圍或調說明文字。
  2. 錯誤說明：拖曳回 `turn_not_rideable` 時說明「把範圍選大一點」對拖曳不適用（見 §3.2），要不要依操作改文字（例如拖曳改成「換個位置，或改用重畫」）。
  3. `/verify` 是否需要改成 KD-tree：實測 30 條路線最慢 141 ms，沒有需要；`same_geometry` 平方成長只有單一 edge 很長（>10 km）才危險，目前看沒有迫切性。
