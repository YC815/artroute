# 待辦（P0 審查後延後處理的項目）

來源：P0 各 task 審查與整支分支最終審查（2026-10-10）的分類結果。做完就刪掉該行。

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
