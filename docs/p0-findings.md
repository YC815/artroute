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
  - Valhalla 預設的 `service_limits.trace.max_search_radius` 與 `meili.default.max_search_radius` 都是 100 m。有效的半徑上限要**兩個都設成 150 m**（spec §4.3 的上限）：前者決定請求會不會被拒絕（超過會丟 `RoutingError`，`Input trace option is out of bounds`），後者才是 Valhalla 實際使用的半徑上限
  - 早期版本只設了 `service_limits`，Valhalla 會把請求的 150 m 悄悄壓回 100 m，不報錯也不生效（最終 review 的 probe：軌跡平行於路、偏移 120–140 m 時，radius=150 與 100 一樣失敗；把 `meili.default.max_search_radius` 也設成 150 後，radius=150 成功（0.605 km），radius=100 仍失敗）。現在 `tiles.write_config` 兩個都設，並有測試驗證
  - m1 上現行的建置是用舊設定產生的，下次重建才會套用 150 m
- oneway:bicycle=no：允許逆向（route 0.404 km，直接逆著騎）
- motorway：避開（route 2.101 km，繞下面那條 row 1 的住宅街）
- `Router.trace()` 的處理：`map_snap` 本身不報錯，所以 Router 在回傳前驗證結果，遇到下列情況丟 `RoutingError`：少於 2 個不同的點；匹配長度低於輸入折線長度的 `MIN_LENGTH_RATIO`（0.7，P1 benchmark 會調）；結果的起點或終點離輸入的起點或終點超過 `2 × search_radius_m`。`_route_result` 遇到相鄰 leg 接點相差超過 1 m 也丟 `RoutingError`，不會串出直線補線。
- 對 P1 的影響：吸附逆向或被截斷的 trace 現在會丟 `RoutingError`，P1 必須接住這個例外並設計 fallback。只在部件的兩個端點之間用 `route()` 補段會丟掉部件的形狀（例如 L 形會被直接切過去），所以 P1 必須在截斷點把部件切開（例如用 `trace_attributes` 的 matched_points 找出截斷位置），或沿部件取樣 `through` 途經點，再用 `route()` 串起來；確切策略屬於 P1 的設計決策。`MIN_LENGTH_RATIO` 要用真實 GPS 軌跡的 benchmark 調整，GPS 抖動讓匹配長度偏離輸入時可能誤殺。

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
- public 拒絕：通過，實測在 Task 1 的 smoke test（m1，測試 app `hl-internal-smoke`，用 homelab commit c9ff5be 的 `hl`，現已合併、就是 PATH 上的 `hl`），不是對 `artroute-engine` 本身跑；這次任務沒有再跑 `hl public`
  - `hl public hl-internal-smoke` exit 1，輸出 `錯誤：hl-internal-smoke 是內網 app，不能公開。要對外的話，用 hl deploy（不加 --internal）重新部署`
  - m1 的 `proxy/sites/` 沒有該 app 的 `.caddy` 路由檔
  - 內網 → 一般 → 內網的重新部署切換正常（切回內網時路由檔被移除）；`hl rm` 清理成功
  - 機制：`hl` 腳本在 `hl public` 前檢查 `zone.chen.hl.internal` label，是內網 app 就 `die`

## P0 新發現的限制
最終 review 時另外寫 probe 在合成地圖上實測出來的限制，P1 設計要把它們算進去：

- (a) `trace_route` 配 `map_snap` 不會繞路：輸入有逆向段，或點落在搜尋半徑之外，結果就是退化或被截斷（probe：穿過一個街區的對角線，只匹配到約 16%）。所以「正反兩個方向都計算」救不了一條同時穿過兩段方向相反的單行道的部件，兩個方向都會失敗
- (b) 有效半徑上限需要 `service_limits.trace.max_search_radius` 與 `meili.default.max_search_radius` 兩個都設成 150 m，細節見上方「bicycle 交通規則」一節；`write_config` 已修正，m1 現行建置要等下次重建才生效
- (c) `valhalla.Actor` 不是 thread-safe：binding 會釋放 GIL，兩個 thread 同時呼叫同一個 Actor 會 segfault／bus error（probe：macOS arm64）。`Router` 現在用 per-instance 鎖序列化呼叫，並有子行程回歸測試（8 thread × 50 次 `route()`；拿掉鎖時會崩潰）。要平行就開多個 `Router` 實例或多個行程
- (d) 快速道路避開只在合成的 `highway=motorway` 上驗證過。probe（沒有 admin DB）顯示：單純的 `highway=trunk` 對 bicycle 是**可騎**的（有替代路線時會避開，是唯一通路時會走上去）；`trunk` + `motorroad=yes`、`bicycle=no`、`motorway` 才會禁行。台灣的快速道路常標成 `trunk`，所以真實資料上的快速道路避開（例如洲美快速道路）**還沒有驗證**
- (e) bicycle 的 `turn_penalty_factor` 在 meili 預設就是 140，不是「開啟」的問題，而是要調：spec §4.3 的「開啟 `turn_penalty_factor`」需要改寫

## 結論
- P0 四項門檻全部通過：pyvalhalla on m1、台灣 tiles、bicycle 遵守單行道（合成地圖＋真實洲美街）、hl --internal
- P0 期間做的修補：test image 補 `libexpat1`；trace 退化／截斷時丟 `RoutingError` 的守門；搜尋半徑設定（兩個 key 都要調）；`Router` 用鎖序列化 Actor 呼叫
- 對設計的影響：有。spec 以下段落要在 P1 開始前修訂，與使用者一起進行：
  - §3 架構：「pyvalhalla 在同一程序內呼叫」要補上併發模型——Actor 不是 thread-safe，單一 `Router` 的呼叫是序列化的，要平行就用多個 `Router` 實例或多個行程
  - §4.2 地圖編輯器：即時吸附會遇到 trace 失敗或被截斷（`RoutingError`），要有該部件的失敗／部分成功的顯示；前端取消舊請求不會中斷引擎已經在跑的 Actor 呼叫，它仍會佔住 `Router` 的鎖（推論，未實測）
  - §4.3 部件吸附：「正反兩個方向都計算」救不了穿過相反單行道的部件，要改成在截斷點切開部件或用 `through` 途經點；`search_radius` 上限 150 m 需要兩個設定都改；`turn_penalty_factor` 預設已是 140，要改成「調整」而不是「開啟」
  - §5 錯誤處理：新增「trace 被截斷或吸附失敗」的情況和處理方式（仍然不用直線補）；吸附 5 秒的逾時要把等鎖的時間算進去（推論，未實測）
  - §6.1 路網資料更新：驗證查詢要加入快速道路（trunk）避開的真實案例（例如洲美快速道路）；熱重載要建新的 `Router` 再切換；半徑設定變更後要重建 tiles 才生效（m1 現行建置要重建）
  - §7 分期：P0 的完成條件已滿足；P1 增加三項工作——部件被截斷時的切段／途經點策略、真實資料上的快速道路避開驗證、`turn_penalty_factor` 與 `MIN_LENGTH_RATIO` 的 benchmark 調整
- P1 的參考數字：tiles 建置＋驗證＋啟用約 41 秒（含下載共 2 分 43 秒）、約 2.1 GiB RAM（重跑 `build_tiles` 量到的 anon 尖峰）、tiles 336 MiB，每次重建都可以直接全量重跑
