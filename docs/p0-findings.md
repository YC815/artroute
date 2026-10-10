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
- linux arm64（m1 容器）：待 Task 7

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
