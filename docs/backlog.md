# 待辦（P0 審查後延後處理的項目）

來源：P0 各 task 審查與整支分支最終審查（2026-10-10）的分類結果；P2 一節來自 P1.5 的品質調查與審查（2026-10-11，`docs/p1-quality-investigation.md`、`docs/p1-5-quality-findings.md`）。做完就刪掉該行。

## P1

- [ ] `valhalla.json` 檔名寫死在 `routing/tiles.py` 和 `data/checks.py` 兩處；收斂成單一函式（例如 `config_path(build_dir)`）。
- [ ] `Router` 載入時讀 config dict，覆寫 `mjolnir.tile_dir` 為 `config.parent / "tiles"`，並在載入時套用搜尋參數。這樣換掛載路徑也能用，改參數不必重建 tiles（目前 `valhalla.json` 存絕對路徑，與相對 symlink 的設計前提矛盾）。
- [ ] `Router.__init__` 設定檔不存在丟 `FileNotFoundError`、回應缺欄位丟 `KeyError`、polyline 截斷丟 `IndexError`，都沒包成 `RoutingError`。
- [ ] `trace()` 不驗證 `search_radius_m` 範圍、空輸入會 `IndexError`：在 API 邊界用 pydantic 驗證。
- [ ] 匹配長度比例只有下限（`MIN_LENGTH_RATIO`），沒有上限；交給評分與 benchmark 決定。
- [ ] `/status` 回傳型別改成具名的 pydantic model（openapi-typescript 契約需要）。
- [ ] `taiwan_checks` 加入洲美快速道路：自行車路線不得使用它的 way。
- [ ] 用 osmium 盤點台灣所有 `highway=trunk` 但沒有 `motorroad`、也沒有 `bicycle` 標籤的 way，評估快速道路漏標風險。
- [ ] 若改用 tile extract（tar）讓多行程 mmap 共用記憶體：`valhalla_build_extract` 以 Python 模組 `valhalla.valhalla_build_extract` 形式附帶，不是 `python -m valhalla` 的子指令。
- [ ] m1 上現行建置（`20261010T145928Z`）用的是舊設定（實際搜尋半徑 100 m），P1 部署時重建。

## P2（吸附品質；P1.5 之後）

2026-10-11 的 P2 處理了評分（評分 v2，spec §4.5、§8 第 21 列）：「小圖形的評分太嚴」已刪除；「折返移除」的評分衝突已解除（見下）。其餘依價值與成本排序後延後，每項附原因。共同的原因：雲端 session 連不到 m1，**改變 `/match` 路線的項目都需要在真實路網上量測接受條件**（P1.5 的每一項都是這樣決定的），P2 只做了不改路線的評分與搜尋；下一個本機 session 可以直接量。

