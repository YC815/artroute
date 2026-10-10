# 吸附品質調查報告（唯讀調查 + 原型量測）

> P1.5 吸附品質的調查報告，量測於 `b436026`（P1 引擎最終修正之前）；內容照原樣收進 repo，spec §8 的「原型」數字出自這裡。原型程式沒有放進 repo。

日期 2026-10-11。程式碼：branch `p1-engine` 的 `b436026`。m1 容器內 `matching/*.py`、`routing/*.py` 的 md5 從頭到尾都等於 `b436026`，調查期間沒有重新部署。調查期間 `p1-engine` 前進到 `bfa794b`（別的 task），見 §5.0。路網：m1 `artroute-engine` 容器，`current` = `20261010T183449Z`。沒有改任何 branch、沒有寫 baseline、沒有動 `current`。

所有數字都是實測；推測一律標「推測」。

## 0. 方法與可信度

- 原型：`qmatch.py`（不在 repo 內）是 `stroke.py`＋`compose.py`＋`service.py` 的逐行複本，加上旗標（Flags）切換各方案；`qbench.py`（不在 repo 內）跑 25 個 bench 案例＋`/match` 範例矩形（`match_rect`）。
- **一致性驗證**：旗標全關時，25 案例的 overall／coverage／cleanliness／length 與 `baseline.json` 逐案例完全相同（`match_rect` 也重現 11/22/0、5041 m、22 segments）。
- **官方 harness 驗證**：在容器 `/tmp/quality-engine` 放一份 engine 複本，`service.match_shape` 改呼叫原型，用 `PYTHONPATH` 覆蓋後跑 `python -m engine.bench`（不帶 `--update-baseline`）：旗標全關 → 平均 overall 48.4、沒有任何「退步」行；推薦組合 → 數字與 qbench 完全一致（見 §4.3）。我記下的 `EXIT=` 是管線最後 `tail` 的結束碼，不是 bench 的，所以這裡用 compare 是否印出「退步：」來判斷。
- 原型多切出一種 segment kind：`bridge` = `stroke._link` 為了接上相鄰兩段而補的 route（正式版也標成 `detour`）。`detour` 只指「失敗小段合併後繞路」。
- `ms` 是單次取樣（同 bench）；小差異是雜訊。

## 1. 三個主要根因

### 根因 1：trace_route 斷段後只取第一段，整筆畫被當成失敗而被盲切（量到的源頭）

- 41 個 bench 筆畫的「整筆畫第一次 trace」有 **38 個失敗**；失敗原因幾乎全是「匹配長度 < 70%」（18 個 < 10%）。全部 26 案例共 1829 次 trace 呼叫，只有 531 次成功。
- 原因是 map matching 斷段：`square@taipei_daan` 整筆 trace 時，Valhalla 回 **22 段互不相連的子路徑**（`trip` 只有第一段 37 m，其餘 21 段在 `alternates`，合計 8044 m ≈ 畫線 8018 m），段與段之間差 54–153 m。`Router.trace` 只讀 `trip`，等於把 99.5% 的匹配丟掉，再判為失敗。
- 斷段的機制（實測）：
  - meili 相鄰取樣點的轉移上限是 `max_route_distance_factor=5` × 點距 25 m = 125 m。畫線落在兩條平行路中間、或候選在逆向單行道上時，相鄰兩點的候選無法在 125 m 內合法相連 → 斷段。證據：把容器 config 的 `max_route_distance_factor` 改成 20，全部筆畫的子路徑總數 396 → 146（但匹配長度變成畫線的 1.82 倍，會繞圈）。放在 `trace_options` 裡實測無效，只有改 config 才生效。
  - 半徑太小（2% 對角線：2 km 圖形 57 m、1 km 圖形 30 m）：半徑 ×1.75 → 子路徑 148、整筆成功 13/41；×2.5（141 m）→ 93、22/41。`match_rect`（半徑 30 m）有 12.4% 的取樣點 30 m 內沒有可騎路段（中位數 5.6 m、P90 34.9 m）。
  - 無效的參數（子路徑數完全不變）：`gps_accuracy` 15/30、`beta` 10、`turn_penalty_factor` 0、`breakage_distance` 5000、`interpolation_distance` 0、bicycle `shortest`。
