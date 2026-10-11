# P2 自動搜尋：部署與真實路網量測

沒有標註來源的數字都是實測值，來自 m1 上的 `artroute-engine`、`artroute` 容器與本機（MacBook Air M3，darwin arm64，Node v26.8.1）。推測一律標「推測」。時間都是 UTC，日期 2026-10-11。

程式：PR #1 分支 `claude/artroute-p2-development-d8henn`。雲端的最後一個 commit 是 `528e2f3`；本機另外加了 `dc48f8a`（測試修正，見第 1 步）和 `1e0243b`（baseline v2，見第 5 步）。引擎與網站部署的都是 `dc48f8a` 的程式（`1e0243b` 只改 baseline.json）。

## 結論

- 完成條件（spec §7：R = 5 km 時 30 秒內回傳前 5 名）**達成**：12 個案例都拿到 5 個候選，最慢 13.5 s（`heart@taipei_riverside`），中位數 4.7 s。60 個候選全部通過安全斷言，不能直接騎的接點 0，樓梯 0 m，公里數也全部在範圍內。
- P2 沒有改變 `/match` 的路線：舊 build 上 25 個案例的長度與 v1 分數，和 committed baseline 完全相同。
- 新 build `20261011T040535Z` 的 pbf 與舊 build 是同一份。路線與第 3 步逐欄相同，baseline 已換成 v2。
- `SEARCH_ROUTERS` 維持預設 2，沒有設定環境變數。
- 網站已部署，仍是私人（只有本人能看）。hl 網路內的驗證全部通過。
- 之後（PR #2）改成 16 個候選全部精算：仍然合格，最慢 19.9 s、中位數 8.3 s，前 5 名平均 overall 中位數高 2.1 分。見第 8 節。

## 1. 本機驗證

| 指令 | 開始–結束 | EXIT | 結果 |
| --- | --- | --- | --- |
| `cd engine && uv run pytest -q -W error` | 03:53:45–03:54:34 | 0 | 986 passed，零警告 |
| `pnpm install --frozen-lockfile` | 03:53:59–03:54:03 | 0 | |
| `pnpm exec tsc --noEmit` | 03:54:03–03:54:06 | 0 | |
| `pnpm lint` | 03:54:06–03:54:09 | 0 | |
| `pnpm test` | 03:54:09–03:54:13 | **1** | 415 passed、**1 failed** |

- 失敗的測試是 `web/src/lib/text/default-text-fixture.test.ts`。雲端產生的 `engine/src/engine/bench/default_text.json`，在本機有一個座標差最後一位：`-0.3414023000498289`（檔案）對 `-0.34140230004982897`（本機產生）。測試原本用 `toEqual` 逐位比較浮點數。
- 原因（推測）：♥ 的字形用 `Math.sin(t) ** 3` 和 `Math.cos` 產生（`web/src/lib/text/hershey.ts`），這類函數的最後一位會隨平台或 Node 版本不同。雲端的 Node 版本沒有紀錄。程式行為沒有改變。
- 照規則先停下回報；使用者授權修正後，`dc48f8a` 把測試改成：文字、字型、每筆的 closed 與點數仍然逐項相等，座標只比到 1e-12。fixture 沒動。驗證過這個測試沒有失去作用：座標改 1e-9 會紅（`expected 1.0000000272292198e-9 to be less than 1e-12`），少一個點也會紅。
- 修正後重跑網站全套：

  | 指令 | 開始–結束 | EXIT | 結果 |
  | --- | --- | --- | --- |
  | `pnpm install --frozen-lockfile` | 03:59:15–03:59:15 | 0 | |
  | `pnpm exec tsc --noEmit` | 03:59:15–03:59:17 | 0 | |
  | `pnpm lint` | 03:59:17–03:59:20 | 0 | |
  | `pnpm test` | 03:59:20–03:59:23 | 0 | 33 個檔案、416 passed |
  | `pnpm build` | 03:59:23–03:59:25 | 0 | |
  | `pnpm test:e2e` | 03:59:25–03:59:40 | 0 | 7 passed |

