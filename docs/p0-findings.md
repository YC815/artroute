# P0 驗證結果

## pyvalhalla 3.9.1
- macOS arm64 wheel：可用
- `python -m valhalla --help` 列出的執行檔：
  - `print_bin_path`
  - `valhalla_build_tiles`
  - `valhalla_add_landmarks`
  - `valhalla_convert_transit`
  - `valhalla_build_admins`
  - `valhalla_add_predicted_traffic`
  - `valhalla_ingest_transit`
  - `valhalla_service`
- linux arm64（m1 容器）：見下方「linux arm64」一節

## bicycle 交通規則（合成地圖）
- 順向 trace：通過（0.404 km，全程在 oneway 街上）
- 逆向 route：繞路，未逆向（0.85 km，繞上面那條街）
- 逆向 trace 的實際行為：既不是 DETOUR 也不是 ERROR——`map_snap` 靜默退化，回傳 0.0 km、2 個重疊點（都在 (121.504, 25.05)），沒有丟 `RoutingError`
  - 被截斷時丟掉的是「第一個非法路段之後的所有東西」，不只逆向那半：0→2→0 只回傳 0.404 km（輸入 0.806 km），0→2→0→2 也只回傳 0.404 km（輸入 1.209 km），結尾的合法段一起消失
  - 同樣的逆向 trace 改用 `edge_walk`：丟錯誤（`Exact route match algorithm failed to find path`）
  - 對照組：雙向街逆著騎的 trace 正常（0.404 km）
  - Valhalla 預設 `service_limits.trace.max_search_radius` 只有 100 m，超過會丟 `RoutingError`（`Input trace option is out of bounds`）；`tiles.write_config` 產生的設定檔已調到 150 m（spec §4.3 的上限）
- oneway:bicycle=no：允許逆向（route 0.404 km，直接逆著騎）
- motorway：避開（route 2.101 km，繞下面那條 row 1 的住宅街）
- `Router.trace()` 的處理：`map_snap` 本身不報錯，所以 Router 在回傳前驗證結果，遇到下列情況丟 `RoutingError`：少於 2 個不同的點；匹配長度低於輸入折線長度的 `MIN_LENGTH_RATIO`（0.7，P1 benchmark 會調）；結果的起點或終點離輸入的起點或終點超過 `2 × search_radius_m`。`_route_result` 遇到相鄰 leg 接點相差超過 1 m 也丟 `RoutingError`，不會串出直線補線。
- 對 P1 的影響：吸附逆向或被截斷的 trace 現在會丟 `RoutingError`，P1 必須接住這個例外，改用部件端點之間的 `route()` 補段；`MIN_LENGTH_RATIO` 要用真實 GPS 軌跡的 benchmark 調整，GPS 抖動讓匹配長度偏離輸入時可能誤殺。

## linux arm64（m1 容器）
- pyvalhalla wheel：可用（`pyvalhalla-3.9.1-cp312-abi3-manylinux_2_28_aarch64`，容器 Python 3.13.11，aarch64，OrbStack kernel 7.0.14）
- osmium wheel：可用（`osmium-4.3.1-cp313-cp313-manylinux_2_26_aarch64`），但 `python:3.13-bookworm-slim` 缺系統函式庫 `libexpat.so.1`
  - 第一次 `docker build --target test` 在 pytest 收集階段失敗，原始輸出：
    ```
    ImportError while loading conftest '/app/tests/conftest.py'.
    tests/osm_fixture.py:6: in <module>
        import osmium
    .venv/lib/python3.13/site-packages/osmium/__init__.py:8: in <module>
        from ._osmium import (InvalidLocationError as InvalidLocationError,
    E   ImportError: libexpat.so.1: cannot open shared object file: No such file or directory
    ```
  - 修法：`engine/Dockerfile` 的 `test` stage 加 `apt-get install libexpat1`。osmium 只在測試用（dev 依賴），`runtime` stage 不動；測試、檢查、門檻都沒改
- 容器內測試：`53 passed in 1.03s`（`docker build --target test --progress=plain .`，build 成功；含會實際建 Valhalla tiles 的 `valhalla` marker 測試）