- 盲切的後果：`_solve` 依「點的索引」對半切，不在真正斷掉的地方切。`square@taipei_daan` 被切成 20 段 stroke＋5 段 detour＋12 段 bridge；`square@taipei_riverside` 是 31＋6＋18。

### 根因 2：各小段獨立吸附，切點兩側端點對不上，補橋（bridge）在單行道／分隔路／河岸上繞一大圈

- 每個小段的 trace 起訖點各自吸附，同一個切點在兩側可以吸到不同的路（`_ensure_matched` 容許差到 2×半徑）。兩端差 > 1 m 就由 `_link` 補一條 route。
- 補橋長度遠大於缺口：`square@taipei_daan` 12 條 bridge 共 **4557 m**，缺口 23–122 m 變成 30–817 m；`square@taipei_riverside` 18 條共 **13022 m**，缺口 35–78 m 變成 1841–2193 m（例：缺口 49 m 的 bridge 走 2039 m，從三重經台北大橋機慢車引道過河再回來）。
- 立即折返（A→B→A）幾乎全部發生在 segment 接點：`square@taipei_daan` 862 m 中 840 m、其他三個案例 100% 在接點。`/match` 範例的 `…[121.542276,25.02989],[121.542573,25.030097],[121.542276,25.02989]…` 就是一個：切點 (121.542655, 25.0300) 被前一段 trace 吸到巷子裡約 36 m 的地方（100 m 的 stroke 終點 (121.542573, 25.030097)），下一段 detour 從那裡出發，只能原路退回。
- 接點 U-turn：base 在 26 個案例共 855 個 segment 接點中，**269 個（31%）是原地折返**。推薦組合是 303 個接點中 84 個。

### 根因 3：繞路與串接只有一種走法——單行道成本、開放筆畫不能反向

- 單行道：把每段 detour／bridge 以同樣起訖、`ignore_oneways` 重算（只用來歸因，不是方案），單行道造成的長度佔多出長度的 16–33%（§2.1）。整條流程都忽略單行道的反事實：長度中位數 2.01 → 1.63，所以單行道是主因之一，但不是唯一原因。
- detour 用 bicycle 預設成本（偏好自行車道），不是最短：`route_opts.shortest` 單獨就讓 `match_rect` 的 bridge＋detour 從 3021 m 降到 2653 m。
- 開放筆畫只能照畫的方向走（`compose._options` 對開放部件只給一種進出）：`hi` 四筆都是由上往下畫，connector 3 條共 5231 m（`hi@taipei_daan`），佔多出長度的 49%。

## 2. 多出來的長度從哪來

### 2.1 四個指定案例的拆解（base，正式程式）

excess = route − target。「drawn」是該類 segment 所對應的畫線長度。

| 案例 | route / target | stroke 長／畫線 | detour 長／畫線 | bridge | connector | 立即折返可消除 | 單行道造成（detour＋bridge） |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `match_rect` | 5041 / 2745（excess 2296） | 2020 / 1786（13 段） | 2165 / 958（5 段） | 856（4 段） | 0 | 284（接點 284） | 669 |
| `square@taipei_daan` | 15022 / 8018（excess 7004） | 7990 / 7071（20） | 2475 / 947（5） | 4557（12） | 0 | 862（接點 840） | 2321 |
| `square@taipei_riverside` | 31371 / 8018（excess 23353） | 6339 / 6074（31） | 12010 / 1945（6） | 13022（18） | 0 | 2433（接點 2433） | 7496 |
| `hi@taipei_daan` | 17997 / 7232（excess 10765） | 7602 / 6388（23） | 2832 / 844（7） | 2332（8） | 5231（3） | 2670（接點 2644） | 1675（connector 0） |

佔 excess 的比例：

| 案例 | bridge（端點偏離補橋） | detour 多出 | stroke 多出 | connector | 立即折返 | 單行道 |
| --- | --- | --- | --- | --- | --- | --- |
| `match_rect` | 37% | 53% | 10% | 0 | 12% | 29% |
| `square@taipei_daan` | 65% | 22% | 13% | 0 | 12% | 33% |
| `square@taipei_riverside` | 56% | 43% | 1% | 0 | 10% | 32% |
| `hi@taipei_daan` | 22% | 18% | 11% | 49% | 25% | 16% |