- 引擎的程式沒有改，所以沒有重跑；換 baseline 之後另外跑過一次，986 passed。

## 2. 部署引擎（不重建）

- 部署前 `readlink /data/current`：`builds/20261010T235143Z`。
- 容器測試 `docker build --target test .`：03:59:23–04:01:26，EXIT=0，**986 passed**，和本機相同。用的是 m1 上的暫存目錄（rsync 的過濾規則與 `hl deploy` 相同），跑完就刪掉暫存目錄與 image。
- `hl deploy artroute-engine --internal`：04:01:34–04:02:07，EXIT=0。2 個 uvicorn worker。
- `/health`：`{"ok":true}`。`/status`：`build_id` 仍是 `20261010T235143Z`，`search_ready: false`（舊 build 沒有可騎道路索引，符合預期）。
- `/match` 樣本：從 `artroute` 容器呼叫 `http://artroute-engine:8080/match`，送大安區的 L 形 `[[121.5405,25.0375],[121.5405,25.0300],[121.5470,25.0300]]`，結果 **200**。
  - 路線 2,572 m、89 個點，點列的 md5 和部署前那次（P1.5 的引擎）完全相同。
  - 只有分數因為改成 v2 而不同：overall/coverage/cleanliness 18/36/0 → 22/44/0。兩次都有 `partial` 警告。
- hl 網路內的 HTTP 呼叫都從 `artroute` 容器用 node `fetch` 送出：兩個容器裡都沒有 curl。

## 3. 舊 build 上的吸附 bench

- `python -m engine.bench`：04:02:25–04:02:37，EXIT=0，25/25 成功。
- 安全：不安全 0、無法判斷 0、轉彎不能直接騎的接點 0、steps 0 m。footway／steps／parking_aisle 合計 30,766／0／1,292 m。
- 沒有任何「退步：」行。baseline 是 v1，所以 bench 用本次路線的 v1 分數比較。
- 逐欄比對：再跑一次，用 `--update-baseline --baseline /tmp/…` 把完整結果寫到容器的 `/tmp`（committed baseline 沒有動），和 committed baseline 逐案例比對 v1 overall、coverage、cleanliness 與 `length_m`。**25 個案例全部相同，不同的案例數 0。**
- v1 overall 平均 53.6、中位數 62。v2 的數字見第 5 步。

## 4. 重建路網

- `docker exec -d artroute-engine sh -c "uv run --no-sync python -m engine.data.cli > /data/build.log 2>&1; echo EXIT=\$? >> /data/build.log"`：從 04:03:17 開始，`meta.json` 的 `created_at` 是 04:07:58，`EXIT=0`。全程約 4 m 41 s。
  - 下載從 04:03:20 到約 04:05:35（以 build id 的時間推算）；之後是標記、可騎道路萃取、tiles 與驗證。
  - Geofabrik 轉址到 `taiwan-261009.osm.pbf`。
- 新 build **`20261011T040535Z`**，`/data/current` 已指過去。
- pbf md5 `28cc8b1b689b45b640b5a8c5d2042b79`，**與舊 build `20261010T235143Z` 相同**。`bicycle_denied_ways.json` 逐 byte 相同。tiles 有 261 個 .gph，`du -sk` 344,424 KiB，與舊 build 相同。
- `prune` 照設計刪掉最舊的 `20261010T183449Z`。現在剩 `20261010T235143Z`（回滾用）和 `20261011T040535Z`。
- 可騎道路索引（`extract_rideable` 的紀錄）：

  | 項目 | 數值 |
  | --- | --- |
  | way | 654,328 條 |
  | 路段（≤ 200 m） | 5,777,571 個 |
  | 檔案合計 | 93,913,530 bytes：`rideable_segments.npy` 92,441,264 + `rideable_offsets.npy` 1,472,136 + `rideable.json` 130 |
  | 格網 | 0.01°，400 × 460 格 |