## 台灣路網建置
資料來源：Geofabrik `taiwan-latest.osm.pbf`，md5 `28cc8b1b689b45b640b5a8c5d2042b79`（下載後與 meta.json 一致）。在 m1 容器內執行 `python -m engine.data.cli`，一次通過、`EXIT=0`。
- pbf 大小：327,422,556 bytes（312 MiB；`du -sh` 顯示 321M）
- tiles 大小：336 MiB（`du -sh builds/20261010T145928Z`；`tiles/` 344,424 KiB，261 個 .gph）
- 建置時間：總共 2 分 43 秒（14:57:26Z → 15:00:09Z）
  - 下載 pbf 2 分 02 秒（約 2.7 MB/s，受 Geofabrik 頻寬影響）
  - `valhalla_build_tiles` + 驗證 + 啟用 41 秒（另外單獨重跑一次 `build_tiles` 為 40.0 秒，8 核）
- 建置時尖峰記憶體（Docker VM 共 8 GiB，可見 7.817 GiB）：
  - `docker stats --no-stream` 每 10 秒取樣的最大值：1.909 GiB（取樣間隔太粗，尖峰出現在兩次取樣之間）
  - 重跑一次 `build_tiles` 到暫存目錄，容器內每 0.25 秒讀 cgroup：anon（行程實際占用）尖峰 2.081 GiB，出現在 26 秒左右；含 page cache 的 `memory.current` 尖峰 3.143 GiB
  - 容器啟動後 cgroup `memory.peak`：3,550,728,192 bytes（3.31 GiB，含 page cache，含下載與建置）
  - 結論：實際需要約 2.1 GiB，連 page cache 也算進去不到 3.4 GiB，8 GiB 的 VM 很寬裕
- 驗證查詢（洲美街單行段）：通過（`taiwan_checks` 沒有 failure，`/data/current` 已切換到 `builds/20261010T145928Z`）
- 額外驗證（容器內 `Router(/data/current/valhalla.json)`，座標取自 `engine.data.checks`）：

  | 方向 | length_km | edges（way_id, begin_heading） |
  | --- | --- | --- |
  | `ZHOUMEI_WEST → ZHOUMEI_EAST` | 0.416 | (287016294, 87), (287016294, 88), (287016294, 108) |
  | `ZHOUMEI_EAST → ZHOUMEI_WEST` | 0.495 | (287016294, 87), (1321767003, 358), (1118312839, 267), (1118312839, 268), (1118312838, 241), (1118312838, 292), (1451078295, 253), (287016294, 123) |

  - 逆向比順向長 0.079 km（+19%），多繞了 4 條別的 way（1321767003、1118312839、1118312838、1451078295），沒有直接逆著騎
  - 逆向路線上 way 287016294 的 edge 只有 heading 87 與 123（都朝東，落在檢查的 45–135 範圍內），沒有朝西的 edge
  - 繞路只多 79 m：逆向路線的第一個和最後一個 edge 都在 way 287016294 上且朝東，繞行的環道很小（原因是推測：兩個查詢點離 way 兩端節點很近；沒有逐點核對座標）

## hl --internal
- 部署：通過。`hl deploy artroute-engine --internal` 最後一行 `完成：http://artroute-engine:8080 （內網：只有 hl 網路上的 app 連得到）`，健康檢查「內網回應正常」
- ls：通過。`artroute-engine  running  http://artroute-engine:8080  internal`
- 從 hl 網路內讀 `/status`：通過。`docker exec hl-proxy wget -qO- http://artroute-engine:8080/status` 回 `{"data":{"build_id":"20261010T145928Z","osm_md5":"28cc8b1b689b45b640b5a8c5d2042b79","created_at":"2026-10-10T15:00:09.887427+00:00"}}`；建置前同一個端點回 `{"data":null}`，建置完成後不用重啟就回新版（`/status` 每次讀 `current`）
- public 拒絕：未在 m1 實測（這次任務不跑 `hl public`）；`hl` 腳本在 `hl public` 前檢查 `zone.chen.hl.internal` label，是內網 app 就 `die`

## 結論
- P0 四項（pyvalhalla on m1、台灣 tiles、bicycle 單行道、hl --internal）：全部通過。唯一的修補是 test stage 補 `libexpat1`，與 P0 假設無關
- 對設計的影響：無，spec 不用改。P1 的參考數字：台灣完整建置約 41 秒、約 2.1 GiB RAM、tiles 336 MiB，每次重建都可以直接全量重跑
