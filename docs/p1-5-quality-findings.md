# P1.5 吸附品質：量測與部署

未標註來源的數字是實測值，來源是 m1 上的 `artroute-engine` 容器與本機；檢查點 A 與 Task 14 的量測在 build `20261010T183449Z` 上，部署之後的量測在新 build 上（見「部署」）。原型數字、計畫預測另外標示；推測一律標「推測」。

名詞：
- 原型：品質調查（`docs/p1-quality-investigation.md`）的推薦組合 C＋R035＋S＋V＋BR，其中 C = 分治改成串接、R035 = 搜尋半徑比例 0.035、S = 繞路／補橋／connector 取最短、V = 開放筆畫可以反向走、BR = 補橋嚴格落點加裁切重試。「原型」的數字取自該報告 §4.3 那次官方 bench 執行的逐案例輸出（原始輸出沒有收進 repo，見該報告 §7）。
- 計畫：`docs/superpowers/plans/2026-10-11-p1-5-quality.md`；「計畫預測」指各 Task 寫的預期 bench 變化。
- A1、A3：驗收規則與對第一次不通過的決定，定義見「閘門檢查」開頭。A6（人行道採 (a)）見「樓梯與人行道」最後；A7（組裝修正）見「預設文字（「I ♥ TAIPEI」）與決策 A7」。

## 檢查點 A（不部署；各階段程式用 PYTHONPATH 載入）

### 量測方法

- 每個階段取該 task 的最後一個 commit，`git archive <sha> engine/src/engine` 解到容器的 `/tmp/p15-<sha>`，用 `PYTHONPATH` 蓋過容器裡的程式（`/app` 與 `current` 路網資料不動，沒有部署、沒有更新 baseline、沒有動 `current`）。每個階段先確認 `service.py` 的路徑是 `/tmp/p15-<sha>/engine/matching/service.py`、`RADIUS_RATIO` 在 T2–T5 是 0.02、T6 起是 0.035，全部符合。
- 階段與 SHA：

  | 階段 | SHA | 內容 |
  | --- | --- | --- |
  | T2 | `f4dc49f` | 引擎行為與 committed baseline 相同，bench 多量 footway／steps／parking_aisle |
  | T3 | `166c946` | 串接錨點改成最長的部件，兩端往外接 |
  | T4 | `529aab9` | 封閉筆畫斷成多條 chain 時照筆畫順序接回（含 Task 3 的三個小修） |
  | T5 | `3de75ed` | 繞路、橋接、connector 取 bicycle 最短路徑（S），建圖驗證兩種設定都跑 |
  | T6 | `e890194` | 預設搜尋半徑 0.02 → 0.035（R035） |
  | T7 | `8327c82` | 分治改成串接（C） |
  | T7+fix | `ece472e` | T7 + 錨點改取 stroke 長度最長的部件，繞路與 connector 不算（決策 A3 的修正，T7 之後的修正 commit；本文件初版 `831ee1a` 量的是修正前的 T7） |

- bench：`python -m engine.bench`（不帶 `--update-baseline`，與該 commit 的 committed `baseline.json` 比較），25 案例，每個地點先預熱一次，**ms 是單次取樣**。
- 預設文字：網站的「I ♥ TAIPEI」（futural、中心 121.5654, 25.033、2000 m），**15 筆畫、68 點、1 個封閉**；`timing.py` 用同一個 Router 連跑 6 次，第 1 次預熱不計，取後 5 次的中位數，並計 Valhalla `trace`／`route` 呼叫次數。量測時引擎容器照常運作、沒有刻意停止服務；當時有沒有其他請求沒有紀錄。
- 量測前與清理後各查一次 `/status`（T7+fix 也各查一次），`build_id` 都是 `20261010T183449Z`。

### 各階段彙總

| 階段 | 成功 | overall 中位數 | overall 平均 | coverage 中位數 | cleanliness 中位數 | length/target 中位數 | ms 中位數／最大 | footway／steps／parking_aisle m |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| T2 | 25/25 | 46 | 48.1 | 74 | 43 | 1.90 | 231 / 504 | 34447／53／2807 |
| T3 | 25/25 | 46 | 47.3 | 74 | 38 | 2.02 | 237 / 503 | 38470／53／2807 |
| T4 | 25/25 | 46 | 47.3 | 74 | 38 | 2.02 | 229 / 510 | 38470／53／2807 |
| T5 | 25/25 | 54 | 47.4 | 74 | 39 | 2.00 | 228 / 505 | 33251／256／4001 |
| T6 | 25/25 | 61 | 52.2 | 74 | 46 | 1.76 | 294 / 1117 | 30578／185／1019 |
| T7 | 25/25 | 60 | 52.4 | 73 | 46 | 1.71 | 284 / 1121 | 30423／316／1428 |
| T7+fix | 25/25 | 60 | 53.6 | 73 | 54 | 1.71 | 282 / 1118 | 30423／316／1428 |

預設文字請求（單一 Router、6 次取後 5 次）：

| 階段 | median ms | max ms | trace／route 呼叫 | segments | score（overall/coverage/cleanliness） | 長度 m | 警告 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| T2 | 247 | 253 | 77／36 | 49 | 53/69/37 | 16145 | stroke 12 `no_route` |
| T3 | 451 | 457 | 77／61 | 51 | 55/70/40 | 18720 | 無 |
| T4 | 462 | 469 | 77／61 | 51 | 55/70/40 | 18720 | 無 |
| T5 | 297 | 304 | 77／41 | 51 | 56/71/41 | 18732 | 無 |
| T6 | 298 | 305 | 39／31 | 44 | 57/71/43 | 19439 | 無 |
| T7 | 291 | 304 | 39／31 | 45 | 58/72/43 | 19365 | 無 |
| T7+fix | 298 | 298 | 39／31 | 45 | 58/72/43 | 19365 | 無 |

- T7 另外重量三次：median 298、290、293 ms（max 300、291、299）；連同上表的 291，四次 median 290–298 ms。
- T7+fix 另外重量三次：median 297、291、290 ms（max 305、298、291）；連同上表的 298，四次 median 290–298 ms，路線與分數和 T7 完全相同（trace／route 呼叫數也相同）。
- 對 300 ms 預算的餘裕：兩個階段的四次 median 都是 0.7%–3.3%（最緊的一次 median 298，只差 2 ms）；T5 起的單次最大值最高 305 ms（T5 304、T6 305、T7 304、T7+fix 305 都有單次超過 300 ms 的紀錄，閘門看的是 median；T3、T4 的單次最大值 457／469 ms 見上表）。
- T3、T4 的預設文字 median 451／462 ms 高於預算：T3 的「接不上就試另一端」讓 route 呼叫從 36 增為 61；T5 的 `shortest`（61 → 41）與 T6 的半徑（trace 77 → 39）把它壓回約 300 ms。預算的閘門只看 T7。
- bench 最慢案例：T2–T5 503–510 ms（T2、T3、T4、T5 依序 504、503、510、505；皆為 `square@taipei_daan@r30`），T6 1117 ms、T7 1121 ms、T7+fix 1118 ms（同一案例）。原型同組合 1140 ms。
- `star@taipei_daan@r30`（Task 3 失敗路徑重試的觀察對象）：T2 361、T3 377、T4 392、T5 388、T6 595、T7 605、T7+fix 595 ms；原型同組合 608 ms。

### 逐案例 overall/coverage