- `validate_build`：建圖流程裡通過，所以 `current` 才會切換。部署的程式對 live build 再跑一次，回傳 `[]`，0.69 s。
- 記憶體：每 30 秒取樣一次 `docker stats`，共 10 個樣本。最高 **1.996 GiB**（04:07:43；VM 上限 7.817 GiB），建完後 310.4 MiB。
  - 取樣間隔太長，可能漏掉真正的峰值（推測）。P1.5 每 3 秒取樣一次，量到 2.763 GiB。
  - 雲端估計萃取約 1.5 GB：04:07:11 的樣本是 1.208 GiB，那時萃取剛結束（紀錄時間 04:07:16）。
- `/status`：`search_ready: true`。

## 5. 新 build 上的吸附 bench 與 baseline

- `python -m engine.bench`：04:08:57–04:09:10，EXIT=0。不安全 0、無法判斷 0、不能直騎的接點 0、steps 0 m。
- md5 相同，所以路線應該和第 3 步一樣。比對結果：表格的每一欄（ms 除外）都和第 3 步完全相同。
- 更新 baseline：`--update-baseline --baseline /data/baseline-p2.json`，04:09:23–04:09:35，EXIT=0。這份檔案和第 3 步的完整結果逐欄相同（ms 除外），25 個條目都有 `"score_version": 2` 與 `legacy_*` 欄位。複製成 `engine/src/engine/bench/baseline.json`，引擎測試 986 passed 之後 commit（`1e0243b`）。
- 彙總：

  | 評分 | overall 中位數 | overall 平均 | coverage 中位數 | cleanliness 中位數 |
  | --- | --- | --- | --- | --- |
  | v1（同一批路線） | 62 | 53.6 | 73 | 54 |
  | v2 | 61 | 53.6 | 73 | 54 |

- 逐案例（路線相同，只有評分版本不同）：

| 案例 | v1 overall/coverage/cleanliness | v2 overall/coverage/cleanliness | 長度 m |
| --- | --- | --- | --- |
| `heart@kaohsiung_lingya` | 73/78/68 | 73/78/68 | 9731 |
| `heart@taichung_west` | 69/76/62 | 68/76/60 | 10489 |
| `heart@taipei_daan` | 65/75/55 | 65/75/55 | 11418 |
| `heart@taipei_daan@r30` | 66/74/57 | 66/74/57 | 11872 |
| `heart@taipei_riverside` | 24/47/0 | 24/47/0 | 18470 |
| `hi@kaohsiung_lingya` | 58/71/46 | 56/71/40 | 15562 |
| `hi@taichung_west` | 37/71/3 | 36/71/1 | 15701 |
| `hi@taipei_daan` | 58/73/42 | 56/73/38 | 15965 |
| `hi@taipei_daan@r30` | 62/79/44 | 61/79/43 | 15136 |
| `hi@taipei_riverside` | 30/59/0 | 30/59/0 | 24012 |
| `letter_l@kaohsiung_lingya` | 71/76/66 | 71/76/66 | 4343 |
| `letter_l@taichung_west` | 64/69/58 | 64/69/58 | 4574 |
| `letter_l@taipei_daan` | 34/67/0 | 34/67/0 | 7694 |
| `letter_l@taipei_daan@r30` | 44/56/31 | 42/56/27 | 6310 |
| `letter_l@taipei_riverside` | 30/0/59 | 30/0/59 | 2004 |
| `square@kaohsiung_lingya` | 59/76/42 | 56/76/37 | 12683 |
| `square@taichung_west` | 70/79/62 | 70/79/62 | 11449 |
| `square@taipei_daan` | 66/74/59 | 66/74/59 | 13300 |
| `square@taipei_daan@r30` | 69/79/59 | 69/79/59 | 13240 |
| `square@taipei_riverside` | 24/49/0 | 24/49/0 | 27184 |
| `star@kaohsiung_lingya` | 66/77/56 | 66/77/56 | 12238 |
| `star@taichung_west` | 70/76/63 | 68/76/61 | 10410 |
| `star@taipei_daan` | 63/72/54 | 63/72/54 | 11551 |
| `star@taipei_daan@r30` | 43/69/17 | 56/69/44 | 13310 |
| `star@taipei_riverside` | 26/53/0 | 26/53/0 | 21451 |

