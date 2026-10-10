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
  - 先順向再逆向的 trace（0→2→0）：逆向那半被靜默丟掉，只回傳 0.404 km 的順向半段
  - 同樣的逆向 trace 改用 `edge_walk`：丟錯誤（`Exact route match algorithm failed to find path`）
  - 對照組：雙向街逆著騎的 trace 正常（0.404 km）
  - `search_radius` 上限是 100 m（`service_limits.trace.max_search_radius`），超過會丟 `RoutingError`
- oneway:bicycle=no：允許逆向（route 0.404 km，直接逆著騎）
- motorway：避開（route 2.101 km，繞下面那條 row 1 的住宅街）
- 對 P1 的影響：`trace()` 遇到逆向段不會報錯，只會悄悄縮短或退化成 0 km，所以 P1 的吸附必須比對 `RouteResult.length_km` 與輸入折線長度，比值過低就視為失敗，改用部件端點之間的 `route()` 補段。