| 案例 | baseline | T2 | T3 | T4 | T5 | T6 | T7 | T7+fix |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `heart@kaohsiung_lingya` | 72/80 | 72/80 | 72/80 | 72/80 | 73/80 | 73/78 | 73/78 | 73/78 |
| `heart@taichung_west` | 68/76 | 68/76 | 68/76 | 68/76 | 65/75 | 69/76 | 69/76 | 69/76 |
| `heart@taipei_daan` | 64/75 | 64/75 | 64/75 | 64/75 | 64/75 | 66/75 | 65/75 | 65/75 |
| `heart@taipei_daan@r30` | 52/58 | 52/58 | 52/58 | 52/58 | 54/57 | 66/74 | 66/74 | 66/74 |
| `heart@taipei_riverside` | 20/41 | 20/41 | 20/41 | 20/41 | 12/23 | 18/36 | 24/47 | 24/47 |
| `hi@kaohsiung_lingya` | 38/75 | 38/75 | 38/75 | 38/75 | 37/74 | 58/71 | 58/71 | 58/71 |
| `hi@taichung_west` | 40/70 | 40/70 | 40/70 | 40/70 | 42/70 | 37/71 | 37/71 | 37/71 |
| `hi@taipei_daan` | 46/75 | 46/75 | 46/75 | 46/75 | 57/75 | 56/72 | 58/73 | 58/73 |
| `hi@taipei_daan@r30` | 62/84 | 62/84 | 62/84 | 62/84 | 58/83 | 60/81 | 60/80 | 60/80 |
| `hi@taipei_riverside` | 16/31 | 16/31 | 29/58 | 29/58 | 29/58 | 30/60 | 30/60 | 30/60 |
| `letter_l@kaohsiung_lingya` | 66/80 | 66/80 | 66/80 | 66/80 | 66/80 | 71/76 | 71/76 | 71/76 |
| `letter_l@taichung_west` | 46/48 | 46/48 | 46/48 | 46/48 | 46/48 | 66/73 | 64/69 | 64/69 |
| `letter_l@taipei_daan` | 44/0 | 44/0 | 37/74 | 37/74 | 37/74 | 34/67 | 34/67 | 34/67 |
| `letter_l@taipei_daan@r30` | 42/55 | 42/55 | 42/55 | 42/55 | 40/55 | 43/56 | 44/56 | 44/56 |
| `letter_l@taipei_riverside` | 30/0 | 30/0 | 0/0 | 0/0 | 0/0 | 0/0 | 0/0 | 30/0 |
| `square@kaohsiung_lingya` | 39/78 | 39/78 | 39/78 | 39/78 | 39/78 | 61/76 | 59/76 | 59/76 |
| `square@taichung_west` | 70/81 | 70/81 | 70/81 | 70/81 | 72/82 | 70/79 | 70/79 | 70/79 |
| `square@taipei_daan` | 56/74 | 56/74 | 56/74 | 56/74 | 62/75 | 66/74 | 66/74 | 66/74 |
| `square@taipei_daan@r30` | 72/83 | 72/83 | 72/83 | 72/83 | 73/83 | 70/80 | 69/79 | 69/79 |
| `square@taipei_riverside` | 20/40 | 20/40 | 20/40 | 20/40 | 16/32 | 22/43 | 24/48 | 24/48 |
| `star@kaohsiung_lingya` | 64/74 | 64/74 | 64/74 | 64/74 | 64/74 | 66/77 | 66/77 | 66/77 |
| `star@taichung_west` | 58/76 | 58/76 | 58/76 | 58/76 | 60/76 | 70/76 | 70/76 | 70/76 |
| `star@taipei_daan` | 61/78 | 61/78 | 61/78 | 61/78 | 60/77 | 64/74 | 63/72 | 63/72 |
| `star@taipei_daan@r30` | 34/0 | 34/0 | 39/66 | 39/66 | 38/64 | 44/70 | 43/69 | 43/69 |
| `star@taipei_riverside` | 22/43 | 22/43 | 22/43 | 22/43 | 22/43 | 26/52 | 26/53 | 26/53 |

### 相對 committed baseline 的下降（T7，錨點修正前）

共 14 條（`table.py` 對 T7 的輸出）。「超過容忍值 3」「容忍值內」是量測腳本 `table.py` 加的標籤，門檻與 bench compare 的容忍值相同（overall 或 coverage 下降 > 3 才印「退步：」），不是閘門；閘門用 A1（coverage 容忍到 8），見「閘門檢查」。

- T7 `heart@kaohsiung_lingya` coverage 80 → 78（容忍值內）
- T7 `hi@kaohsiung_lingya` coverage 75 → 71（超過容忍值 3）
- T7 `hi@taichung_west` overall 40 → 37（容忍值內）
- T7 `hi@taipei_daan` coverage 75 → 73（容忍值內）
- T7 `hi@taipei_daan@r30` overall 62 → 60（容忍值內）
- T7 `hi@taipei_daan@r30` coverage 84 → 80（超過容忍值 3）
- T7 `letter_l@kaohsiung_lingya` coverage 80 → 76（超過容忍值 3）
- T7 `letter_l@taipei_daan` overall 44 → 34（超過容忍值 3）
- T7 `letter_l@taipei_riverside` overall 30 → 0（超過容忍值 3；T3 起的退步，T7+fix 已修正）
- T7 `square@kaohsiung_lingya` coverage 78 → 76（容忍值內）
- T7 `square@taichung_west` coverage 81 → 79（容忍值內）
- T7 `square@taipei_daan@r30` overall 72 → 69（容忍值內）
- T7 `square@taipei_daan@r30` coverage 83 → 79（超過容忍值 3）
- T7 `star@taipei_daan` coverage 78 → 72（超過容忍值 3）

逐項說明（「原型」「計畫預測」見開頭的名詞）：

- 計畫已預告的 R035／S／C 取捨（T6 起出現；star、letter_l@kaohsiung_lingya、square@taipei_daan@r30 與原型完全相同，hi 與原型的差異來自 V，見下）：
  - `star@taipei_daan` coverage 78 → 72：T6 先降到 74、T7 再降到 72；overall 61 → 63。原型 63/72，完全相同。
  - `letter_l@kaohsiung_lingya` coverage 80 → 76（T6）；overall 66 → 71。原型相同。
  - `square@taipei_daan@r30` overall 72 → 69、coverage 83 → 79（T6 70/80、T7 69/79）。原型相同。
  - `hi@taipei_daan@r30` overall 62 → 60、coverage 84 → 80（T5 58/83、T6 60/81、T7 60/80）。原型 62/79；Task 9（V）預測可回到 62。
  - `hi@kaohsiung_lingya` coverage 75 → 71（T6）；overall 38 → 58。原型 62/72；計畫預測 Task 9（V）之後 overall 58 → 62、coverage 71 → 72。
- 計畫沒有預告的 overall 小降（在容忍值內）：`hi@taichung_west` overall 40 → 37（T6；T5 為 42/70）、coverage 70 → 71。原型（含 V）45/72；計畫只預測 Task 9（V）之後 overall 回到 45，沒有把 T6／T7 的這個下降列進 Task 7 的預告清單。
- 原型約略相同、計畫沒有預告 coverage 的小降（在容忍值內）：`hi@taipei_daan` coverage 75 → 73（T6 72、T7 73；原型 74）、overall 46 → 58（原型 52，計畫預測 V 之後 58 → 52）。
- 原型也有、計畫沒有逐案列出的小降（皆在容忍值內，T6 起）：`heart@kaohsiung_lingya` coverage 80 → 78（原型 73/78，相同）、`square@kaohsiung_lingya` coverage 78 → 76 與 `square@taichung_west` coverage 81 → 79（原型相同）。
- **原型沒有、計畫也沒預告的退步（T3 造成）**：
  - `letter_l@taipei_riverside` overall 30 → 0：T3 起 0/0/0（T2 30/0/59，長度 2000 m → 4136 m）；T7 長度 3527 m，仍是 0/0/0。原型 30/0/59、2004 m。
  - `letter_l@taipei_daan` overall 44 → 34：T3 起 37/74/0（T2 44/0/87，長度 1711 m → 8334 m，coverage 0 → 74 但 cleanliness 87 → 0），T6 再降 3 分成 34/67/0；T7 長度 7694 m。原型 44/66/22、4672 m。
  - 原因見下方「letter_l 兩個案例」。

### 相對 committed baseline 的下降（T7+fix，最終）

共 13 條（`table.py` 對 T7+fix 的輸出）。bench 的 `EXIT=1`：compare 印出 6 條「退步：」，就是下列標「超過容忍值 3」的 6 條（hi@kaohsiung_lingya coverage、hi@taipei_daan@r30 coverage、letter_l@kaohsiung_lingya coverage、letter_l@taipei_daan overall、square@taipei_daan@r30 coverage、star@taipei_daan coverage）。這 6 條裡 5 條是 coverage 下降 4–6（≤ A1 的 8），1 條是 `letter_l@taipei_daan` overall -10（A3 例外），所以閘門仍通過。