- v2 比 v1 高的只有 `star@taipei_daan@r30`：43 → 56，cleanliness 17 → 44。重複騎的部分不再重複計入。
- v2 比 v1 低的有 8 個案例，差 1–3 分，都是 cleanliness 下降：`heart@taichung_west`、`hi@kaohsiung_lingya`、`hi@taichung_west`、`hi@taipei_daan`、`hi@taipei_daan@r30`、`letter_l@taipei_daan@r30`、`square@kaohsiung_lingya`、`star@taichung_west`。
  - 推測：v1 會重複計入貼著目標來回騎的部分，把平均拉低；v2 每格只算一次，偏離的線佔比變高。

## 6. 搜尋 bench

- `python -m engine.bench.search --json /data/p2-search.json`：04:10:35–04:11:44，EXIT=0。build `20261011T040535Z`、5,777,571 條可騎路段、Router × 2。
- **12 個案例全部合格**：都 ≤ 30 s、都有 5 個候選。
  - 60 個候選的安全斷言全過，不能直騎的接點 0，樓梯 0 m，`length_ok` 全部為 true。
  - 沒有 `notice`、沒有錯誤。
- 時間：中位數 4.7 s（bench 印的 4.8 s 取的是第 7 個值），最慢 13.5 s。
  - 依圖形：default_text 2.3–5.2 s、heart 5.6–13.5 s、hi 2.6–4.8 s。
  - 冷的案例（每個地點第一個）2.3–5.2 s，沒有比熱的慢。
- 粗篩＋微調（coarse 欄）最多 1.3 s，遠低於 8 s。其餘時間都花在精算：heart 的精算最久，粗篩擺放數也最多（36 個角度）。
- 記憶體：每 2 秒啟動一次 `docker stats`，共 23 個樣本。峰值 **1.345 GiB**（VM 上限的 17%）。
- 逐案例：

| 案例 | total s | coarse s | 粗篩擺放數 | 精算次數 | 前 5 名（overall／km／起點離圓心） | Spearman |
| --- | --- | --- | --- | --- | --- | --- |
| `default_text@taipei_daan@R5`（cold） | 5.2 | 0.8 | 134,418 | 11 | 74／20.17／47 m、72／24.77／2562 m、72／23.34／2569 m、69／18.8／1130 m、62／17.66／399 m | +0.05 |
| `heart@taipei_daan@R5` | 9.3 | 1.3 | 1,234,720 | 11 | 82／15.18／2545 m、80／14.26／2162 m、80／16.62／4845 m、79／14.7／1940 m、77／15.84／1045 m | +0.39 |
| `hi@taipei_daan@R5` | 4.8 | 0.7 | 133,930 | 11 | 74／16.76／825 m、72／10.73／1632 m、70／17.45／996 m、68／17.62／140 m、62／13.95／1255 m | -0.07 |
| `default_text@taichung_west@R5`（cold） | 2.4 | 0.7 | 134,418 | 11 | 80／18.95／1588 m、79／21.62／1864 m、78／20.27／1124 m、72／20.21／88 m、72／21.78／1733 m | -0.48 |
| `heart@taichung_west@R5` | 5.6 | 1.2 | 1,234,720 | 11 | 86／17.66／2979 m、84／12.99／623 m、84／18.67／1736 m、83／13.84／2198 m、82／13.74／338 m | +0.51 |
| `hi@taichung_west@R5` | 2.7 | 0.6 | 133,930 | 12 | 69／19.04／1106 m、69／18.66／1780 m、67／19.12／1020 m、56／18.55／3583 m、43／18.9／577 m | +0.02 |
| `default_text@kaohsiung_lingya@R5`（cold） | 2.3 | 0.6 | 134,418 | 11 | 78／18.9／2123 m、75／19.47／1552 m、74／12.06／442 m、72／14.11／1295 m、71／20.82／2235 m | -0.07 |
| `heart@kaohsiung_lingya@R5` | 8.4 | 1.1 | 1,234,720 | 11 | 84／18.76／843 m、82／9.84／464 m、82／17.48／578 m、82／13.82／1498 m、82／13.75／2086 m | +0.01 |
| `hi@kaohsiung_lingya@R5` | 2.6 | 0.5 | 133,930 | 11 | 64／16.14／3670 m、52／17.71／1278 m、46／18.71／3220 m、46／11.15／4806 m、42／9.4／1073 m | -0.49 |
| `default_text@taipei_riverside@R5`（cold） | 4.1 | 0.8 | 134,418 | 11 | 76／21.78／2811 m、74／17.3／3252 m、68／16.2／3473 m、60／22.42／108 m、58／19.63／2871 m | +0.12 |
| `heart@taipei_riverside@R5` | 13.5 | 1.2 | 1,234,720 | 11 | 80／14.39／2213 m、78／13.88／3559 m、76／13.04／4951 m、74／11.11／3836 m、74／10.97／3997 m | +0.05 |
| `hi@taipei_riverside@R5` | 4.6 | 0.7 | 133,930 | 11 | 66／18.55／432 m、66／10.67／1520 m、66／19.67／2029 m、64／15.79／1702 m、60／15.03／1545 m | +0.25 |