- [x] ~~折返移除（G）與繞圈移除（L）~~：P2.5 Q2 的剪小刺與小圈（`matching/prune.py`，主 spec §8 第 43 列）做了；以下是原本的紀錄。評分 v2 只看看得到的線，移除貼著目標的折返不再讓乾淨度下降，評分這一半已解決。移除本身改變 `/match` 的路線、要在新接點做轉彎檢查（安全相關），價值是路線變短（length/target 中位數 1.71 → 約 1.65），不是畫得更像；需要在 m1 用 bench（v2 分數與 v1 分數都看）量過再上。L 的 ms 最大 1.8 s，要改成增量計算。
- [ ] 斷成多個部件的開放筆畫整條反向（裁決 A3，`letter_l@taipei_daan`）：改變 `/match` 路線；接受條件（反向的單一 chain 何時比正向的多個部件好）要看真實路網上 connector 的長度才定得出來。
- [ ] 圖形跨水域、沒有橋的警告（品質調查 §3、§6）：自動搜尋已經自然避開（粗篩的距離場在水面上很遠）。手動模式的警告需要新的警告碼（`/match` 契約的 enum 變更，舊網站會把它當成格式錯誤，要先部署網站再部署引擎）或另一個欄位，以及「附近沒有可騎道路」的判斷門檻要在真實資料上調。可騎道路索引（§6.1）已經可以拿來做這個判斷。
- [ ] 封閉圈旋轉的切點離接點不到 8 m 時，該接點的轉彎探針被截短（接點轉彎檢查的審查觀察）：只有旋轉後的本體在路線頭或尾、且沒接任何東西時沒被重驗。bench 的 `bad_turns` 抓到再修：重驗旋轉本體的兩端，或避免切點離接點不到 8 m。搜尋 bench 對每個候選也跑 `bad_turns`，會一起抓。
- [ ] 反向的接受條件改成「不比正向差」：目前反向結果用和正向 trace 一樣的門檻接受，近平手時仍多花 1 次 trace，也沒有最小節省門檻。順便處理兩個小項：第 0 個部件固定正向、反向本體接不上時不退回正向。改變 `/match` 路線，需要 bench 量測。
- [ ] 人行道禁止選項（S，裁決 A6）：使用者想禁止而不是「盡量避免」時，把 `footway=sidewalk` 且自行車本來能走的改標 `bicycle=no`。bench 的 footway 30,766 → 4,167 m（−86%），但 overall 中位數 62 → 58、`square@taipei_daan@r30` 72 → 55，預設文字約慢 10%；做之前要先評估連通性（橋上只有人行道可騎、車道又是 `bicycle=no` 的地方）。等使用者決定。
- [ ] trace 吸得到、route 卻進不去的路段（例如 ramp）會逼出一條長 connector（決策 A7 之後仍在）：預設文字的筆畫 12（106 m，way 138265875，`use=ramp`）只能排在路線最前面，多一條 2,302 m 的 connector。做法待量測：裁掉進不去的那一段，或讓反向重新吸附避開它。搜尋帶起點提示時，這種部件可能讓起點跑出圈外（被淘汰）。
- [ ] `edge_walk` 在兩條 way 共用同一對端點時可能配錯 way（例如樓梯和旁邊的坡道）：bench 的禁行 way 斷言因此可能少抓，用途公尺數也可能有少量誤差。它是縱深防禦，真正的防線是 tiles 的 `bicycle=no`。
- [ ] 轉角 ≤ 30° 的接點完全不檢查（接點轉彎檢查的已知限制，不是回歸）：8 m 的弦角會把 S 形小折後面的 90° 轉彎藏起來；平面上幾乎平行重疊的上下層道路也量不出來（剪小刺的新接點已改成 ≤ 30° 也用 route 量，主 spec §8 第 43 列；但上下層在平面上重疊超過 8 m 時，兩個探針都落在重疊處，route 一樣分不出來）。要做到「絕不輸出不能騎的接點」得補上這一塊；做法待評估（縮小弦長，或 ≤ 30° 的接點也量），每個接點多一次 route 的延遲要在真實路網量。

### P2 自動搜尋的後續

- [ ] 精算候選數（spec §8 第 29 列）：16 個全部精算已在真實路網量過（`docs/p2-search-findings.md` 第 8 節：最慢 19.9 s、`late` 18／60、`gain` 中位數 +2.0），依判讀規則評估把 `FINE_KEEP` 加到 24；heart 推估會碰到 25 秒軟性預算，可一併評估 `SEARCH_ROUTERS=3`。**併入 P2.5 Q4**：精算改用描形規劃器、預算改 60 秒後重新量再決定（`docs/superpowers/specs/2026-10-11-p2-5-likeness-design.md` §7.3）。
- [ ] 粗篩參數（繞路係數 1.15–1.8、截斷 × 3、方向權重 0.5、KEEP_A 2000）只在合成地圖上調過。真實路網上前 16 名的粗篩排名與精算 overall 的 Spearman 中位數只有 +0.04（`docs/p2-search-findings.md`），目前靠全部精算彌補；要讓粗篩本身變準，得在 m1 上用搜尋 bench 的 `--json`（`evaluations`：每次精算的粗篩名次與分數）比較不同參數。
- [ ] 排隊位置只算得到同一個 uvicorn worker 內的等待者（spec §4.6），另一個 worker 也有人在等時顯示的位置偏前。要精確得用共享的佇列檔。
- [ ] `is_rideable` 是 Valhalla 通行規則的近似（`bicycle=private` 算不可騎等，spec §8 第 22 列），沒有逐條對照 3.9.1 的 lua。只影響排名。
- [ ] 搜尋時地圖上直接拖曳圓心（目前用按鈕設成圖形位置或地圖中心）。

## P2.5（像不像；設計見 `docs/superpowers/specs/2026-10-11-p2-5-likeness-design.md`）