- T7+fix `heart@kaohsiung_lingya` coverage 80 → 78（容忍值內）
- T7+fix `hi@kaohsiung_lingya` coverage 75 → 71（超過容忍值 3）
- T7+fix `hi@taichung_west` overall 40 → 37（容忍值內）
- T7+fix `hi@taipei_daan` coverage 75 → 73（容忍值內）
- T7+fix `hi@taipei_daan@r30` overall 62 → 60（容忍值內）
- T7+fix `hi@taipei_daan@r30` coverage 84 → 80（超過容忍值 3）
- T7+fix `letter_l@kaohsiung_lingya` coverage 80 → 76（超過容忍值 3）
- T7+fix `letter_l@taipei_daan` overall 44 → 34（超過容忍值 3）
- T7+fix `square@kaohsiung_lingya` coverage 78 → 76（容忍值內）
- T7+fix `square@taichung_west` coverage 81 → 79（容忍值內）
- T7+fix `square@taipei_daan@r30` overall 72 → 69（容忍值內）
- T7+fix `square@taipei_daan@r30` coverage 83 → 79（超過容忍值 3）
- T7+fix `star@taipei_daan` coverage 78 → 72（超過容忍值 3）

與 T7（錨點修正前）相比，T7+fix 只有一個案例的輸出不同，其餘 24 個案例的 overall、coverage、cleanliness、長度完全相同：`letter_l@taipei_riverside` 0/0/0、3527 m → 30/0/59、2004 m，與 committed baseline 的 overall、coverage、cleanliness 相同（30/0/59），與原型（30/0/59、2004 m）完全相同。長度 2004 m 對 baseline 的 2000 m 有 4 m 差：T2 的 part0 開頭與結尾是 detour（kinds 為 detour…detour，2000 m），T7+fix 的 part0 只有兩段 stroke（2004 m）；兩者是不同的路線，分數相同。整體 overall 平均 52.4 → 53.6、cleanliness 中位數 46 → 54，中位數維持 60。

T7 清單 14 條，T7+fix 清單 13 條：少的一條是 `letter_l@taipei_riverside`，其餘 13 條與 T7 相同，說明見 T7 小節的「逐項說明」。`letter_l@taipei_daan` overall 44 → 34 是決策 A3 明確接受的例外（coverage 0 → 67，補出完整的 L；根因見「letter_l 兩個案例」）。

### 與原型逐案例對照（T7 對原型；T7+fix 的變化見表後）

25 案例中 18 個與原型的 overall／coverage／cleanliness／長度完全相同（heart 5 個、square 5 個、star 5 個（包括 `star@taipei_daan@r30` 43/69/17、13310 m）、`letter_l@taichung_west`、`letter_l@kaohsiung_lingya`、`letter_l@taipei_daan@r30`）。不同的 7 個：

| 案例 | 原型 | T7 | Δoverall | 原因 |
| --- | --- | --- | --- | --- |
| `hi@kaohsiung_lingya` | 62/72/53，14007 m | 58/71/46，15562 m | -4 | 原型含 V（開放筆畫反向，Task 9 才加） |
| `hi@taichung_west` | 45/72/18，14250 m | 37/71/3，15701 m | -8 | 同上；計畫 Task 9 預測 37 → 45 |
| `hi@taipei_daan` | 52/74/29，15758 m | 58/73/42，15965 m | +6 | 同上；計畫預測 V 之後 58 → 52 |
| `hi@taipei_daan@r30` | 62/79/44，15136 m | 60/80/39，16913 m | -2 | 同上 |
| `hi@taipei_riverside` | 32/63/0，22127 m | 30/60/0，23885 m | -2 | 同上 |
| `letter_l@taipei_daan` | 44/66/22，4672 m | 34/67/0，7694 m | -10 | 見下 |
| `letter_l@taipei_riverside` | 30/0/59，2004 m | 0/0/0，3527 m | -30 | 見下 |

hi 的 5 列是 Task 9（V）尚未加入造成的差異（計畫已預告）；`letter_l` 的 2 列不在預告內。T7+fix 之後 `letter_l@taipei_riverside` 回到 30/0/59、2004 m，與原型相同；與原型相同的案例變成 19 個，不同的剩 6 個（`hi` 5 個＋`letter_l@taipei_daan`）。原型 25 案例 overall 平均 54.4、中位數 62；T7 平均 52.4、中位數 60；T7+fix 平均 53.6、中位數 60。

### letter_l 兩個案例（T3 的後果；決策 A3 與 T7+fix）

`letter_l` 是單一開放筆畫（3 個點：向下再向右），吸附後斷成兩個部件：part0 = 垂直那段（由上往下）、part1 = 水平那段（由西往東）。用 `composition.dropped` 與部件端點量到：

- 兩個案例在 T2 都是 `dropped=[1]`：串接器丟掉 part1，輸出只剩 part0（`letter_l@taipei_daan` 1711 m、cleanliness 87、coverage 0；`letter_l@taipei_riverside` 2000 m、cleanliness 59、coverage 0）。原型也是這樣（`letter_l@taipei_riverside` 30/0/59）。
- T3 的錨點改成最長的部件（以路線長度計，含繞路）：兩個案例的錨點都變成 part1（`letter_l@taipei_daan` 3414 m 對 part0 的 1711 m；`letter_l@taipei_riverside` 4136 m 對 part0 的 2000 m）。
  - `letter_l@taipei_daan`：part0 排在錨點前面，但 part0 的終點 → part1 的起點這個接點接不上（兩點相距 14.7 m，`try_route` 回 None；反方向的 route 有 603 m，推測是單行道）。T3 的「原定那一端接不上就試另一端」就把 part0 接在 part1 的**尾巴**。開放部件不能反向（Task 9 的 V 只處理「只吸出一條 chain 且沒有警告」的開放筆畫，`letter_l` 斷成兩條 chain，不符合），所以輸出是「水平段 → 一條回頭的 connector → 垂直段」。connector 在 T3 是 3209 m（兩端直線距離 2346 m，離畫線最遠 610 m），T7 是 3027 m（離畫線最遠 896 m）。
  - `letter_l@taipei_riverside`：part0 的終點 → part1 的起點相距 202.6 m，兩個方向的 route 都回 None，兩端都接不上，part0 被丟掉（`dropped=[0]`，T3、T7 相同），輸出只剩變成錨點的 part1。part1 含一條 2088 m 的繞路（T7：兩端直線距離 127 m，離畫線最遠 913 m），所以 coverage 0、cleanliness 0。舊版保留的是 part0（2000 m，貼著畫線，cleanliness 59），錨點換成「路線比較長」的 part1 之後，這個較好的部件反而被丟掉。
- 結果：`letter_l@taipei_daan` coverage 從 0 變 74／67（整個 L 都畫出來了），但 cleanliness 87 → 0、overall 下降；`letter_l@taipei_riverside` 完全沒有分。T3 的驗收預期是「可能一起改善」，實際 overall 變差。
- 初版結論：不是移植錯誤（與原型逐案例相同的其他 18 個案例證明移植吻合），是 T3 的設計（接另一端）在「開放部件不能反向」時的副作用，原型沒有 T3，所以原型沒有這個退步。初版（T7，`831ee1a`）當時的處理：不調參數、不改 compare 容忍值，把是否調整 T3 的做法交給決策；決策結果見下一段 A3。

決策 A3 與 T7+fix（`ece472e`）：A3 把兩個案例分開處理——`letter_l@taipei_riverside` 當成缺陷修掉，`letter_l@taipei_daan` 當成明確接受的例外。
- `letter_l@taipei_riverside` 是真缺陷：錨點用總路線長度挑，被繞路灌大（part1 有 2088 m 的繞路），乾淨的 part0（stroke 2004 m）被擠掉。修正：錨點取 stroke 類 segment 的長度總和最長的部件（detour／connector 不算），平手規則不變。量到的 stroke 長度：part0 2004 m（T7+fix 的 parts 紀錄：兩段 stroke，2004 m）、part1 973 m（取自 T7 的逐 segment 輸出：detour 兩段合計 2554 m（上面提到的 2088 m 加一段 466 m）、stroke 973 m；T7+fix 的 parts 紀錄只有各部件總長與 segment 種類，part1 的內容與 T7 相同）。錨點回到 part0，part1 被丟（`dropped=[1]`），輸出回到 30/0/59、2004 m。
- `letter_l@taipei_daan` 接受為 A1 的明確例外：baseline 44 的 coverage 是 0（只畫了片段），現在畫出完整的 L（coverage 67），代價是一條 3027 m 的 connector。原因：part0 終點 → part1 起點 14.7 m 的缺口，正向 route 無解、反向有 603 m（推測是單行道，沒有查道路屬性）。T7+fix 輸出與 T7 完全相同（34/67/0、7694 m；這個案例的 part1 stroke 長度 1675 m 小於 part0 的 1711 m，錨點換成 part0，結果仍是水平段 → connector → 垂直段）。根本的做法是把斷成多個部件的開放筆畫整條反向（把 V 擴到多部件筆畫），放 P2 backlog。這個案例的 overall 最多可降 10。