- 前四欄互斥、加起來是 100%；「立即折返」與「單行道」跨在前幾類之上，不能相加。
- 「吸附端點偏離造成的鋸齒」= bridge 全部（畫線長度 0）＋接點上的立即折返。
- 立即折返中有一部分是畫圖需要的：只移除「不讓 coverage 變差」的折返（總預算 1 m），可移除量為 203／460／1623／765 m。`hi` 其餘約 1.9 km 一移除 coverage 就會變差，推測多半是 connector 沿著已畫好的筆畫走回去，屬於畫圖本身。
- 單行道欄：每段 detour／bridge 以同起訖重算，`ignore_oneways` 前後的差。再加上 `ignore_restrictions`，數字完全相同，所以轉向限制不是這些繞路的原因。
- 整條流程忽略單行道的反事實（違規，只用來歸因）：route 5041→4122、15022→12836、31371→23996、17997→16134 m。

### 2.2 `match_rect` 為什麼只有 11 分

- 半徑 30 m（1 km 圖形，下限 30 m）。矩形四邊不在路上：底邊在瑞安街以北約 20 m，左邊（121.5400）最近的可騎路段 55 m，上邊附近只有 service road。12.4% 的取樣點 30 m 內沒有路。
- 整筆 trace 拆成 12 段子路徑、只覆蓋 2047／2745 m；分治後 13 stroke＋5 detour＋4 bridge。
- 推測：就算路線完美，小圖形在評分上也很難拿分（`ZERO_SCORE_RATIO`＝對角線 5% ≈ 48 m）。只註記，評分調整屬 P2。

## 3. taipei_riverside 為什麼特別差

| 量測 | taipei_riverside（square） | taipei_daan（square） |
| --- | --- | --- |
| 取樣點最近可騎路段距離中位數／P90／最大 | 12.1 / 63.2 / 180.0 m | 7.7 / 23.1 / 69.6 m |
| 超過半徑（56.7 m）的取樣點 | **12.7%** | 0.3% |
| 超過 100 m 的取樣點 | **4.3%** | 0% |
| 整筆 trace 子路徑合計／畫線 | 6183 / 8018 m（24 段，段間缺口最大 313 m） | 8044 / 8018 m（22 段） |
| detour 長／對應畫線 | 12010 / 1945 m（6.2 倍） | 2475 / 947 m（2.6 倍） |
| bridge | 18 條 13022 m | 12 條 4557 m |
| 快速道路禁行 way（端點在範圍內） | 12（新北環河快速道路 8、新生高架 4） | 12（建國快速道路 9、基隆路高架 3） |

用資料回答：

1. **圖形跨河**：2 km 正方形的西側在淡水河與三重岸，北側碰到基隆河堤外與台北交流道。heart／star 也一樣：12.7–13.5% 的取樣點半徑內沒有可騎路，最近的 edge 有 20–24 個點是 `primary/ferry`（渡輪航線）。
2. **只能從橋與水門過**：`square@taipei_riverside` 的路線用了 3 次「台北大橋機慢車引道」。detour 3579–4857 m 對應的畫線只有 524–782 m（例：起點在三重的南邊，沿二重環狀自行車道→淡水河河濱自行車道→堤外道路繞 4857 m）。
3. **補橋跨過障礙**：缺口 35–78 m 的 bridge 走 1841–2193 m，因為兩端落在河、引道（單向）、環河北路、台北交流道的兩側。
4. **河濱自行車道有被用到**：路線 31.4 km 中 9.3 km 是 cycleway，但主要用在 detour 裡，不是用來畫圖。
5. **不是快速道路標記造成的**：範圍內被禁行的 way 數和 Daan 一樣（12 條）；`p1-engine-findings.md` 已量過，沒有標記的 P0 build 上 riverside 5 個案例的結果完全相同。渡輪 edge 沒有出現在路線裡（0 m）。
6. 單行道在這裡佔多出長度的 32%，和 Daan 的 33% 相當，不是 riverside 特有的原因。

推測：這個地點的低分主要是案例放置的問題（中心點在河岸，圖形跨過兩條河）。引擎能改善的上限有限，推薦組合只把 riverside 平均從 21.6 拉到 27.2。比較實際的做法是在吸附前偵測「圖形跨水域、無橋」並警告使用者，沒有實作，只是建議。

## 4. 改善方案

### 4.1 方案與合法性