- 精算次數 11–12，K = 10 加上公里數修正。
- Spearman（粗篩排名與精算 overall）：中位數 +0.04，範圍 −0.49 到 +0.51。粗篩前 16 名之間的排名，幾乎預測不了精算分數。
  - 推測：最慢的案例只用了 30 秒預算的 45%。把 K 調大，或把 NMS 後的 16 個全部精算，前 5 名可能更好，時間仍在預算內。這是調整的依據，不是這次的問題。
- 河濱（`taipei_riverside`）三個案例都拿到 5 個候選，沒有遇到跨河的地理限制。
- `SEARCH_ROUTERS`：12 個案例在預設 2 下全過，記憶體峰值遠低於上限的一半，所以沒有試 3、4，也沒有設定環境變數。

## 7. 部署網站

- `hl deploy artroute`：04:12:24–04:12:43，EXIT=0，仍是鎖定（`hl ls`：`private`）。`ENGINE_URL` 沒有改。
- hl 網路內的驗證（從 `artroute` 容器用 node `fetch` 呼叫 `http://artroute:8080`）：

  | 項目 | 結果 |
  | --- | --- |
  | `/api/status` | 200，內容與引擎 `/status` 逐字相同（`20261011T040535Z`、`search_ready: true`） |
  | `/` | 200，`<title>artroute｜騎車 GPS 藝術路線</title>` |
  | `/maplibre-gl-worker.mjs` | 200，`application/javascript; charset=UTF-8`，508,034 bytes |
  | `/api/match`（第 2 步的大安區 L 形） | 200，22/44/0，2,572 m，點列 md5 與第 2 步相同，`data_version` `20261011T040535Z` |
  | `/api/search` | 200，`text/event-stream; charset=utf-8`，事件逐筆到達，6.27 s 收到 `result` |
  | 中途中斷 | 引擎紀錄 `search … elapsed_ms=2470 cancelled=True`；再搜一次沒有排隊，6.34 s 收到 `result` |
  | 程序 | `next-server` 以 uid 100（容器內 `nextjs`）執行 |

- `/api/search` 送的是 spec 指定的請求：正方形、大安區、R = 3 km、6–14 km、±15°。
  - 事件依序到達：0.35 s 起是 `progress coarse` 0–6/6，接著 `refine` 0–30/30，0.54 s 開始 `match`。
  - 0.97 s 第一次 `best`（70 分、9.62 km），2.27 s 更新成 78 分、6.90 km。
  - 6.27 s 收到 `result`：5 個候選，`coarse_candidates` 56,048、`evaluated` 11、`elapsed_ms` 6,212、`notice` null。
  - 5 個候選（overall／km／起點離圓心）：78／6.90／1,033 m、70／9.62／1,556 m、68／10.48／2,223 m、62／11.48／2,113 m、60／13.33／1,929 m，公里數都在範圍內。