### 其他

- validate_build（T5 `3de75ed`）：`validate_build: []`。
- T3 與 T2 不同的案例共 4 個：`hi@taipei_riverside`（16/31 → 29/58，長度 32353 → 37840 m）、`letter_l@taipei_daan`（44/0 → 37/74）、`letter_l@taipei_riverside`（30/0 → 0/0）、`star@taipei_daan@r30`（34/0 → 39/66，2617 → 15318 m）。其餘 21 個案例 overall、coverage、cleanliness、長度與 T2 相同。T2 的 `composition.dropped` 這四個案例都有丟部件（`letter_l` ×2 丟 part1、`star@taipei_daan@r30` 丟 part1、`hi@taipei_riverside` 丟 part4）。計畫（Task 8 驗收）要看的「無法與其他部分相連」警告文字在 T2 看不到：`_merge_warnings` 對同一個筆畫只留先到的吸附階段警告（「部分路段找不到可騎的道路，已略過」，code `partial`），所以改用 `composition.dropped` 確認，四個都成立。
- T2 與 committed baseline：25 案例 overall、coverage、cleanliness、長度、target 完全相同，bench `EXIT=0` 無「退步」行。
- T3 → T4：25 案例的 overall、coverage、長度完全相同。原因：`_stitch_closed` 在 15 個 heart／star／square 案例裡，T7 全部只收到 1 條 chain（不會接 connector）；T4 只有 `star@taipei_daan@r30` 收到 2 條 chain，也沒接上（2 條進、2 條出、沒有 connector）。所以 bench 裡**沒有任何案例觀察得到 Task 4 縫合造成的切角捷徑**（Task 4 review 提出的觀察項），那條路徑只有單元測試覆蓋。T7 輸出裡 heart／star／square／letter_l 唯一的 connector 是 `letter_l@taipei_daan` 的 3027 m（compose 產生，不是縫合），沒有 heart／star／square 的 connector。
- 原路折返（out-and-back）量測（Task 7 review 提出的觀察項）：把 route 上對稱重合（≤ 2 m）的 A→B→A 當作折返，取 T6 與 T7 對照（單位：折返總長 m，來回合計）。以下數字都是 T7 修正前量的；`letter_l@taipei_riverside` 的 T7 輸出是已被 A3 修掉的 0/0/0，T7+fix 的輸出不同，沒有重量：
  - `letter_l`：daan 150 → 150、taichung_west 662 → 414、kaohsiung_lingya 0 → 0、riverside 164 → 98（T7 修正前的輸出）、daan@r30 386 → 258。T7 沒有新增，反而減少。
  - `star`：taipei_daan 546 → 678（新增一個 66 m 單程的 detour 折返，位置 121.54019, 25.03573）、taichung_west 384 → 384、kaohsiung_lingya 986 → 642、riverside 2128 → 2128、daan@r30 2768 → 2688。
  - 其他增加的：`square@taipei_riverside` 1916 → 3666（新增兩個 detour 折返：單程 384 m 在 121.51898, 25.07773，緊接在一個既有的 365 m stroke 折返之後；單程 438 m 在 121.50292, 25.07671，後面接一個既有的 454 m stroke 折返）、`heart@taipei_riverside` 996 → 1470。這兩個案例的 overall／coverage 在 T7 反而上升（square 22/43 → 24/48、heart 18/36 → 24/47）。
  - `star@taipei_daan@r30` 有一個單程 1141 m 的折返（121.53432, 25.02949，在 detour 裡），T3 就有（T3 總折返 3376 m），不是 T7 造成；T6／T7 都在。
  - 折返偵測是量測腳本（對稱重合 ≤ 2 m），不是引擎的檢查；小於 40 m 單程的沒有逐項列出。
- 安全斷言：7 個階段（T2–T7、T7+fix）全部「不安全 0、無法判斷 0」。bench 的 `EXIT`：T2 為 0；T3 起因 compare 的「退步」行為 1（T7+fix 的 6 條見上）。

### 閘門檢查

A1 是驗收規則：安全斷言全過（不安全 0、無法判斷 0）；每個案例相對 committed baseline 的 overall 下降 ≤ 3、coverage 下降 ≤ 8；整體 overall 中位數與平均都高於 baseline；`star@taipei_daan@r30` coverage ≥ 50；延遲在預算內（預設文字 median ≤ 300 ms、bench 最慢 ≤ 2500 ms）。A3 是對第一次不通過的決定：`letter_l@taipei_riverside` 是真缺陷，改錨點選法修掉（改取 stroke 長度最長的部件，`ece472e`）；`letter_l@taipei_daan` 的 overall 下降（44 → 34）明確接受為 A1 的例外（最多可降 10），理由是 baseline 的 coverage 0 只畫了片段，現在畫出完整的 L（coverage 67）。

閘門分兩次：初次用 T7 判定，不通過；A3 之後，以 T7+fix 重新判定。A1 對每個案例適用，只有 `letter_l@taipei_daan` 依 A3 例外。

| 條件 | T7（初次） | T7+fix（最終） |
| --- | --- | --- |
| 1. 每個階段安全斷言（不安全 0、無法判斷 0） | 通過（T2–T7） | 通過（T2–T7、T7+fix 共 7 個階段） |
| 2. T2 與 committed baseline 的 overall／coverage／length 相同 | 通過 | 通過（T2 未變） |
| 3. T3：`star@taipei_daan@r30` coverage ≥ 50；與 T2 不同的案例 T2 都有丟部件 | 通過（coverage 66；4 個案例 T2 都有 dropped） | 同左（T3 數字不變） |
| 4. T4：與 T3 不同的案例只能是封閉筆畫、沒有超過容忍值的下降 | 通過（沒有任何案例不同） | 同左 |
| 5. T5 `validate_build` 回 `[]` | 通過 | 同左 |
| 6. 每案例 overall 下降 ≤ 3、coverage 下降 ≤ 8 | 不通過：`letter_l@taipei_riverside` -30、`letter_l@taipei_daan` -10 | 通過：`letter_l@taipei_riverside` 30（無下降）；`letter_l@taipei_daan` -10 依 A3 例外；其餘 overall 下降最多 3（`hi@taichung_west`、`square@taipei_daan@r30`）、coverage 下降最多 6（`star@taipei_daan`） |
| 7. 整體 overall 中位數與平均都高於 baseline | 通過（60 對 46、52.4 對 48.1） | 通過（60 對 46、53.6 對 48.1） |
| 8. `star@taipei_daan@r30` coverage ≥ 50 | 通過（69） | 通過（69） |
| 9. 預設文字 median ≤ 300 ms；bench 最慢 ≤ 2500 ms | 通過（median 290–298 ms；最慢 1121 ms） | 通過（四次 median 298、297、291、290 ms；最慢 1118 ms，`square@taipei_daan@r30`） |

T7+fix 下降清單的 13 條（11 個案例）全部被接受，分三類（對照原型與計畫預測，見前面各節）：