| 代號 | 方案 | 原理 | 合法性（單行道／快速道路／真實路網） |
| --- | --- | --- | --- |
| R035 | 半徑比例 0.02 → 0.035（下限 30、上限 150 不變） | 給 meili 更多候選，斷段變少，整筆 trace 成功率提高 | 路徑仍全部是 bicycle costing 的 Valhalla trace／route 結果；map_snap 遇到逆向會截斷而不是逆騎（P0 已證實）。只是可能吸到較遠的路 |
| C | 分治改成串接（chain） | 右半段的第一個輸入點換成左半段 trace 的實際終點，接點自然吻合，不必補橋 | 每段仍是 Valhalla 結果；接點規則與 base 相同（≤ 1 m） |
| S | 繞路／補橋／connector 用 `costing_options.bicycle.shortest=true` | 繞路取最短，而不是自行車偏好路線 | 只換成本函數，通行權與單行道的判斷不變。實測：建置驗證 `taiwan_checks`（洲美街單行段、洲美快速道路 36 個方向）用 shortest router 回 `[]`，全數通過 |
| V | 開放筆畫可以反向走 | compose 給開放部件第二個選項（終點進、起點出）；選到時用反向的點重新吸附整筆 | 反向是重新跑一次 trace／route，不是把現成路線倒過來走，所以單行道照樣遵守 |
| BR | 補橋嚴格落點＋重試 | bridge 兩端都必須落在 1 m 內（正式版沒檢查，見 §5.1）；失敗時把前段尾巴／後段開頭裁掉 20／50／100 m 再接；compose 的 connector 失敗時同樣裁掉開頭重試 | 只裁掉既有路段，接點仍 ≤ 1 m，沒有直線 |
| G | 有防護的立即折返移除 | 移除 A→B→A，以及折返到同一條線上的情況，條件是 coverage 誤差（評分的 mean＋P90 /2）總增量 ≤ 1 m | 剩下的每一小段都是原本某段路的子段、方向不變，所以不會逆向，也不會多出任何 edge。新的轉彎點用 route 檢查「8 m 前 → 8 m 後」是否能直接騎：245 個移除中有 193 個需要檢查，**0 個不能直接騎** |
| L | 有防護的繞圈移除 | 把路線回到同一點的閉合段 P→…→P 剪掉（包含折返），條件同 G，並且新轉彎必須通過直接騎檢查才收 | 同 G；314 次剪除中只有 1 次被轉彎檢查擋下（直接不收） |
| 否決 | P（接受截斷前綴）、A（直接用 trace 的 alternates 縫起來）、T（繞路經過畫線的中間點）、vcut（先在轉角切開）、tpf500（turn penalty）、use_roads 0／1、bicycle_type Road／Mountain、trace 用 shortest、自適應半徑 | 見下表 | — |

### 4.2 bench 前後數字（qbench；25 案例，不含 `match_rect`）

better/worse 是 overall 比 base 高或低超過 3 分的案例數。

