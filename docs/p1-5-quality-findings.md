# P1.5 吸附品質：量測與部署

未標註來源的數字是實測值，來源是 m1 上的 `artroute-engine` 容器（build `20261010T183449Z`）與本機。原型數字、計畫預測另外標示；推測一律標「推測」。

名詞：
- 原型：品質調查（`.superpowers/sdd/2026-10-11-p1-engine/quality-investigation-report.md`）的推薦組合 C＋R035＋S＋V＋BR，其中 C = 分治改成串接、R035 = 搜尋半徑比例 0.035、S = 繞路／補橋／connector 取最短、V = 開放筆畫可以反向走、BR = 補橋嚴格落點加裁切重試。「原型」的數字取自該報告 §4.3 在官方 bench 上的輸出（`official_combo`）。
- 計畫：`docs/superpowers/plans/2026-10-11-p1-5-quality.md`；「計畫預測」指各 Task 寫的預期 bench 變化。
- A1、A3：驗收規則與對第一次不通過的決定，定義見「閘門檢查」開頭。

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
- 對 300 ms 預算的餘裕：兩個階段的四次 median 都是 0.7%–3.3%（最緊的一次 median 298，只差 2 ms）；單次最大值最高 305 ms（T5 304、T6 305、T7 304、T7+fix 305 都有單次超過 300 ms 的紀錄，閘門看的是 median）。
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

共 14 條（`table.py` 對 T7 的輸出）。標「超過容忍值 3」是 bench compare 的標籤（overall 或 coverage 下降 > 3），不是閘門；閘門用 A1（coverage 容忍到 8），見「閘門檢查」。

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

- 計畫已預告的 R035／S／C 取捨（T6 起出現；star、letter_l@kaohsiung_lingya、square@taipei_daan@r30 與原型完全相同，hi 的差異來自 V，見下）：
  - `star@taipei_daan` coverage 78 → 72：T6 先降到 74、T7 再降到 72；overall 61 → 63。原型 63/72，完全相同。
  - `letter_l@kaohsiung_lingya` coverage 80 → 76（T6）；overall 66 → 71。原型相同。
  - `square@taipei_daan@r30` overall 72 → 69、coverage 83 → 79（T6 70/80、T7 69/79）。原型相同。
  - `hi@taipei_daan@r30` overall 62 → 60、coverage 84 → 80（T5 58/83、T6 60/81、T7 60/80）。原型 62/79；Task 9（V）預測可回到 62。
  - `hi@kaohsiung_lingya` coverage 75 → 71（T6）；overall 38 → 58。原型 62/72；計畫預測 Task 9（V）之後 overall 58 → 62、coverage 71 → 72。
  - `hi@taichung_west` overall 40 → 37（T6；T5 為 42/70）、coverage 70 → 71。計畫預測 Task 9 之後 45。
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
- `letter_l@taipei_riverside` 是真缺陷：錨點用總路線長度挑，被繞路灌大（part1 有 2088 m 的繞路），乾淨的 part0（stroke 2004 m）被擠掉。修正：錨點取 stroke 類 segment 的長度總和最長的部件（detour／connector 不算），平手規則不變。量到的 stroke 長度：part0 2004 m（T7+fix 的 parts 紀錄：兩段 stroke，2004 m）、part1 973 m（取自 T7 的逐 segment 輸出：detour 2554 m、stroke 973 m；T7+fix 的 parts 紀錄只有各部件總長與 segment 種類，part1 的內容與 T7 相同）。錨點回到 part0，part1 被丟（`dropped=[1]`），輸出回到 30/0/59、2004 m。
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