- A3 例外（與原型不同、計畫沒有預告）：`letter_l@taipei_daan` overall 44 → 34（coverage 0 → 67）。
- 計畫 Task 7 已預告的 5 個 coverage 退步（`star@taipei_daan`、`letter_l@kaohsiung_lingya`、`square@taipei_daan@r30` 與原型相同；兩個 `hi` 的 coverage 與原型差 1，來自尚未加入的 V）：`star@taipei_daan` coverage 78 → 72、`letter_l@kaohsiung_lingya` coverage 80 → 76、`hi@kaohsiung_lingya` coverage 75 → 71、`square@taipei_daan@r30` coverage 83 → 79（overall 72 → 69）、`hi@taipei_daan@r30` coverage 84 → 80（overall 62 → 60）。
- 容忍值內的小降（不在計畫的 Task 7 預告清單內；`heart@kaohsiung_lingya`、`square@kaohsiung_lingya`、`square@taichung_west` 與原型相同，`hi@taichung_west` overall 37 是計畫 Task 9 預測會改善的值，`hi@taipei_daan` coverage 73 對原型 74）：`heart@kaohsiung_lingya` coverage 80 → 78、`hi@taichung_west` overall 40 → 37、`hi@taipei_daan` coverage 75 → 73、`square@kaohsiung_lingya` coverage 78 → 76、`square@taichung_west` coverage 81 → 79。

閘門結論：通過（T7+fix `ece472e`：安全斷言全過；letter_l@taipei_riverside 回到 30/0/59；letter_l@taipei_daan overall 44→34 依 A3 例外；其餘案例 overall 下降 ≤ 3、coverage 下降 ≤ 8；overall 中位數 46→60、平均 48.1→53.6；star@taipei_daan@r30 coverage 69；預設文字 median 290–298 ms、bench 最慢 1118 ms）

## 樓梯與人行道（Task 14）

使用者決定（2026-10-11）：「禁走樓梯」「人行道盡量避免」。

### 做了什麼

- 樓梯：建圖前所有 `highway=steps` 標成 `bicycle=no`，比照快速道路移除會重新放行的 tag（`bicycle:*`、`cycleway*`、`oneway:bicycle`、`vehicle:forward/backward`，另加 `service=driveway`，見下）。沒有例外：`bicycle=yes`＋`ramp:bicycle=yes`（有牽引道的樓梯）也禁行，`allow` 清單也不例外。被標記的樓梯寫進禁行清單（加 `highway` 欄位），bench 的安全斷言因此也涵蓋樓梯。
- `validate_build` 加樓梯檢查：被標記的樓梯至少一條；依 way id 等距抽 20 條，兩端各往外延伸 15 m 當起訖、兩個方向各查一次（預設與 shortest 兩種設定），路線不得經過任何被禁行的 way，且至少一半的路線要配得出路段（修補後的真實 build 上兩種設定都是 34/40）。
- 人行道：自行車**本來就能走**的 `footway`／`pedestrian`（含 sidewalk、crossing）標成 `bicycle=dismount`；本來不能走的維持不能走。判斷「本來就能走」：`bicycle` 是 Valhalla lua 視為可通行的值（yes、designated、permissive、use_sidepath……），或沒有 `bicycle` 時 `cycleway`／`cycleway:both` 放行（`cycleway=opposite*` 只放行逆向，標 dismount 會連順向打開，所以不算；台灣資料 0 條受影響）。`path`、`cycleway` 不動。

### 標記數量（m1，`/data/osm/taiwan-latest.osm.pbf`，與 build `20261010T183449Z` 同一份）

| 類別 | 條數 |
| --- | --- |
| 樓梯 → `bicycle=no` | 16,359 |
| 快速道路 → `bicycle=no`（不變，供對照） | 2,399 |
| 禁行清單合計 | 18,758 |
| footway → dismount（不含 sidewalk、crossing） | 1,631 |
| footway=sidewalk → dismount | 1,810 |
| footway=crossing → dismount | 1,429 |
| pedestrian → dismount | 124 |
| footway／pedestrian 不動（自行車本來就不能走） | 120,429 |

### Valhalla 3.9.1 的實測行為（合成地圖，本機 pyvalhalla 3.9.1；lua 取自 `valhalla_build_tiles` 內嵌的 graph.lua）

- **footway／pedestrian（含 `footway=sidewalk`）沒有自行車 tag 時，自行車本來就不能走**（lua 的 highway 表 `bike_forward=false`；route 實測無解）。bench 量到的人行道公尺數全部來自明確允許自行車的人行道：base 的 30,580 m 裡，`footway=sidewalk`＋`bicycle=yes` 21,097 m、`footway=sidewalk`＋`bicycle=designated` 5,135 m、其他 `bicycle=yes`／`designated`／`use_sidepath`／`cycleway=shared` 等約 4,300 m；沒有任何允許 tag 的只有 3 m（推測是 edge_walk 配錯）。
- **`bicycle=dismount` 會放行本來不能走的人行道**（lua 的 bicycle 表 dismount＝true）。所以「沒有明確 bicycle=yes／designated／permissive 就標 dismount」這條規則（計畫原文）會讓沒有 `bicycle` tag 的 113,820 條 footway、5,013 條 pedestrian（除了約百條靠 cycleway 放行的，本來都不通）與 `bicycle=no` 的 1,653 條變成可騎，而真正被走的人行道（都有明確允許）一條也不會被避開。實作改成只標本來就能走的。
- dismount 在**預設成本**下以牽車速度計價：經人行道 0.8 km（其中人行道 0.4 km）對經道路 1.25 km 會改走道路（測試釘住）；但 0.2 km 的人行道對 1.1 km、2.0 km 的道路繞路（5–10 倍）仍走人行道，所以只是「有稍長的道路就繞開」。
- dismount 對 **shortest** 沒有作用：成本只看長度，照樣走人行道（測試釘住，升級 pyvalhalla 時會提醒）。
- dismount 對 **map_snap（trace）** 沒有作用：平行人行道相距 10 m／20 m、畫線落在兩者之間的各種位置，`bicycle=dismount` 與 `bicycle=yes` 吸附到的路完全相同。
- 樓梯：預設成本本來就重罰（0.2 km 樓梯對 2.4 km 繞路仍繞路），shortest 會直接走；route 的起訖不會吸附到樓梯上（只有樓梯可走時連未修補都無解），所以樓梯檢查的起訖要往外延伸。
- `service=driveway`（沒有 `access`）會蓋過 trunk 的 `bicycle=no`（lua 讓所有交通方式通行，實測重新可騎）；台灣快速道路目前 0 條帶這個 tag，屬防護。樓梯帶這個 tag 實測不會重開，一樣移除。
- edge_walk 遇到兩條 way 共用同一對端點時會配錯 way（2 點的捷徑被配成同端點的 4 點道路），合成地圖已避開這種配置。

### 吸附的人行道公尺數從哪來（base，量測腳本以 edge.use 分 segment 種類加總）

| segment 種類 | footway m | pedestrian_crossing m | steps m |
| --- | --- | --- | --- |
| stroke（trace 吸附） | 23,418 | 4,130 | 96 |
| detour | 4,108 | 417 | 97 |
| connector | 3,054 | 343 | 114 |

76% 的人行道公尺數來自 trace 吸附，而 trace 不看 dismount；繞路、橋接、connector 的成本設定最多只能影響其餘約 7.2 km。

### bench 對照（m1，scratch build，不啟用）

量測方法：用分支程式（`920e561` 的 `engine/src/engine`，PYTHONPATH 載入 `/tmp/p15-t14-*`）在 `artroute-engine` 容器對同一份 pbf 建 scratch build 到 `/data/p15-scratch/`（121 s），只把 scratch 目錄自己的 `current` 指過去，正式 `/data/current` 不動，量完刪除。base＝`0ad47e6`（Task 14 之前）在正式 build `20261010T183449Z` 上。各變體：

- (a)：資料修補＋繞路／橋接／connector 維持 shortest（分支現況）。
- (b)：資料修補＋繞路／橋接／connector 改回預設 bicycle 成本（接點轉彎的 rideable 探針仍用 shortest）。
- (b2)：同 (b)，預設成本加 `use_roads=1.0`（計畫提到的「調 use_roads」）。
- (S)：參考用，不在計畫的 (a)(b) 內：`footway=sidewalk` 且自行車本來能走的改標 `bicycle=no`（禁行），其他同 (a)。另一份 scratch build `/data/p15-scratch-s/`。