- 中斷：客戶端 2.5 s 後 abort（當時是 `match` 4/15）。重搜的結果和第一次完全相同（同樣的 5 個候選）。
- 規格寫的是 `curl -N`，因為容器裡沒有 curl，改用 node `fetch` 逐塊讀串流，印出每個事件到達的時間，作用相同。

## 8. 精算 16 個

PR #2 分支 `claude/p2-search-validation-jn70st`（`d69e0ca`）：粗篩交來的 16 個候選和它們的公里數修正全部精算（spec §8 第 29 列）。

### 結論

- 完成條件**仍達成**：12 個案例都 ≤ 30 s、都有 5 個候選，最慢 19.9 s（`heart@taipei_riverside`），中位數 8.3 s。
- 安全：60 個候選的安全斷言全過，不能直騎的接點 0，樓梯 0 m，公里數全部在範圍內。沒有 `notice`，沒有錯誤。
- 多精算有效：前 5 名有 18／60 個來自粗篩第 11–16 名，12 個案例裡有 10 個至少一個。前 5 名平均 overall 比 K = 10 實際部署的結果高，中位數 +2.1（0 到 +13.8）。第 1 名分數變高的有 5／12 個案例。
- 判讀：`late` 常不是 0，最慢案例 19.9 s < 20 s，符合 PR 的第二條規則。**建議下個雲端 session 評估把 `FINE_KEEP` 加到 24。** 但最慢案例離 20 s 只差 0.1 s，評估時要注意下面「判讀」一節的時間推估。
- `SEARCH_ROUTERS` 沒有案例超過 30 s，所以沒有試 3，仍是預設 2。

### 驗證與部署

- 本機 `cd engine && uv run pytest -q -W error`：**989 passed**，零警告。
- 容器測試 `docker build --target test .`：EXIT=0，**989 passed**。用 m1 上的暫存目錄，rsync 的過濾規則和 `hl deploy` 相同，跑完已刪掉暫存目錄與 image。
- `hl deploy artroute-engine --internal`：04:44:13–04:44:23，完成。沒有重建路網，`/data/current` 仍是 `builds/20261011T040535Z`。
- `/health`：`{"ok":true}`。`/status`：`build_id` `20261011T040535Z`、`search_ready: true`。`SEARCH_ROUTERS` 未設定。
- 吸附 bench 沒有跑：`/match` 的程式沒有改。

### 搜尋 bench

- `python -m engine.bench.search --json /data/p2-k16-search.json`：04:44:41–04:46:34，EXIT=0。build `20261011T040535Z`、5,777,571 條可騎路段、Router × 2。
- 時間：中位數 8.3 s（bench 印的 8.7 s 取第 7 個值），最慢 19.9 s。K = 10 時是 4.7 s／13.5 s。
  - 雲端推估最慢約 21 s、中位數約 7 s，實測都在推估附近。
- 粗篩＋微調最多 1.5 s，其餘都是精算。精算 17–25 次，K = 10 時 11–12 次。
- 記憶體：每 2 秒在背景啟動一次 `docker stats --no-stream`，不等上一次結束，共 57 個樣本。
  - 峰值 **2.145 GiB**（04:46:17，VM 上限 7.817 GiB 的 27%）。開始時 673 MiB（剛重新部署），結束時 1.05 GiB。
  - K = 10 時峰值 1.345 GiB。多出的約 0.8 GiB，推測是精算次數變多、載入的 tiles 範圍變大，沒有驗證。
- 逐案例（左邊 K = 10 是第 6 步的數字，右邊是這次）：

