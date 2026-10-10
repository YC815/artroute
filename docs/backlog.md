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

- [ ] 斷成多個部件的開放筆畫整條反向（裁決 A3）：反向吸附（spec §4.4）目前只處理「單一 chain、沒有警告」的開放筆畫。`letter_l@taipei_daan` 斷成兩個部件（垂直段、水平段），part0 終點 → part1 起點的 14.7 m 缺口正向 route 無解、反向有 603 m（推測是單行道），結果是一條 3027 m 的 connector、overall 44 → 34（coverage 0 → 67）。做法：把整個筆畫倒過來重新吸附，擴到多部件的筆畫。
- [ ] 折返移除（G）與繞圈移除（L），要和評分調整一起做（品質調查 §4.4 第二步）：length/target 中位數可降到 1.65（G）／1.57（L），但 cleanliness 是平均距離，貼著目標來回騎的部分距離接近 0，移除反而讓 overall 平均 −0.6／−1.0。L 的 ms 最大 1.8 s，上線前要改成增量計算。
- [ ] 圖形跨水域、沒有橋的警告（品質調查 §3、§6）：吸附前偵測並警告使用者。`taipei_riverside` 的分數上限受地理限制，引擎改善有限。
- [ ] 封閉圈旋轉的切點離接點不到 8 m 時，該接點的轉彎探針被截短（接點轉彎檢查的審查觀察）：只有旋轉後的本體在路線頭或尾、且沒接任何東西時沒被重驗。bench 的 `bad_turns` 抓到再修：重驗旋轉本體的兩端，或避免切點離接點不到 8 m。
- [ ] 反向的接受條件改成「不比正向差」：目前反向結果用和正向 trace 一樣的門檻接受，近平手時仍多花 1 次 trace，也沒有最小節省門檻。順便處理兩個小項：第 0 個部件固定正向、反向本體接不上時不退回正向。
- [ ] 人行道禁止選項（S，裁決 A6）：使用者想禁止而不是「盡量避免」時，把 `footway=sidewalk` 且自行車本來能走的改標 `bicycle=no`。bench 的 footway 30,766 → 4,167 m（−86%），但 overall 中位數 62 → 58、`square@taipei_daan@r30` 72 → 55，預設文字約慢 10%；做之前要先評估連通性（橋上只有人行道可騎、車道又是 `bicycle=no` 的地方）。
- [ ] 小圖形的評分太嚴：`match_rect`（1 km 圖形）只有 9–11 分，因為 `ZERO_SCORE_RATIO` 是對角線的 5%（約 48 m），路線再好也很難拿分（品質調查 §2.2）。

## P3（無人值守的每週重建、公開上線前）

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