| 變體 | footway m（bench 合計） | steps m | overall 中位數／平均 | A1（對 committed baseline） | 最大 overall 降幅（對 committed） | 預設文字 median ms（4 次） | bench 最慢 ms |
| --- | --- | --- | --- | --- | --- | --- | --- |
| committed baseline | — | — | 46／48.1 | — | — | — | — |
| base（`0ad47e6`） | 30,580 | 307 | 62／53.6 | 通過 | letter_l@taipei_daan −10（A3） | 291–299 | 1134 |
| (a) | 30,766 | 0 | 62／53.6 | 通過 | 同 base | 303–309 | 1114 |
| (b) | 26,801（−12.4%） | 0 | 59／52.4 | **不通過**：square@taipei_daan@r30 72→67、hi@taipei_daan@r30 62→58 | −5 | 310–315 | 1128 |
| (b2) | 26,301（−14.0%） | 0 | 59／51.8 | **不通過**：hi@taipei_daan 46→36、hi@taipei_daan@r30 62→58 | −10 | 306–312 | 1123 |
| (S) | 4,167（−86.4%） | 0 | 58／51.0 | **不通過**：square@taipei_daan@r30 72→55、letter_l@taipei_daan 44→33、letter_l@taipei_daan@r30 42→32、heart@taipei_daan 64→58；coverage −9 兩案 | −17 | 319–331 | 1045 |

- 安全斷言：5 個變體全部 25/25 成功、不安全 0、無法判斷 0（禁行清單含 18,758 條，樓梯包含在內）。
- (a) 對 base：25 案例 overall 完全相同；coverage 只有 `hi@taipei_riverside` 60→59、`square@taipei_riverside` 48→49；樓梯 307 m → 0（`heart@taipei_riverside`、`square@taipei_riverside`、`hi@taipei_riverside` 改走別條路，前者 footway 911 → 1,326 m）。footway 多 186 m：trace 部分 23,418 → 23,833 m（樓梯禁行後改道），detour／connector 略降。
- (b) 的 footway 下降全部來自 connector（2,937 → 385 m）與 detour（3,996 → 2,583 m）；trace 部分 23,833 m 不變。對 base 另有 `hi@kaohsiung_lingya` 58→36（committed baseline 是 38，所以 A1 沒抓到）。
- 預設文字（「I ♥ TAIPEI」）：base／(a) 48/72/23、16,884 m；(b) 44/71/18；(b2) 40/71/8；(S) 37/69/5。(a) 的 median 比 base 多約 4%，程式的吸附部分與 base 相同（只有 `data/` 不同），差異來自路網（樓梯禁行改道）或 tile 快取狀態，推測；在 +10% 內。
- 洲美街／洲美快速道路／樓梯檢查：scratch build 上 `validate_build` 回 `[]`（0.7 s）。樓梯檢查的非空洞性：用 scratch 的禁行清單在未修補的正式 build 上跑，預設成本 0 項（預設成本本來就繞開樓梯）、shortest 抓到 4 項（20 條抽樣裡 3 條的路線經過樓梯，例如 way 607070536 兩個方向都走它）。

### 決定

計畫的規則：人行道公尺數明顯下降，且符合 A1，且預設文字中位數與最慢案例不劣於 290–298 ms／1118 ms 超過 10%；(a)(b) 都符合選人行道較少者，都不符合就停下交由決策。

- (a)：人行道沒有下降（30,580 → 30,766 m），不符合。
- (b)：人行道 −12.4%，但 A1 不通過（兩案 overall 降 4–5），不符合；(b2) 同樣不通過。

兩者都不符合，**沒有選定**，分支維持 (a)（樓梯禁行已生效、人行道 dismount 只影響預設成本的 route）。根本原因：Valhalla 3.9.1 的 dismount 只有預設成本會看，而吸附流程 76% 的人行道來自 trace（map_snap 不看成本）、其餘是 shortest（也不看）。要讓吸附真的避開人行道，只剩「禁行」這條路（S：−86%），但 S 在大安區的案例 overall 最多降 17、預設文字慢約 10%，而且違反「盡量避免」而非「禁止」的字面，需要使用者決定。

決策 A6（之後）：採 (a)。沒有允許 tag 的人行道本來就不會被走；bench 走到的人行道幾乎都明確允許自行車（合法的共用人行道）。要再減少只能靠禁行，而禁行會降分，也不符合「盡量避免」（不是「禁止」）。`920e561` 的 dismount 保留（無害；之後若繞路改回預設成本就會生效）。使用者仍可改選禁行（S）。

## 部署（Task 13）

時間都是 UTC。

- 引擎第一次部署：`5c2e8df`（Task 1–12、14 與 web W1 合併後）。`hl deploy artroute-engine --internal` 從 2026-10-10T23:49:14Z 到 23:49:21Z，EXIT=0。容器測試 `docker build --target test` 826 passed，與本機相同。`/health` 回 `{"ok":true}`，2 個 uvicorn worker。
- 正式重建（Task 14 的樓梯、人行道修補要新的 tiles）：在容器內 detached 跑 `python -m engine.data.cli`。
  - 時間：2026-10-10T23:50:40Z 到 23:53:45Z，共 3 m 05 s，EXIT=0。下載約 63 s；標記、tiles、驗證約 2 m。
  - 新 build：**`20261010T235143Z`**，`/status` 回這個 id。
  - pbf 的 md5 是 `28cc8b1b689b45b640b5a8c5d2042b79`，與舊 build `20261010T183449Z` 是同一份（Geofabrik 沒有更新）。所以新舊 build 的差別只有修補規則。
  - `layout.prune(keep=2)` 照設計刪掉 `20261010T181406Z`；回滾版 `20261010T183449Z` 保留。
  - tiles：261 個 .gph，346,980 KiB（舊 build 344,768 KiB）。
  - 記憶體：每約 3 s 取樣一次，峰值 2.763 GiB（VM 上限 7.817 GiB），建完後 178.9 MiB。
- 標記數：新 build 的禁行清單＋同一份 pbf 的計數，與 Task 14 的 scratch build 逐項相同。

  | 類別 | 條數 |
  | --- | --- |
  | 樓梯 → `bicycle=no` | 16,359 |
  | 快速道路 → `bicycle=no`（trunk 2,033、trunk_link 366） | 2,399 |
  | 禁行清單合計 | 18,758 |
  | 人行道 → dismount（footway 1,631、sidewalk 1,810、crossing 1,429、pedestrian 124） | 4,994 |
  | footway／pedestrian 不動 | 120,429 |

- `validate_build`（部署的程式＋live build）：`[]`，0.7 s。兩次部署後都量過。
- 引擎第二次部署：`b8dc6ec`，決策 A7 的組裝修正，見下面「預設文字」。
  - 2026-10-11T00:05:14Z 到 00:05:21Z，EXIT=0。
  - 容器測試 828 passed，與本機相同。2 個 worker。
  - 不重建：`current` 仍是 `20261010T235143Z`。
- 網站：`8258ea9` 的 `web/`（也就是 W1 合併後的版本），`hl deploy artroute` 從 2026-10-11T00:07:45Z 到 00:08:01Z，EXIT=0，仍是鎖定（只有本人能看）。在 hl 網路內驗證：
  - `/api/status` 200，內容與引擎 `/status` 相同（build `20261010T235143Z`）。
  - 首頁 200（`<title>artroute｜騎車 GPS 藝術路線</title>`）。
  - `/maplibre-gl-worker.mjs` 200（`application/javascript`，508,314 bytes）。
  - `/api/match`：大安區 L 形樣本回 200，overall/coverage/cleanliness 65/71/59，路線 2,189 m，`data_version` `20261010T235143Z`。
  - 程序以 `nextjs`（uid 100）執行。

## 部署前後的階段量測（新 build；V、轉彎檢查、組裝修正）

量法與檢查點 A 相同：`git archive` 解到容器，用 `PYTHONPATH` 載入。差別是這次在新 build `20261010T235143Z` 上量。live 用部署的 `/app`。ms 是單次取樣。

| 階段 | 程式 | 內容 |
| --- | --- | --- |
| T7+fix | `ece472e` | 檢查點 A 的最終程式（對照用） |
| T9 | `3564e0c` | 開放筆畫可以反向走（V，含整筆只 trace 一次的修正） |
| T11 | `593ef4a` | 接點轉彎檢查（Task 10 與三輪修正）＋bench 的轉彎計數（Task 11）；Task 14 之前最後一個引擎 commit |
| 修正前 live | `5c2e8df` | 吸附程式與 `593ef4a` 相同，多了 Task 14 的建圖程式 |
| live | `b8dc6ec` | ＋決策 A7 的組裝修正 |