| 案例 | total s | coarse s | 精算次數 | late | gain（bench，下限） | 比 K = 10 實際 | Spearman |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `default_text@taipei_daan@R5`（cold） | 5.2 → 8.0 | 0.8 → 0.8 | 11 → 21 | 2 | +3.2 | +3.2 | +0.05 → −0.07 |
| `heart@taipei_daan@R5` | 9.3 → 17.8 | 1.3 → 1.2 | 11 → 17 | 1 | +0.4 | +0.4 | +0.39 → +0.16 |
| `hi@taipei_daan@R5` | 4.8 → 8.7 | 0.7 → 0.7 | 11 → 21 | 1 | +1.6 | +1.6 | −0.07 → +0.12 |
| `default_text@taichung_west@R5`（cold） | 2.4 → 3.8 | 0.7 → 0.7 | 11 → 21 | 2 | +2.0 | +2.0 | −0.48 → −0.19 |
| `heart@taichung_west@R5` | 5.6 → 8.7 | 1.2 → 1.2 | 11 → 20 | 0 | +0.0 | 0.0 | +0.51 → +0.64 |
| `hi@taichung_west@R5` | 2.7 → 5.5 | 0.6 → 0.6 | 12 → 24 | 2 | +3.2 | **+8.2** | +0.02 → −0.21 |
| `default_text@kaohsiung_lingya@R5`（cold） | 2.3 → 4.0 | 0.6 → 0.6 | 11 → 20 | 2 | +1.2 | +1.2 | −0.07 → +0.01 |
| `heart@kaohsiung_lingya@R5` | 8.4 → 10.8 | 1.1 → 1.1 | 11 → 18 | 4 | +2.2 | +2.2 | +0.01 → −0.16 |
| `hi@kaohsiung_lingya@R5` | 2.6 → 6.1 | 0.5 → 0.5 | 11 → 25 | 1 | +3.6 | **+13.8** | −0.49 → +0.05 |
| `default_text@taipei_riverside@R5`（cold） | 4.1 → 7.5 | 0.8 → 0.9 | 11 → 21 | 2 | +4.2 | **+6.6** | +0.12 → −0.10 |
| `heart@taipei_riverside@R5` | 13.5 → 19.9 | 1.2 → 1.5 | 11 → 17 | 0 | +0.0 | 0.0 | +0.05 → +0.67 |
| `hi@taipei_riverside@R5` | 4.6 → 9.2 | 0.7 → 0.7 | 11 → 22 | 1 | +1.8 | **+3.0** | +0.25 → −0.10 |

- 「比 K = 10 實際」：這次前 5 名的平均 overall，減掉第 6 步實際部署 K = 10 時前 5 名的平均。
- bench 的 `gain` 是下限，實測也是這樣：8 個案例的「只精算前 10 名」和 K = 10 實際部署的前 5 名分數完全相同。另外 4 個（粗體）實際部署的分數更低，因為舊的停止條件跑完 11–12 次（含公里數修正）就停，前 10 名的候選和公里數修正沒有全部跑到。
- bench 的彙總行：`前 5 名來自粗篩第 11 名以後：18/60；前 5 名平均 overall 比只精算前 10 名高（中位數）+2.0；第 1 名分數變高 4/12 案例；只精算前 10 名會少於 5 個候選 0 案例`。和實際部署比，第 1 名變高的是 5／12，多了 `hi@kaohsiung_lingya`（64 → 76）。
- Spearman：中位數 −0.03，範圍 −0.21 到 +0.67。K = 10 時是 +0.04（−0.49 到 +0.51）。
  - 前 5 名的粗篩名次分布很平均：名次 0–9 有 42 個（每名 1–6 個），名次 10–15 有 18 個（每名 1–4 個）。
- 前 5 名（overall／km／起點離圓心）：