| 方案 | overall 中位數 | overall 平均 | coverage 中位數 | cleanliness 中位數 | length/target 中位數 | length/target 平均 | ms 中位數／最大 | better/worse | riverside 平均 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| base | 46 | 48.4 | 74 | 41 | 2.01 | 2.14 | 245 / 539 | – | 21.6 |
| R035 | 64 | 52.5 | 74 | 55 | 1.72 | 1.89 | 301 / 1222 | 8/2 | 25.8 |
| R05（0.05） | 60 | 52.0 | 72 | 51 | 1.60 | 1.83 | 445 / 1765 | 10/3 | 25.0 |
| C | 59 | 49.5 | 76 | 52 | 1.92 | 2.08 | 227 / 509 | 4/2 | 22.0 |
| S | 54 | 48.5 | 74 | 43 | 1.95 | 2.08 | 234 / 511 | 2/3 | 19.2 |
| V | 48 | 48.6 | 74 | 41 | 2.01 | 2.09 | 241 / 513 | 1/1 | 21.6 |
| BR | 52 | 48.7 | 74 | 43 | 1.90 | 2.11 | 266 / 550 | 1/1 | 21.6 |
| G | 45 | 47.5 | 73 | 39 | 1.93 | 2.04 | 257 / 533 | 0/1 | 21.2 |
| L | 45 | 47.4 | 73 | 39 | 1.91 | 2.03 | 742 / 4473 | 0/1 | 21.2 |
| P（否決） | 54 | 48.6 | 75 | 46 | 1.96 | 2.09 | 221 / 585 | 4/3 | 22.6 |
| A＋R035（否決） | 61 | 49.7 | 73 | 54 | 1.69 | 1.86 | 136 / 447 | 8/5 | 21.2 |
| T（否決） | 56 | 51.0 | 76 | 39 | 2.02 | 2.40 | 253 / 517 | 4/0 | 25.6 |
| vcut（否決） | 52 | 49.0 | 75 | 39 | 1.94 | 2.19 | 193 / 325 | 3/1 | 22.0 |
| tpf500（否決） | 46 | 47.1 | 72 | 40 | 1.95 | 2.01 | 244 / 530 | 0/2 | 20.0 |
| use_roads 1.0（否決） | 46 | 47.4 | 74 | 43 | 2.02 | 2.13 | 272 / 545 | 0/3 | 21.4 |
| bicycle_type Road（否決） | 46 | 47.4 | 74 | 41 | 2.00 | 2.14 | 233 / 506 | 0/3 | 21.2 |
| C＋R035 | 64 | 52.5 | 73 | 55 | 1.71 | 1.88 | 300 / 1134 | 10/2 | 26.8 |
| C＋R035＋S | 60 | 54.0 | 73 | 55 | 1.71 | 1.86 | 301 / 1128 | 12/0 | 26.8 |
| C＋R035＋S＋V | 62 | 54.4 | 74 | 55 | 1.71 | 1.82 | 303 / 1138 | 13/0 | 27.2 |
| **C＋R035＋S＋V＋BR（推薦）** | **62** | **54.4** | **74** | **54** | **1.69** | **1.86** | **313 / 1121** | **13/0** | **27.2** |
| 推薦＋G | 62 | 53.8 | 73 | 54 | 1.65 | 1.80 | 324 / 1153 | 12/1 | 26.8 |
| 推薦＋L | 62 | 53.4 | 73 | 54 | 1.57 | 1.76 | 573 / 1799 | 12/2 | 26.8 |
| C＋R05＋S＋V＋BR | 62 | 53.8 | 72 | 51 | 1.59 | 1.78 | 504 / 1599 | 13/2 | 27.0 |

否決理由：

- P、A：partial 案例增加（A 6 個、P 4 個），`match_rect` 掉到 0–2 分。縫合 alternates 等於在真正的斷點補橋，又回到根因 2。
- T：路線更長（平均 2.40）。
- vcut：`match_rect` 掉到 0 分（partial）。
- tpf500、use_roads、bicycle_type、trace shortest：效果在雜訊內或變差。
- 自適應半徑（P95 最近距離＋15 m）：overall 中位數 46，每個案例多約 900 ms 的 locate。
- R05：長度最短，但 riverside 與 r30 案例退步 3 個，ms 最大 1.6–1.8 s。

### 4.3 推薦組合在官方 harness 的結果

`PYTHONPATH=/tmp/quality-engine:/tmp/quality-proto python -m engine.bench`，比對 repo 的 `baseline.json`：**沒有任何「退步：」行**，成功 25/25、SLOW 0。

| | overall 中位數 | overall 平均 | coverage 中位數 | cleanliness 中位數 | length/target 中位數 | length/target 平均 | ms 中位數／最大 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| base（官方 bench，同一天） | 46 | 48.4 | 74 | 41 | 2.01 | 2.14 | 233 / 521 |
| C＋R035＋S＋V＋BR | **62** | **54.4** | 74 | 54 | **1.69** | 1.86 | 300 / 1140 |

- 逐案例：13 個進步超過 3 分（最多：`hi@kaohsiung_lingya` 38→62、`square@kaohsiung_lingya` 39→59、`letter_l@taichung_west` 46→64、`hi@taipei_riverside` 16→32、`heart@taipei_daan@r30` 52→66）。最大退步是 `square@taipei_daan@r30` 72→69，在 compare 的容忍值 3 以內。
- `letter_l@taipei_daan` 的 overall 都是 44，但 coverage 從 0 變 66、長度從 1711 m 變 4672 m：base 只畫了 L 的一部分（另一部分被 compose 丟掉，見 §5.2），推薦組合畫出完整的 L。overall 看不出這個差別。
- `match_rect`：11 → 9（長度 5041 → 4331 m）。小圖形評分問題不在這次範圍。
- 四個指定案例（推薦組合）：