| 階段 | 成功 | overall 中位數 | overall 平均 | coverage 中位數 | cleanliness 中位數 | length/target 中位數 | ms 中位數／最大 | footway／steps／parking_aisle m |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| T7+fix（舊 build，檢查點 A） | 25/25 | 60 | 53.6 | 73 | 54 | 1.71 | 282 / 1118 | 30423／316／1428 |
| T7+fix | 25/25 | 60 | 53.6 | 73 | 54 | 1.71 | 286 / 1120 | 30624／0／1292 |
| T9 | 25/25 | 62 | 53.6 | 73 | 54 | 1.71 | 289 / 1139 | 30766／0／1292 |
| T11 | 25/25 | 62 | 53.6 | 73 | 54 | 1.71 | 291 / 1148 | 30766／0／1292 |
| 修正前 live | 25/25 | 62 | 53.6 | 73 | 54 | 1.71 | 284 / 1131 | 30766／0／1292 |
| live | 25/25 | 62 | 53.6 | 73 | 54 | 1.71 | 288 / 1132 | 30766／0／1292 |

- 新 build 對舊 build（同為 `ece472e`）：只有三個 riverside 案例改道，原因是樓梯禁行：
  - `hi@taipei_riverside` 30/60 → 30/59。
  - `square@taipei_riverside` 24/48 → 24/49。
  - `heart@taipei_riverside` 分數不變，長度 18,406 → 18,470 m。
  - 樓梯 316 m → 0。
- T9（V）對 T7+fix：bench 只有 `hi@taipei_daan@r30` 改變，60/80/39、16,933 m → 62/79/44、15,136 m，與原型完全相同。
  - 計畫預測 V 也會改善 `hi@kaohsiung_lingya`（58 → 62）、`hi@taichung_west`（37 → 45），並讓 `hi@taipei_daan` 58 → 52；三個都沒有出現，維持 58／37／58。
  - 推測原因：正式版只讓「只吸出一條 chain、沒有警告」的開放筆畫反向，而且反向整筆只 trace 一次（決策 A5），比原型保守。沒有逐案驗證。
- T11 對 T9、兩個 live 對 T11：25 案例的分數與長度完全相同。
  - 轉彎檢查在 bench 上沒有改變任何輸出。
  - A7 的修正在 bench 上也沒有改變任何輸出（bench 裡沒有「出得去、進不來」的部件）。
- 安全：每個階段都是不安全 0、無法判斷 0。轉彎不能直接騎的接點：T11、修正前 live、live 都是 0（更早的程式沒有這項計數）。新 build 上每個案例的樓梯都是 0 m。

### 預設文字（「I ♥ TAIPEI」）與決策 A7

| 程式（新 build） | score | 長度 m | connector（段，m） | trace／route | median ms |
| --- | --- | --- | --- | --- | --- |
| T7+fix `ece472e` | 58/72/43 | 19,365 | 14，10,026 | 39／31 | 308 |
| T9 `3564e0c` | 48/72/23 | 16,884 | 12，7,572 | 45／29 | 299 |
| T11 `593ef4a` | 48/72/23 | 16,884 | 12，7,572 | 45／32 | 310 |
| 修正前 live `5c2e8df` | 48/72/23 | 16,884 | 12，7,572 | 45／32 | 301、309、300、305 |
| live `b8dc6ec` | **56/72/39** | 16,049 | 12，6,737 | 45／28 | 264、261、257、264 |

**cleanliness 的下降發生在 T9**。T10／T11 的輸出逐 segment 與 T9 相同，只多 3 次 route（轉彎探針）；轉彎檢查沒有打斷任何 chain。

V 本身是改善：
- 反向 5 個部件，都是乾淨的單段 stroke。另有 1 個部件的反向 trace 失敗，照畫的方向走。
- 中段的 connector 從 4,407 m 減到 2,261 m，路線短 13%，coverage 不變。

分數下降來自組裝的缺陷，T3 起就有，T7+fix 也有：
- `_assemble` 在原定那一端接不上時，立刻改接另一端。
- 筆畫 12 是一段 106 m 的吸附結果，落在 way 138265875（`use=ramp`）。route 都進不去：往它的 route 終點都停在 6.6–7.5 m 外的另一條路，裁切重試 4 次都失敗；但從它出發的 route 正常。
- 它排在最後，接尾端失敗，就被搬到開頭（1,686 m connector），佔掉規劃中排第一的「I」的位置。
- 「I」接開頭也得進它，同樣失敗，只好橫越整張圖接到尾巴。
- T7+fix 時這條 connector 是 2,426 m，平均離畫線 56 m。T9 的反向讓路線的結尾移了位置，它變成 2,118 m，平均離畫線 108 m、最遠 232 m。
- 結果路線點到畫線的距離：p90 77 m → 111 m，平均 38.7 m → 45.6 m，cleanliness 43 → 23。

**決策 A7**：修組裝，不採「接不上的小部件直接丟」（會少畫）。

修法（`b8dc6ec`）：
- 原定那一端接不上的部件先擱著，等其他部件都照規劃接完再補。
- 補的時候，原定那一端換了部件才再試一次，再試另一端；都不行才丟。
- 所有接點照樣經過 `join.connect`，安全不變式與連續性檢查不變。

修正後：
- 「I」照規劃接在心形前面（667 m）；只有筆畫 12 在最前面多一條 2,302 m 的 connector（平均離畫線 55 m）。
- 結果 56/72/39、16,049 m，p90 84 m。route 呼叫 32 → 28，median 約 260 ms。
- 單元測試以「出得去、進不來」的假 router 重現：舊版的順序是 3, 1, 2, 0，新版是 3, 0, 1, 2。

## 部署後：與 committed baseline 對照

committed baseline 是 `5c2e8df` 的 `baseline.json`（舊 build，P1 最終版）；「live」是 `b8dc6ec` 在新 build 上。

| 案例 | baseline | T7+fix（舊 build） | T7+fix | T9 | T11 | live |
| --- | --- | --- | --- | --- | --- | --- |
| `heart@kaohsiung_lingya` | 72/80 | 73/78 | 73/78 | 73/78 | 73/78 | 73/78 |
| `heart@taichung_west` | 68/76 | 69/76 | 69/76 | 69/76 | 69/76 | 69/76 |
| `heart@taipei_daan` | 64/75 | 65/75 | 65/75 | 65/75 | 65/75 | 65/75 |
| `heart@taipei_daan@r30` | 52/58 | 66/74 | 66/74 | 66/74 | 66/74 | 66/74 |
| `heart@taipei_riverside` | 20/41 | 24/47 | 24/47 | 24/47 | 24/47 | 24/47 |
| `hi@kaohsiung_lingya` | 38/75 | 58/71 | 58/71 | 58/71 | 58/71 | 58/71 |
| `hi@taichung_west` | 40/70 | 37/71 | 37/71 | 37/71 | 37/71 | 37/71 |
| `hi@taipei_daan` | 46/75 | 58/73 | 58/73 | 58/73 | 58/73 | 58/73 |
| `hi@taipei_daan@r30` | 62/84 | 60/80 | 60/80 | 62/79 | 62/79 | 62/79 |
| `hi@taipei_riverside` | 16/31 | 30/60 | 30/59 | 30/59 | 30/59 | 30/59 |
| `letter_l@kaohsiung_lingya` | 66/80 | 71/76 | 71/76 | 71/76 | 71/76 | 71/76 |
| `letter_l@taichung_west` | 46/48 | 64/69 | 64/69 | 64/69 | 64/69 | 64/69 |
| `letter_l@taipei_daan` | 44/0 | 34/67 | 34/67 | 34/67 | 34/67 | 34/67 |
| `letter_l@taipei_daan@r30` | 42/55 | 44/56 | 44/56 | 44/56 | 44/56 | 44/56 |
| `letter_l@taipei_riverside` | 30/0 | 30/0 | 30/0 | 30/0 | 30/0 | 30/0 |
| `square@kaohsiung_lingya` | 39/78 | 59/76 | 59/76 | 59/76 | 59/76 | 59/76 |
| `square@taichung_west` | 70/81 | 70/79 | 70/79 | 70/79 | 70/79 | 70/79 |
| `square@taipei_daan` | 56/74 | 66/74 | 66/74 | 66/74 | 66/74 | 66/74 |
| `square@taipei_daan@r30` | 72/83 | 69/79 | 69/79 | 69/79 | 69/79 | 69/79 |
| `square@taipei_riverside` | 20/40 | 24/48 | 24/49 | 24/49 | 24/49 | 24/49 |
| `star@kaohsiung_lingya` | 64/74 | 66/77 | 66/77 | 66/77 | 66/77 | 66/77 |
| `star@taichung_west` | 58/76 | 70/76 | 70/76 | 70/76 | 70/76 | 70/76 |
| `star@taipei_daan` | 61/78 | 63/72 | 63/72 | 63/72 | 63/72 | 63/72 |
| `star@taipei_daan@r30` | 34/0 | 43/69 | 43/69 | 43/69 | 43/69 | 43/69 |
| `star@taipei_riverside` | 22/43 | 26/53 | 26/53 | 26/53 | 26/53 | 26/53 |