| 案例 | 前 5 名 |
| --- | --- |
| `default_text@taipei_daan@R5` | 77／24.74／493 m、74／20.17／47 m、72／24.77／2562 m、72／23.34／2569 m、70／23.55／114 m |
| `heart@taipei_daan@R5` | 82／15.18／2545 m、80／14.26／2162 m、80／16.62／4845 m、79／14.29／574 m、79／14.7／1940 m |
| `hi@taipei_daan@R5` | 74／16.76／825 m、72／10.73／1632 m、70／13.7／458 m、70／17.45／996 m、68／17.62／140 m |
| `default_text@taichung_west@R5` | 80／18.95／1588 m、79／21.62／1864 m、78／20.27／1124 m、78／23.8／4229 m、76／22.87／2343 m |
| `heart@taichung_west@R5` | 86／17.66／2979 m、84／12.99／623 m、84／18.67／1736 m、83／13.84／2198 m、82／13.74／338 m |
| `hi@taichung_west@R5` | 72／18.42／987 m、69／19.04／1106 m、69／18.66／1780 m、68／18.18／2907 m、67／19.12／1020 m |
| `default_text@kaohsiung_lingya@R5` | 78／18.9／2123 m、75／14.92／1211 m、75／19.47／1552 m、74／12.06／442 m、74／19.35／781 m |
| `heart@kaohsiung_lingya@R5` | 86／17.53／173 m、85／12.51／1433 m、84／13.38／14 m、84／18.76／843 m、84／14.64／964 m |
| `hi@kaohsiung_lingya@R5` | 76／12.83／555 m、64／11.4／2362 m、64／16.14／3670 m、63／19.38／4278 m、52／17.71／1278 m |
| `default_text@taipei_riverside@R5` | 76／21.78／2811 m、76／17.06／2815 m、74／17.3／3252 m、73／23.06／770 m、70／19.94／1284 m |
| `heart@taipei_riverside@R5` | 80／14.39／2213 m、78／13.88／3559 m、76／13.04／4951 m、74／11.11／3836 m、74／10.97／3997 m |
| `hi@taipei_riverside@R5` | 73／11.25／2138 m、66／18.55／432 m、66／10.67／1520 m、66／19.67／2029 m、66／15.13／3975 m |

### 判讀（PR #2 的規則；只寫文件，沒有改程式）

- 第一條（`late` 全是 0 而且 `gain` 中位數 ≤ 0 → 退回 K = 10）：不成立。`late` 18／60，`gain` 中位數 +2.0。
- 第二條（`late` 常不是 0 而且最慢案例 < 20 s → 評估 `FINE_KEEP` 24）：成立。10／12 個案例的 `late` 不是 0，最慢 19.9 s。
- 建議下個雲端 session 評估 24 時一併考慮：
  - 支持加大：名次 10–15 進前 5 名的比例（18／60 = 30%），和它們在 16 個裡佔的比例（6／16 = 38%）差不多。粗篩排名在前 16 名內幾乎沒有資訊。名次 16–23 會不會一樣好沒有量過（第二次 NMS 之後的排名，推測相近）。
  - 時間：heart 的精算每次約 1.9–2.2 s（每個 Router）。24 個加上公里數修正約 25 次，`heart@taipei_riverside` 推估約 1.5 + 18.5 × 25／17 ≈ 29 s（推測），會被 25 秒的軟性預算截斷。所以對大圖形來說，24 實際上等於「跑到 25 秒」。網站的等待時間中位數推估會從 8.3 s 再變長。
  - 替代方案：`SEARCH_ROUTERS=3`。記憶體峰值 2.145 GiB，佔 VM 的 27%；m1 有 8 個 CPU。精算時間推估可以少約 1／3，但沒有量過，而且要先量 3 個 Router 的記憶體。

## 沒通過或待決定的事項

- 沒有未通過的項目。第 1 步的測試失敗已經修正（`dc48f8a`），雲端 session 已確認（2026-10-11，雲端也通過）。
- 給雲端 session 調整用的觀察（不阻擋合併）：
  - Spearman 很低，時間預算還很寬，可以考慮調大 K。→ 已改成 16 個全部精算（第 8 節）。下一步是評估 `FINE_KEEP` 24，見第 8 節的判讀。
  - 建圖的記憶體峰值要用更密的取樣再量一次。
- 手動事項：使用者要在 https://artroute.fiir.fyi 用自己的帳號實際試一次自動搜尋：設定範圍 → 搜尋 → 套用候選 → 調整 → 下載 GPX → 匯入 Strava。