| 案例 | route（base → 推薦） | length/target | bridge | 其他 |
| --- | --- | --- | --- | --- |
| `match_rect` | 5041 → 4331 | 1.84 → 1.58 | 856 → 175 | detour 2165 → 2094 |
| `square@taipei_daan` | 15022 → 13300 | 1.87 → 1.66 | 4557 → 1219 | detour 2475 → 0；stroke 只剩 8 段 |
| `square@taipei_riverside` | 31371 → 26356 | 3.91 → 3.29 | 13022 → 8311 | detour 12010 → 9429 |
| `hi@taipei_daan` | 17997 → 15758 | 2.49 → 2.18 | 2332 → 870 | connector 5231 → 3685 |

### 4.4 建議採用

1. **第一步：C＋R035＋S＋V＋BR**。overall 中位數 46→62、平均 48.4→54.4；length/target 中位數 2.01→1.69；ms 中位數 233→300、最大 521→1140（離 5 s 逾時還很遠）；bench 沒有退步。各項改動都不新增 edge 種類，合法性的論證見 §4.1。
   - 實作要點：
     - `service.RADIUS_RATIO` 改為 0.035。
     - `stroke._solve` 改成串接：右半段的起點換成左半段的實際終點。
     - `Router.route` 支援 costing_options，只在吸附流程傳 `shortest`。
     - `compose._options` 讓開放部件多一個反向選項，並重新吸附反向的筆畫。
     - `_link`／`_append` 檢查補橋兩端都落在 1 m 內，失敗時裁頭尾重試。
2. **第二步（建議等 P2 評分調整後再上）：G 或 L**。length/target 中位數可再降到 1.65（G）或 1.57（L），共移除 10.6 km（G）／17.1 km（L）的折返與繞圈，但 overall 平均 −0.6／−1.0。原因是 cleanliness 是「路線取樣點離目標的平均距離」：貼著目標來回騎的部分距離接近 0，會把平均拉低，移除反而讓分數變差。評分沒有懲罰重複與多餘長度，這點跟產品目標（越少折返越好）衝突。L 的 ms 最大 1.8 s，上線前要改成增量計算（G 已經改好）。

## 5. 調查中發現的正式版缺陷（與上述方案獨立）

### 5.0 p1-engine 目前的 HEAD 會退步（調查期間的新 commit）

調查期間 `p1-engine` 從 `b436026` 前進到 `bfa794b`，其中 `f1e859d` 讓繞路與補橋的端點一律只容許 1 m，不合格就斷開 chain，沒有重試。我把 HEAD 的 `engine/src/engine` 用 `git archive` 複製到容器 `/tmp/quality-head`，用 `PYTHONPATH` 跑官方 bench（唯讀）：

- **3 個案例退步**：`heart@taipei_daan` 64 → 28（coverage 76 → 0，長度 12641 → 7918 m）、`star@taipei_daan@r30` 42 → 34（coverage 66 → 0，長度 14957 → 2617 m）、`hi@taipei_riverside` 16 → 10。
- 成功案例的平均 overall 48.4 → 46.4。其餘 22 個案例與 baseline 相同。

我的原型 BR 第一版（只嚴格、只裁後段開頭）在同兩個案例上也是 coverage 0（`heart@taipei_daan` 26、`star@taipei_daan@r30` 34、長度 0.36 倍）。機制是 §5.2：嚴格拒絕 → chain 斷開 → compose 的 connector 也接不上 → 整個部件被丟掉。推測 HEAD 的退步就是 `f1e859d` 造成的，但沒有逐 commit 隔離。

加上「前段尾巴／後段開頭裁掉 20／50／100 m 再重試」（BR 最終版）之後，同樣的嚴格規則就不會退步（§4.2：13/0）。建議 HEAD 補上重試，或在 compose 丟部件前也先裁切重試。§5.1 的直線缺口在 HEAD 已改成「丟 DiscontinuousRoute（500）」。

### 5.1 補橋會畫出不在路上的直線