- `validate_build`：`[]`。
- 安全：不安全 0、無法判斷 0、轉彎不能直接騎的接點 0、樓梯 0 m。
- 整體 overall：中位數 46 → 62，平均 48.1 → 53.6（53.64）。`star@taipei_daan@r30` 的 coverage 69。
- bench 的 `EXIT=1`：compare 印出 6 條「退步：」。說明見下面「baseline」。

閘門（A1，加上 A3 的例外）：通過。

| 條件 | 結果 |
| --- | --- |
| 安全斷言（含轉彎、樓梯） | 0／0／0，樓梯 0 m |
| 每案例 overall 下降 ≤ 3、coverage 下降 ≤ 8 | overall 最多 −3（`hi@taichung_west`、`square@taipei_daan@r30`）、coverage 最多 −6（`star@taipei_daan`）；`letter_l@taipei_daan` −10 依 A3 例外 |
| 整體 overall 中位數與平均都高於 baseline | 62 對 46、53.6 對 48.1 |
| `star@taipei_daan@r30` coverage ≥ 50 | 69 |
| 延遲（預設文字 median ≤ 400 ms 才繼續、最慢案例 ≤ 2500 ms） | 257–264 ms；1132 ms |

## footway／steps／parking_aisle（只量測）

| 案例 | T2 footway／steps | live footway／steps |
| --- | --- | --- |
| `heart@taipei_daan` | 2013／0 | 2248／0 |
| `heart@taichung_west` | 0／0 | 0／0 |
| `heart@kaohsiung_lingya` | 142／0 | 142／0 |
| `heart@taipei_riverside` | 927／12 | 1326／0 |
| `star@taipei_daan` | 2591／0 | 1977／0 |
| `star@taichung_west` | 0／0 | 0／0 |
| `star@kaohsiung_lingya` | 575／0 | 394／0 |
| `star@taipei_riverside` | 993／0 | 947／0 |
| `letter_l@taipei_daan` | 46／0 | 1027／0 |
| `letter_l@taichung_west` | 0／0 | 0／0 |
| `letter_l@kaohsiung_lingya` | 274／0 | 224／0 |
| `letter_l@taipei_riverside` | 0／0 | 0／0 |
| `square@taipei_daan` | 4563／0 | 4314／0 |
| `square@taichung_west` | 0／0 | 0／0 |
| `square@kaohsiung_lingya` | 1539／0 | 845／0 |
| `square@taipei_riverside` | 1255／16 | 813／0 |
| `hi@taipei_daan` | 3557／0 | 3282／0 |
| `hi@taichung_west` | 0／0 | 0／0 |
| `hi@kaohsiung_lingya` | 938／0 | 136／0 |
| `hi@taipei_riverside` | 2429／16 | 966／0 |
| `heart@taipei_daan@r30` | 2908／0 | 2686／0 |
| `star@taipei_daan@r30` | 608／0 | 1897／0 |
| `letter_l@taipei_daan@r30` | 1744／0 | 1177／0 |
| `square@taipei_daan@r30` | 3637／0 | 2839／0 |
| `hi@taipei_daan@r30` | 3708／9 | 3526／0 |
| 合計 | 34,447／53 | 30,766／0 |

parking_aisle 合計：T2 2,807 m，live 1,292 m。

這些數字是 edge_walk 的整條 edge 長度加總，近似值（分段點會重複算）。人行道的政策照決策 A6。

## 延遲

預設文字：單一 Router，每次跑 6 回、第 1 回不計，取 median。

| 量測 | median ms | max ms | trace／route |
| --- | --- | --- | --- |
| 修前（T2，舊 build） | 247 | 253 | 77／36 |
| T7+fix（舊 build），4 次 | 290–298 | — | 39／31 |
| 新 build：T7+fix | 308 | 314 | 39／31 |
| 新 build：T9 | 299 | 303 | 45／29 |
| 新 build：T11 | 310 | 316 | 45／32 |
| 修正前 live，4 次 | 301、309、300、305 | 315、328、310、310 | 45／32 |
| live，4 次 | 264、261、257、264 | 267、265、264、264 | 45／28 |

- live 比修前多約 10–17 ms，比修正前 live 少約 40–45 ms：route 少了 4 次，而且不再有兩條橫越整張圖的 connector。
- V 讓 trace 從 39 增為 45。
- bench：ms 中位數 288，最慢 `square@taipei_daan@r30` 1,132 ms（≤ 2500 ms，原型同組合 1,140 ms）。
- W1 之後拖曳中最多一筆在途，300 ms 不再是硬門檻。

## baseline

已更新（`8258ea9`）：在 live（`b8dc6ec`，新 build）上跑 `python -m engine.bench --update-baseline --force`。

- 25 案例全部成功，沒有任何案例需要 `--force` 放行。
- 候選 baseline 的 overall／coverage／cleanliness 與同一程式的 bench 逐案例相同。
- 新的 `baseline.json` 多了 `use_m`（footway／steps／parking_aisle 公尺數）與 `bad_turns` 兩個欄位。

對舊 baseline 接受的 12 條下降（11 個案例）：

- 依 A3 例外：`letter_l@taipei_daan` overall 44 → 34。這次在新 build 上 coverage 0 → 67，畫出完整的 L；代價是一條 3,027 m 的 connector（推測是單行道造成，見「letter_l 兩個案例」）。
- 計畫 Task 7 已預告的 R035／S／C 取捨（coverage 下降 4–6，≤ 8）：
  - `star@taipei_daan` coverage 78 → 72
  - `hi@taipei_daan@r30` coverage 84 → 79（overall 62 → 62，T9 的 V 讓 overall 回到 baseline）
  - `hi@kaohsiung_lingya` coverage 75 → 71
  - `letter_l@kaohsiung_lingya` coverage 80 → 76
  - `square@taipei_daan@r30` coverage 83 → 79
- 容忍值內的小降：
  - `square@taipei_daan@r30` overall 72 → 69
  - `hi@taichung_west` overall 40 → 37
  - `heart@kaohsiung_lingya` coverage 80 → 78
  - `hi@taipei_daan` coverage 75 → 73
  - `square@kaohsiung_lingya` coverage 78 → 76
  - `square@taichung_west` coverage 81 → 79

與檢查點 A（T7+fix）的清單相比，少了 `hi@taipei_daan@r30` 的 overall 下降，因為 V 把它拉回 62。

## 待決定與後續（P2）

- 人行道類路段是否禁止：決策 A6 採 (a)，量測見上；使用者可以改選禁行（S）。
- 立即折返與繞圈移除（品質調查 G／L）、評分調整、跨水域警告：`docs/backlog.md`。
- `letter_l@taipei_daan`：把斷成多個部件的開放筆畫整條反向（把 V 擴到多部件筆畫，A3），`docs/backlog.md`。
- 吸附得到、但 route 進不去的路段（預設文字的筆畫 12，ramp）：A7 之後它只能排在路線最前面，仍多一條 2,302 m 的 connector。可能的做法是裁掉進不去的那段，或讓反向重新吸附避開它，要另外量測。
- V 只改變了一個 bench 案例，計畫預測的另外三個 hi 案例沒有改變（推測見「部署前後的階段量測」）。
- edge_walk 在兩條 way 共用同一對端點時會配錯 way（Task 14），bench 的用途公尺數可能有少量誤差。