- [ ] 連接線完整版（隱形連接線：連接線本身也用路網圖找「盡量沿已畫線」的路）。使用者可以接受少量連接線，先做輕量版（spec §7.2）；輕量版之後連接線仍明顯影響像不像才做。
- [ ] 既有接點的轉彎檢查（`turns.rideable`）在死巷剩不到 8 m 時，兩個探針會繞過尖端、落在不同處，看起來像約 143° 的轉彎而放行；只有一側有頂點的原路折回也認不出來。剪小刺用的嚴格版已經擋下（§8 第 43 列），但吸附與串接的既有接點仍用 `rideable`。改 `rideable` 會改變 `/match` 的路線，要在真實路網量 bench（`bad_turns` 用的也是 `rideable`）。
- [ ] 規劃器的繞遠成本沒有任何測試會紅（方向成本已經懲罰多出來的路程，加上 4Δ 上限），目前是保險；用 bench 的 v3 調權重時一起評估要不要留。
- [ ] `bench/likeness.py` 的預設資料目錄是 `tests/data/likeness/`，runtime image 不帶 `tests/`：在容器裡跑要 `--data` 指到複製進去的目錄（本機 prompt 已寫）。
- [ ] fine 的 `graph.region` 與規劃網路的建構不受期限中斷：網站上限 20 km 的大圖形在真實路網上的時間與記憶體要本機量，必要時加 fine 的範圍上限（超過就用 fast）。
- [ ] Valhalla 升級時重新檢查 `edge_walk` 是否檢查中間邊的通行（`test_edge_walk_does_not_check_one_ways…` 會變紅）；目前照著走只用 map_snap。
- [ ] 規劃器的權重只在合成地圖上定過；用本機 bench 的 v3 與下一輪標註調整（主 spec §8 第 48 列）。
- [ ] 剪小刺漏剪「基於直線多繞的小圈」（使用者看 `heart@taipei_daan` 縮圖指出，findings §2.5）：那段 708 m 的 detour，剪 439 m 的一刀 v3 +0.45、轉彎能騎，但超過圈長上限（外框 × 15% = 300 m）；剪 285 m 的一刀 v3 −0.20。可評估：圈長上限放寬到 25%（要用 bench 確認沒剪到圖形本身），或 detour 段允許更長的圈。使用者決定先部署、漏剪的交給手動微調。
- [ ] 剪小刺的 `rejected` 不分原因：便宜判斷（新接點的轉彎）沒過的圈也記進「不重試」，旁邊剪了別的圈、接點附近的幾何變了之後，它可能已經可以剪卻不會再試（`heart@taipei_daan` 有一個 23 m 的）。只對「v3 下降」與「route 量過不能騎」記不重試，或剪了之後清掉附近的 rejected。
- [ ] 引擎容器的 `/tmp` 累積 `valhalla_config_*.json`（重建前約 100 個，部署會清掉）：Router 或 bench 建了暫存設定檔沒刪。
- [ ] 校正資料擴充：第一輪只有 36 組、12 個案例、3 種圖形（有表態的 15 組）。每個子階段結束時再標一輪，涵蓋圖片描邊與較長的文字。

## P3（無人值守的每週重建、公開上線前；排在 P2.5 之後）

- [ ] 單一建圖鎖：`prune` 會刪掉所有沒有 `meta.json` 的目錄，包括另一個行程正在建的。
- [ ] `run_build`：`activate`、`prune` 在 try 之外——`activate` 失敗會留下有 meta 卻從未啟用的 build；`prune` 失敗會讓已啟用的建置仍丟例外。
- [ ] `meta.json` 改原子寫入（暫存檔 + `os.replace`）；`os.replace` 前 fsync。
- [ ] `download_with_md5`：空的 md5 回應會 `IndexError`；HTTP 錯誤丟 `httpx.HTTPStatusError` 而不是 `DownloadError`。補測試：HTTP 錯誤、空 md5、下載中斷、連跑三次 `run_build`、meta 寫入失敗。
- [ ] `read_meta` 遇到壞掉的 JSON 會丟例外，讓 `/status` 回 500；改成視為無資料，並測 current symlink 懸空的情況。
- [ ] `build_tiles` 子行程失敗（壞掉的 pbf）的分支補測試。
- [ ] `taiwan_checks`：加上「逆向長度 > 順向長度」的斷言，避免因無關原因繞開而空過；單一 edge 的 heading 45–135 判定在彎曲的分段 edge 上可能誤判失敗；`route()` 丟的 `RoutingError` 要標出是哪一項檢查。
- [ ] `prune` 只看新舊：手動回滾後，下一次建置會保留被放棄的壞版本、刪掉已知好的版本。回滾指令要刪除或標記被放棄的 build。
- [ ] 建圖不要在服務中的容器裡跑：改用一次性容器或至少 `nice`（spec §6.1 的低優先權），避免與服務共用 cgroup 的 2.1 GiB 尖峰。
- [ ] Dockerfile：base image 釘 digest（目前是浮動 tag，`uv_build` 卻有版本上限）；runtime image 改非 root；runtime 不帶 `tests/`。
- [ ] homelab `hl`：`cmd_public` 在 `docker inspect` 失敗時會放行，改讀 `compose.yml` 的 internal label；公開部署前修好，避免內網引擎被誤公開。