`stroke._route` 容許 route 端點離要求的位置 2×半徑，`_link` 收下 bridge 時沒有檢查 bridge 的起訖是否接上前後兩段。base 的 26 個案例中，**4 個接點有 > 1 m 的缺口**（`match_rect` 11.6 m、`heart@taipei_daan` 2 個 11.6 m、`star@taipei_daan@r30` 21.2 m）。`_route_points` 直接把點串起來，等於在地圖上畫了一段直線，違反「絕不畫直線」。BR 方案修正後缺口為 0。

### 5.2 吸附後的端點可能落在 route 到不了的小路網，部件被整個丟掉

- trace 的端點可能落在 Valhalla route 視為不可達的 edge 上（`minimum_reachability=0` 時回「No path could be found」）。route 預設會把起點悄悄改吸到 15–203 m 外的地方，結果是 0 長度，或者端點對不上。
- 於是 chain 斷開，compose 的 connector 也接不上，該部件被丟棄：`letter_l@taipei_daan`、`letter_l@taipei_riverside` 在 base 的 coverage 都是 0。
- BR 修好前者（coverage 0→66）。後者在所有方案下仍是 coverage 0（起點偏 202.6 m），沒有修。

### 5.3 接點的轉彎沒有驗證

兩段 Valhalla 結果在節點接起來時，接點的轉彎沒人檢查。量法：轉角 > 30° 的接點，從 8 m 前 route 到 8 m 後，看 Valhalla 會不會直接騎過去。

- base：855 個接點中有 57 個轉角 > 30°，其中 2 個 Valhalla 不會直接騎（要繞 154–500 m），都在 `match_rect`。
- 推薦組合：303 個接點中有 36 個轉角 > 30°，其中 1 個不會直接騎，也在 `match_rect`。
- 原因沒查清楚：locate 看不到轉向限制。推測是轉向限制，或是量測點吸附到旁邊的 edge。正式版應該加上接點檢查，不論採不採用這些方案。

### 5.4 路線會用到人行道、樓梯、停車場通道

`square@taipei_riverside` 的路線含 footway 1255 m、steps 16 m、parking_aisle 573 m。這是 bicycle costing 的預設通行規則，不在這次範圍；推測需要產品決定是否禁止。

## 6. 疑慮與風險

- 推薦組合是在 `b436026` 上量的。HEAD 已重構 `_route`／`legal_route`／compose，移植時要重量一次。HEAD 本身目前就有 3 個 bench 退步（§5.0），先處理那個。

- 半徑加大讓端點容許偏差（2r）一起變大：2 km 圖形從 113 m 變 198 m。對評分有利，但起訖點可能離使用者畫的位置更遠。另外 ms 最大值約變成 2.2 倍。
- 推薦組合只在 25 個合成案例上量過，沒有真實使用者畫的圖。中位數只是第 13 個值，所以同時列了平均與 better/worse。
- riverside 的上限受地理限制（跨河），引擎再怎麼改也有限；應該在吸附前警告「圖形跨水域」。
- 評分（cleanliness 是平均值）和「少折返」的目標衝突，G／L 要等 P2 評分調整後才看得出效果。
- 合法性驗證的限制：對整條路線跑 `edge_walk` 在 base 就有 24/26 失敗（推測是 trace 的端點在 edge 中間、以及原地 U-turn 讓逐點比對失敗），不能拿來當驗證工具。所以合法性靠三件事：（a）每段都是 bicycle costing 的 Valhalla 結果；（b）後處理只刪不加；（c）每個新轉彎點用 route 檢查能否直接騎；另外 `taiwan_checks` 在 shortest 下通過。U-turn 在雙向道路中段算不算合法沒有查證，推測可以（慢車迴轉）。
- 根因 1 的另一種修法是改 config 的 `max_route_distance_factor`，它確實會減少斷段，但匹配長度變成畫線的 1.82 倍，不建議。

## 7. 重現

原型程式（`qmatch.py`、`qbench.py`）、逐案例的原始輸出與執行腳本都沒有放進 repo，這份報告的數字不能直接重跑。原型各旗標的定義見 §4.1 的表，實作要點見 §4.4；正式程式移植後的量測見 `docs/p1-5-quality-findings.md`。調查時在容器內建立的暫存目錄（`/tmp/quality-proto`、`/tmp/quality-engine`、`/tmp/quality-head`、`/tmp/quality-tmp`）已全部刪除。
