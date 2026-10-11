# 手動微調路線 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 使用者能在路線上剪掉圈、重畫一段、拖曳經過點、復原／重做；微調過的路線自動存成草稿（IndexedDB），移走圖形也不會不見。

**Architecture:** 引擎新增無狀態的 `POST /edit`、`POST /verify`：請求帶整條路線與整條路線的 HMAC 簽章，引擎驗簽後只切既有的 Valhalla 幾何、新的部分只來自 Valhalla，並在新接點做嚴格轉彎檢查。網站管微調狀態、復原堆疊與草稿。設計見 `docs/superpowers/specs/2026-10-11-route-editing-design.md`（下稱「spec」）。

**Tech Stack:** engine：Python 3.13、uv、FastAPI、pyvalhalla 3.9.1、pytest。web：Next.js 16、React 19、zod 4、MapLibre、vitest、Playwright、pnpm。

## Global Constraints

- 一律繁體中文（註解、錯誤訊息、文件）。Python 只用 uv；JS 只用 pnpm（新依賴用 `pnpm add`，只動 `web/pnpm-lock.yaml`）。
- 絕不畫直線：輸出相鄰兩點必須來自同一個 Valhalla 結果（或其切片），否則相距 ≤ `JOIN_TOLERANCE_M`（1 m，`engine/src/engine/routing/router.py`）。
- 不逆向騎單行道；不走被標記的快速道路與樓梯。新的部分只能來自 `Router.route`／`Router.route_through`／現行吸附（`match_stroke`）。
- 新接點用 `rideable_strict`（`turns.py`）；沿路線離新接點 < `TURN_PROBE_M`（8 m）的既有接點用 `rideable` 重量（`join.before_neighbours`／`after_neighbours`）。
- 任何檢查沒過都回錯，**不回半成品**。
- 安全相關的測試要做到「拿掉功能會紅」：每個安全測試的 docstring 寫明拿掉哪一行會紅，審查時實際拿掉驗證。
- 引擎驗證：`cd engine && uv run pytest -q -W error`（零警告全過）。網站驗證：`cd web && pnpm exec tsc --noEmit && pnpm lint && pnpm test && pnpm build && pnpm test:e2e`。
- 契約改了要 `cd engine && uv run python -m engine.api.openapi > openapi.json`（以現有產生方式為準，見 `tests/test_openapi.py`）並 `cd web && pnpm gen:engine-types`。
- 註解只寫程式本身看不出的限制；風格比照周圍程式（繁中、句號、說明「為什麼」）。

## 相對 spec 的實作決定（寫進 spec 與主 spec §8 第 62 列）

1. **剪掉不做「繞路兩端接最短路」的退路**：`detour` 段本來就是它兩端之間的最短 route（`types.try_route` 一律 `shortest=True`），換成最短路結果相同。找不到圈就回 `nothing_to_cut`，說明改用「重畫・最短」。
2. **金鑰的建立用「暫存檔＋`os.link`」**：只用 `O_EXCL` 時，別的 worker 可能在內容寫完前讀到空檔。先寫完同目錄的暫存檔（600），再 `os.link` 到 `/data/edit-key`（目標已存在就失敗、改讀），一定讀到完整的 32 bytes。
3. **簽章也涵蓋 `length_m`**（量化到 mm）：不簽的話客戶端能改顯示與 GPX 以外用到的長度欄位。
4. **拖曳的 Valhalla 失敗一律回 `no_road`**（訊息：「放開的位置 50 公尺內沒有可騎的路，或從那裡接不回原路線」）：Valhalla 的錯誤分不出是哪一種。
5. **`/verify` 的期限 15 秒**（`/edit` 5 秒）：要對每段做 `trace_attributes`，長路線 5 秒不夠。

---

## PR C：引擎（分支 `claude/route-editing-engine`，worktree `.worktrees/edit-engine`）

### Task C1：路線簽章與金鑰

**Files:**
- Create: `engine/src/engine/edit/__init__.py`（空）、`engine/src/engine/edit/signature.py`
- Test: `engine/tests/test_edit_signature.py`

**Interfaces（Produces）:**
- `KEY_BYTES = 32`
- `load_or_create_key(path: Path) -> bytes`：檔案存在就讀（長度不是 32 丟 `ValueError`）；不存在就在同目錄寫暫存檔（`os.open(..., O_WRONLY|O_CREAT|O_EXCL, 0o600)`、`os.urandom(32)`、`fsync`），`os.link(tmp, path)`；`FileExistsError` 時改讀現有的；最後一定刪暫存檔。
- `sign_route(key: bytes, data_version: str, segments: Sequence[Segment]) -> str`：HMAC-SHA256 的 hex。
- `verify_route(key: bytes, data_version: str, segments: Sequence[Segment], sig: str) -> bool`：`hmac.compare_digest`；`sig` 不是 64 位 hex 直接 False。
- 編碼（`_encode`，確定性、不依賴 JSON 格式）：`b"artroute-route-v1\0"` + `data_version` 的 UTF-8 長度（`>I`）與內容 + 段數（`>I`），每段：kind（`stroke`=0、`detour`=1、`connector`=2、`edit`=3，`>B`）、`stroke_index`（null 編成 -1，`>i`）、`round(length_m * 1000)`（`>q`）、點數（`>I`）、每點 `round(lon * 1e7)`、`round(lat * 1e7)`（`>q` 各一）。

**Segment 的 kind 擴充:** `engine/src/engine/matching/types.py` 的 `SegmentKind` 加 `"edit"`。

- [ ] **Step 1: 寫會紅的測試**（`test_edit_signature.py`）
  - `test_sign_then_verify_round_trips`
  - `test_any_change_breaks_the_signature`（parametrize：改一個點 1e-6 度、改 kind、改 stroke_index、改 length_m 1 m、重排兩段、刪一段、重複一段、換 data_version）——docstring：拿掉 `verify_route` 的 compare（恆回 True）會紅。
  - `test_json_formatting_does_not_matter`：段落經 `json.dumps`／`json.loads`（含 `indent=2`、把 `121.5` 寫成 `1.215e2`）後仍驗得過。
  - `test_rejects_malformed_signature`（空字串、63 位、非 hex）。
  - `test_key_is_created_once_with_mode_600`（`tmp_path`）：第二次呼叫讀到同一把；`stat` 權限 `0o600`；目錄裡沒有殘留暫存檔。
  - `test_concurrent_creation_yields_one_key`：8 個 thread 同時 `load_or_create_key`，全部拿到同一把。
  - `test_wrong_length_key_file_is_an_error`。
- [ ] **Step 2:** `uv run pytest tests/test_edit_signature.py -q -W error` → FAIL（模組不存在）。
- [ ] **Step 3:** 實作 `signature.py`、`SegmentKind` 加 `"edit"`。
- [ ] **Step 4:** 跑測試 → PASS；跑全套 `uv run pytest -q -W error`。
- [ ] **Step 5:** commit `feat(engine): route signature and edit key`

### Task C2：`Router.route_through` 與路線上的位置

**Files:**
- Modify: `engine/src/engine/routing/router.py`（新增方法）
- Create: `engine/src/engine/edit/position.py`
- Test: `engine/tests/test_edit_position.py`、`engine/tests/test_router_unit.py`（加 route_through 的請求格式測試，比照該檔既有的假 Actor 寫法）

**Interfaces（Produces）:**
- `Router.route_through(self, start: LonLat, via: LonLat, end: LonLat, *, shortest: bool = False) -> RouteResult`：三個 location，via 是 `{"type": "through", "search_cutoff": DRAG_SNAP_M}`（`DRAG_SNAP_M = 50.0` 定義在 router.py），起訖同 `route`（`node_snap_tolerance` 0）。回傳的點串接方式同 `_route_result`（leg 間缺口 > 1 m 丟 `RoutingError`）。
- `edit/position.py`：
  - `@dataclass(frozen=True) class RoutePos: segment: int; offset_m: float`
  - `@dataclass(frozen=True) class Cut: segment: int; index: int; point: LonLat`：切點在 `segments[segment].points[index]` 與 `[index + 1]` 之間（含落在頂點上：`point == points[index]`）。`point` 由線性內插得到，一定在原本的折線上。
  - `resolve(segments, pos: RoutePos) -> Cut`：`segment` 越界、`offset_m` 為負或超過該段幾何長度 + 1 m 丟 `NotOnRoute`（`class NotOnRoute(ValueError)`）。超過長度但 ≤ 1 m 夾到終點。距離用 `haversine_m` 累積（與 `polyline_length_m` 同一把尺）。
  - `arc_m(segments, pos: RoutePos) -> float`：從路線起點量到該位置的公尺數（前面各段的幾何長度 + offset）。
  - `pos_at_arc(segments, arc: float) -> RoutePos`：反函數，夾在 [0, 總長]。
  - `order(segments, a: RoutePos, b: RoutePos) -> tuple[RoutePos, RoutePos]`：依 `arc_m` 排先後。
  - `split(segments, start: Cut, end: Cut) -> tuple[list[Segment], list[Segment], list[Segment]]`：回傳 (保留的前段, 被換掉的中間, 保留的後段)。切開的段落用 `join.cut` 產生（點全是原本的點，加上內插的切點）；不到 2 個不同點的片段丟掉。`start` 必須在 `end` 之前（同段時 index／point 先後），否則 `ValueError`。

- [ ] **Step 1: 寫會紅的測試**：`resolve` 在段中、頂點上、段尾、越界；`arc_m`／`pos_at_arc` 互為反函數（多段、含零長度段）；`split` 的三段首尾相接（haversine ≤ 1e-6 m）、點都是原本的點或切點、切點落在原折線上（到原折線距離 < 1e-6 m）；`order` 交換順序；同段切兩刀。`route_through` 的請求：via 帶 `type: through` 與 `search_cutoff: 50`。
- [ ] **Step 2:** 跑 → FAIL。
- [ ] **Step 3:** 實作。
- [ ] **Step 4:** 跑 → PASS、全套。
- [ ] **Step 5:** commit `feat(engine): route positions and Router.route_through`

### Task C3：拼接與安全檢查（`splice`）

**Files:**
- Create: `engine/src/engine/edit/splice.py`
- Test: `engine/tests/test_edit_splice.py`（純函式＋假 router：比照 `tests/turn_router.py`／`tests/test_join.py` 的假 router）

**Interfaces:**
- Consumes：C2 的 `Cut`、`split`；`join.junction_ok`、`before_neighbours`、`after_neighbours`；`service._check_continuous`（改名公開為 `check_continuous`，舊名保留為別名或改所有呼叫處）。
- Produces：
  - `class EditRejected(Exception)`：`code: Literal["not_on_route", "nothing_to_cut", "no_route", "turn_not_rideable", "no_road"]`、`message: str`（繁中，給使用者看，附可以改用哪個操作）。
  - `splice(router, deadline, before: list[Segment], middle: list[Segment], after: list[Segment]) -> list[Segment]`：`[*before, *middle, *after]`，檢查：
    1. 兩個新接點（`before[-1]|middle[0]`、`middle[-1]|after[0]`；`before` 或 `after` 空時那一側沒有接點）相距 ≤ `JOIN_TOLERANCE_M`，否則 `EditRejected("no_route")`。
    2. 新接點 `junction_ok(..., strict=True)`；沿路線 < 8 m 的既有接點 `junction_ok(..., strict=False)`；任何一個沒過 → `EditRejected("turn_not_rideable")`。
    3. `check_continuous`（`DiscontinuousRoute` 是程式錯誤，照樣往外丟）。
    4. 結果沒有任何有長度的段 → `EditRejected("nothing_to_cut")`。
  - `edit_segment(result: RouteResult, stroke_index: int | None) -> Segment`：`kind="edit"`。
  - `replaced_stroke(middle: list[Segment]) -> int | None`：被換掉範圍的 `stroke_index` 全部相同（忽略 connector）就回它，否則 None。
  - `route_between(router, deadline, before, after, start: Cut, end: Cut, segments, *, via: LonLat | None = None) -> RouteResult`：在切點之間規劃（`via` 有值用 `route_through`），端點必須各自 ≤ 1 m 落在要求的點上。不行時依序換用切點前後的頂點：起點候選 `[start.point, points[start.index], points[start.index + 1]]`、終點候選 `[end.point, points[end.index + 1], points[end.index]]`（去重、保持「起點在終點之前」），逐對嘗試，最多 9 次；每次前 `deadline.check()`。換用頂點時 `before`／`after` 的切法跟著改（回傳實際用的 `Cut`）。全部失敗 → `EditRejected("no_route")`（拖曳由呼叫端改成 `no_road`）。回傳 `(RouteResult, start_cut, end_cut)`。

- [ ] **Step 1: 寫會紅的測試**
  - `test_gap_at_a_new_junction_is_rejected`（middle 起點離 before 終點 3 m）——拿掉檢查 1 會紅。
  - `test_new_junction_uses_strict_turn_check`：假 router 讓 0° 的接點 route 繞遠 → `turn_not_rideable`（非嚴格版會放行 0°，所以改成 `strict=False` 會紅）。
  - `test_existing_junction_within_8m_is_rechecked`。
  - `test_output_is_continuous_and_reuses_original_points`。
  - `test_route_between_falls_back_to_neighbour_vertices`：假 router 對內插點回傳吸到 5 m 外的結果、對頂點回傳精確結果。
  - `test_route_between_gives_up_after_nine_tries`。
- [ ] **Step 2–4:** FAIL → 實作 → PASS、全套。
- [ ] **Step 5:** commit `feat(engine): splice with strict junction checks`

### Task C4：三個操作（剪掉、重畫、拖曳）

**Files:**
- Create: `engine/src/engine/edit/ops.py`
- Modify: `engine/src/engine/matching/prune.py`（把 `_candidates`、`_cut_loop`、`_turns_ok` 公開成 `loop_candidates`、`cut_loop`、`turns_ok`，原名的呼叫處一起改；行為不變）
- Modify: `engine/tests/osm_fixture.py`（需要的話加場景：街廓圈、只能靠繞圈迴轉的單行道、平行路）
- Test: `engine/tests/test_edit_ops.py`（`@pytest.mark.valhalla`，合成地圖，比照 `test_prune_grid.py`）

**Interfaces:**
- Consumes：C1–C3、`stroke.match_stroke`、`join.connect`、`service.default_search_radius`、`service.StrokeInput`。
- Produces（全部回傳新的 `list[Segment]`，失敗丟 `EditRejected`）：
  - `cut_at(router, deadline, segments, at: RoutePos) -> list[Segment]`：`flat(segments)` 上用 `loop_candidates(points, max_m=math.inf)` 找圈，只留「`at` 的 arc 位置嚴格落在圈內」的，依長度由短到長；每個用 `cut_loop` 剪，`turns_ok(router, deadline, pruned, created=set())` 通過就回傳；有圈但全部轉彎沒過 → `turn_not_rideable`；沒有圈 → `nothing_to_cut`（訊息：「這裡沒有可以剪掉的圈，可以改用「重畫・最短」把這段換掉」）。不套用圈長上限、不比 v3、不限次數（期限照算）。
  - `reroute(router, deadline, segments, a: RoutePos, b: RoutePos, mode: Literal["shortest", "shape"], strokes: list[StrokeInput]) -> list[Segment]`：
    - 先 `order`；兩點的 arc 差 < 2 m → `no_route`（「兩個點太近」）。
    - shortest：`route_between`（`shortest=True` 的 route）→ `edit_segment(…, replaced_stroke(middle))` → `splice`。
    - shape：兩個位置所在段的 `stroke_index` 必須相同且不是 None，否則 `no_route`（「兩個點不在同一個筆畫上，請改用「最短」」）。把兩個切點投影到該筆畫的目標（`StrokeInput` 的點，封閉筆畫含收尾）上取參數 t_a、t_b；開放筆畫取 [min, max] 的子折線、依 t_a < t_b 決定方向（t_a > t_b 時倒過來）；封閉筆畫取從 t_a 往前走到 t_b 的那一段。目標點列 = `[start.point, *子折線, end.point]`，`match_stroke(router, 目標, stroke_index=…, search_radius_m=default_search_radius([該筆畫]), min_piece_m=2*radius, deadline=…)`；結果不是剛好一條 chain 或有 warnings → `no_route`。chain 的段 kind 改成 `"edit"`（保留 stroke_index）。接回用 `join.connect(router, deadline, before, chain, "edit", idx)`，再 `connect(結果, after, "edit", idx)`，任一回 None → `no_route`；最後仍走 `splice` 的 1–4 項檢查（`splice(router, deadline, [], 全部, [])` 只做連續性；新接點的嚴格檢查改成：對 connect 產生的每個接點與兩個原始切點的接點呼叫 `junction_ok(strict=True)`——實作時把「哪些索引是新接點」算出來，並寫測試證明拿掉會紅）。
  - `drag(router, deadline, segments, grab: RoutePos, to: LonLat) -> list[Segment]`：`s = arc_m(grab)`；範圍 `[max(0, s − DRAG_SPAN_M), min(總長, s + DRAG_SPAN_M)]`（`DRAG_SPAN_M = 200.0`）；`route_between(..., via=to)`；`to` 到結果折線的距離 > `DRAG_SNAP_M` 或任何 `RoutingError`／`no_route` → `EditRejected("no_road", …)`（訊息見上方決定 4）；成功 → `splice`。

- [ ] **Step 1: 寫會紅的合成地圖測試**（spec §6.2，每個場景驗一件事）：
  - 剪掉：直線旁的街廓圈被剪掉；圈長超過外框 15% 也剪；只能靠繞圈迴轉的單行道不剪、回 `turn_not_rideable`；點在沒有圈的地方回 `nothing_to_cut`；點在兩個巢狀圈的內圈時剪內圈。
  - 重畫：最短模式不逆向騎單行道（`oneway=yes`、`oneway=-1` 各一）；貼圖形模式沿目標的路（結果到目標的平均距離 < 最短模式）；兩點反著點結果相同；跨筆畫的貼圖形回 `no_route`。
  - 拖曳：拖到平行路，結果經過放開位置 10 m 內，且 200 m 範圍外的點與原路線完全相同；拖到離路 > 50 m 的空地回 `no_road`。
  - 共通（parametrize 所有成功案例）：相鄰點 ≤ 1 m 或來自同一段；新段 kind 是 `"edit"`；`check_continuous` 通過；每個新接點 `junction_ok(strict=True)`。
- [ ] **Step 2–4:** FAIL → 實作 → PASS、全套。
- [ ] **Step 5:** commit `feat(engine): cut, reroute and drag edits`

### Task C5：`/edit` 端點、`route_sig`、契約

**Files:**
- Modify: `engine/src/engine/api/models.py`、`engine/src/engine/app.py`
- Modify: `engine/openapi.json`（重新產生）、`web/src/lib/engine/openapi.d.ts`（`pnpm gen:engine-types`）
- Modify: `docs/superpowers/specs/2026-10-10-strava-art-design.md` §4.7（新增「手動微調新增」一節）
- Test: `engine/tests/test_api_edit.py`（比照 `test_api_match.py` 的 `create_app` ＋假 router／合成地圖）

**Interfaces:**
- models：
  - `SegmentOut.kind` 加 `"edit"`；`MatchResponse` 加 `route_sig: str`。
  - `RoutePosIn(BaseModel)`：`segment: int = Field(ge=0)`、`offset_m: Finite = Field(ge=0)`。
  - `CutOp`（`type: Literal["cut"]`、`at`）、`RerouteOp`（`type: Literal["reroute"]`、`from_: RoutePosIn = Field(alias="from")`、`to: RoutePosIn`、`mode: Literal["shortest", "shape"]`）、`DragOp`（`type: Literal["drag"]`、`grab`、`to: LonLatPair`）；`EditOp = Annotated[CutOp | RerouteOp | DragOp, Field(discriminator="type")]`。
  - `SegmentIn`：同 `SegmentOut`，`points` 長度 2–20000；`EditRequest`：`segments: list[SegmentIn]`（1–2000）、`data_version: str`（≤ 64）、`route_sig: str`（≤ 128）、`strokes: list[StrokeIn]`（同 `MatchRequest`）、`op: EditOp`。
  - `ErrorDetail.code` 加 `"stale_data"`、`"invalid_route"`、`"not_on_route"`、`"nothing_to_cut"`、`"turn_not_rideable"`、`"no_road"`、`"verify_failed"`；加 `segments: list[int] | None = None`（只有 verify_failed 帶）。
- app：
  - `create_app(..., edit_key_path: Path | None = None, verify_timeout_s: float = 15.0)`；預設 `data_root / "edit-key"`；在 `create_app` 裡 `load_or_create_key`（啟動時就建好）。
  - `_response(result, version, key)` 一律附 `route_sig = sign_route(key, version, result.segments)`；`/search` 的候選同樣簽。
  - `POST /edit`：共用 `admission` 與 `Deadline(match_timeout_s)`、斷線取消（比照 `/match`）。順序：取 current router → `data_version != version` → 409 `stale_data`；`verify_route` 失敗 → 422 `invalid_route`；做操作；`EditRejected` → 422 帶它的 code／message；`MatchTimeout` → 504；成功 → `MatchResponse`（分數用 `score(targets, route)`，`target_length_m` 同 `/match`，`warnings=[]`，新簽章）。
  - responses 文件：400／409／422／503／504。
- 契約測試：`tests/test_openapi.py` 照現有方式更新；web 的 `openapi-sync.test.ts` 要過。

- [ ] **Step 1: 寫會紅的 API 測試**：`/match` 回應帶 `route_sig` 且驗得過；`/edit` 舊版本 → 409；改一個點 → 422 `invalid_route`（拿掉驗簽會紅）；成功的 cut 回 200、新簽章能再拿來 `/edit` 一次；`EditRejected` 對應 422 code；慢的假 router → 504、不回半成品；金鑰檔在第一次 `create_app` 後存在且權限 600；請求格式錯誤（`op.type` 未知）→ 422 陣列。
- [ ] **Step 2–4:** FAIL → 實作 → PASS、全套；重新產生 openapi.json 與 web 型別，跑 `cd web && pnpm test src/lib/engine`。
- [ ] **Step 5:** commit `feat(engine): POST /edit and route_sig`

### Task C6：`/verify` 與共用的安全檢查

**Files:**
- Move: `engine/src/engine/bench/replay.py` → `engine/src/engine/matching/replay.py`（bench 改 import；`tests/test_replay.py` 改 import）
- Create: `engine/src/engine/matching/safety.py`：從 `bench/runner.py` 把 `_edges`、`_shifted` 與 `safety_problem` 的逐段檢查搬過來成 `unsafe_segments(router, segments, denied_ids) -> list[int] | None`（回傳有問題的段落索引；`None` 表示全部通過）；`safety_problem` 改成呼叫共用的函式，bench 的行為與輸出字串不變（`tests/test_bench.py` 全過證明）。判斷不了（`wrong_way is None`、配不出路段）算有問題。
- Modify: `engine/src/engine/app.py`、`models.py`
- Test: `engine/tests/test_api_verify.py`、`engine/tests/osm_fixture.py`（第二張合成地圖：同一張圖把一條路改成 `oneway=yes`）

**Interfaces:**
- `VerifyRequest`：`segments`、`data_version`、`route_sig`、`strokes`。
- `POST /verify`（期限 `verify_timeout_s`，共用 admission）：驗簽（用請求的 `data_version`）失敗 → 422 `invalid_route`；版本 == current → 直接回傳（分數重算、簽章不變）；不同 → 在 current router 上 `unsafe_segments`（denied ids 用 `engine.data.build.load_denied(build)`，每個 build 快取一次）＋連續性＋每個接點 `junction_ok(strict=False)`；全部通過 → 以 current 版本重新簽章回傳；沒過 → 422 `verify_failed`，`segments` 是有問題的段落索引（接點有問題時兩側都列）。

- [ ] **Step 1: 寫會紅的測試**：同版本直接通過；在「改成單行」的 build 上逆向那段被列出（拿掉 `unsafe_segments` 的逆向判斷會紅）；沒改到的路線重新簽章後能 `/edit`；簽章不對 → 422；bench 的 `safety_problem` 既有測試全過。
- [ ] **Step 2–4:** FAIL → 實作 → PASS、全套；重新產生 openapi.json 與 web 型別。
- [ ] **Step 5:** commit `feat(engine): POST /verify with shared safety checks`

### Task C7：文件

- spec：§3.3 剪掉的退路、§3.4 金鑰建立、簽章含 length_m、拖曳錯誤、`/verify` 期限（上方「相對 spec 的實作決定」）。主 spec §8 加第 62 列。`docs/handoff.md` 目前狀態加 PR C；PR 說明的「本機待辦」：部署、`heart@taipei_daan` 剪右側繞路的時間、每個 bench 案例拖曳一次、用 m1 的兩個 build 驗 `/verify`。
- commit `docs: route editing engine decisions`

---

## PR D：網站（分支 `claude/route-editing-web`，worktree `.worktrees/edit-web`）

D1、D2 不依賴引擎契約，可以與 PR C 平行。D3 起要先 `git merge claude/route-editing-engine`。

### Task D1：草稿 store（IndexedDB）

**Files:**
- Create: `web/src/lib/storage/drafts.ts`、`web/src/lib/storage/drafts.test.ts`
- Modify: `web/package.json`（`pnpm add -D fake-indexeddb`）

**Interfaces（Produces）:**
- `DRAFT_LIMIT = 20`、`UNOPENED_TTL_MS = 7 * 24 * 3600 * 1000`
- `type DraftRoute = { segments: Segment[]; dataVersion: string; routeSig: string; score: Score; lengthM: number }`（`Segment`、`Score` 型別先在本檔用 zod 定義：`kind` 含 `"edit"`；D3 合併契約後改成引用 `contract.ts`）
- `type Draft = { v: 1; id: string; createdAt: number; updatedAt: number; openedAt: number | null; source: SavedSource; transform: Transform; route: DraftRoute }`
- `openDraftDb(factory?: IDBFactory): Promise<DraftDb>`；`DraftDb` 方法：
  - `list(): Promise<Draft[]>`（zod 驗證；壞掉的跳過但不刪；依 `updatedAt` 新到舊）
  - `get(id)`、`put(draft)`（不改時間）、`remove(id)`
  - `create(input: { source; transform; route }, now: number): Promise<{ draft: Draft; overLimit: boolean }>`：存之前若已經 ≥ 20 份，刪 `openedAt === null` 中 `updatedAt` 最舊的一份；沒有可刪的就照樣存並回 `overLimit: true`。
  - `update(id, route, now)`：換 route、`updatedAt = now`。
  - `markOpened(id, now)`。
  - `sweep(now): Promise<number>`：刪 `openedAt === null && now − updatedAt > UNOPENED_TTL_MS`，回傳刪了幾份。
- `requestPersist(): Promise<void>`：有 `navigator.storage?.persist` 就呼叫，錯誤吞掉。
- `DRAFTS_CHANNEL = "artroute:drafts"`；`notifyDraftsChanged()`／`onDraftsChanged(cb): () => void`（`BroadcastChannel`，不存在時 no-op）。
- 資料庫 `artroute`、版本 1、store `drafts`（keyPath `id`）。`id` 用 `crypto.randomUUID()`。

- [ ] **Step 1: 寫會紅的測試**（`fake-indexeddb` 的 `IDBFactory`，每個測試一個新 factory）：建立／更新／打開；7 天刪除只刪未打開的（6.9 天不刪、7.1 天刪、打開過的 30 天也不刪）；上限 20 刪最舊的未打開；全部打開過時第 21 份照存且 `overLimit: true`；壞掉的紀錄 `list` 跳過但仍在 store；`requestPersist` 在沒有 API 時不丟錯。
- [ ] **Step 2–4:** FAIL → 實作 → PASS；`pnpm exec tsc --noEmit && pnpm lint && pnpm test`。
- [ ] **Step 5:** commit `feat(web): IndexedDB draft store`

### Task D2：微調歷史（復原／重做）

**Files:**
- Create: `web/src/lib/edit/history.ts`、`web/src/lib/edit/history.test.ts`

**Interfaces（Produces）:** 泛型、純函式：
- `EDIT_HISTORY_LIMIT = 50`
- `type History<T> = { past: T[]; present: T; future: T[] }`
- `startHistory<T>(present: T): History<T>`、`pushHistory(h, next)`（`past` 最多 50，超過丟最舊；清空 `future`）、`undo(h)`、`redo(h)`（沒得退／進時回傳同一個物件）、`canUndo(h)`、`canRedo(h)`。

- [ ] 測試：50 步上限、新編輯清掉重做、undo／redo 邊界回傳同一物件；實作；`pnpm test`；commit `feat(web): edit history`

### Task D3：契約、代理與用戶端

**Files:**
- Merge `claude/route-editing-engine`。
- Modify: `web/src/lib/engine/contract.ts`（`segment.kind` 加 `"edit"`、`MatchResponse` 加 `route_sig`、`EditRequest`／`VerifyRequest` 型別與 zod、錯誤碼）、`web/src/lib/engine/client.ts`（`postEdit`、`postVerify`，錯誤型別帶 `code`、`segments`）
- Create: `web/src/app/api/edit/route.ts`、`web/src/app/api/verify/route.ts`（比照 `api/match/route.ts` 與 `lib/engine/proxy.ts`：同樣的 body 上限與 zod 回應驗證；`/verify` 的逾時 16 s）
- Modify: `web/src/lib/storage/drafts.ts`（改引用契約的型別）
- Test: 對應的 `*.test.ts`（比照 `api/match` 的測試）

- [ ] 測試：zod 接受 `kind: "edit"`、拒絕缺 `route_sig`；代理轉發 409／422 的 body；`openapi-sync.test.ts` 過。實作、驗證、commit `feat(web): edit and verify contract`

### Task D4：微調模式（狀態、地圖互動、操作）

**Files:**
- Create: `web/src/hooks/use-route-edit.ts`（微調狀態：`mode: "place" | "edit"`、`History<EditedRoute>`、正在選的操作、等待中的請求、錯誤訊息；呼叫 `postEdit`，`AbortController` 取消）
- Create: `web/src/components/studio/edit-toolbar.tsx`（shadcn toggle group：剪掉／重畫／拖曳；重畫第二步選「最短」「貼圖形」；復原、重做、完成）
- Create: `web/src/lib/map/route-pick.ts`（純函式：地圖點擊的經緯度 → 最近的 `{segment, offset_m}`，只在 12 px（換算成公尺）內才算點到；回傳該段與偏移量；測試）
- Modify: `web/src/components/map/route-layers.ts`（`kind === "edit"` 的段用虛線外框；選到的點、拖曳把手）、`map-view.tsx`（點擊、拖曳事件往上傳）
- Modify: `web/src/components/studio/studio.tsx`、`info-panel.tsx`（「微調路線」按鈕；微調中 GPX 用微調後的路線；`downloadable` 在微調／草稿時以微調路線為準）
- Modify: `web/src/hooks/use-live-match.ts` 或 `lib/match/scheduler.ts`：`edit` 模式不送請求、取消在途的請求、遲到的結果丟掉（測試：`edit` 模式下 fine 回來不覆蓋）
- Modify: `web/src/lib/studio/hotkeys.ts`：⌘/Ctrl+Z、Shift+⌘/Ctrl+Z、Esc（打字的地方除外，比照 `isToggleTargetKey`）

**行為（spec §2）：** 進入時圖形鎖住（`ShapeOverlay` 隱藏把手、不接收拖曳）；第一次成功的編輯建立草稿（D1 `create`），之後每次 `update`；`overLimit` 時顯示提示；引擎拒絕時保留原路線並顯示 message；409 時提示「路網已更新」並提供「重新驗證」（D5）。「完成」離開微調但畫面保留微調路線，直到使用者再移動圖形（移動後回到即時吸附的路線）。

- [ ] 單元測試：`route-pick`、`use-route-edit` 的狀態轉移（以假的 `postEdit`）、`edit` 模式不送吸附、GPX 用微調後路線、快捷鍵判斷。實作、全套驗證、commit `feat(web): route edit mode`

### Task D5：我的路線、打開草稿、舊路網

**Files:**
- Create: `web/src/components/studio/drafts-panel.tsx`（列表：縮圖（路線點畫成 SVG polyline）、名稱（`gpxName(source)`）、公里數、最後修改、路網日期；打開；刪除要確認；超過上限的提示）
- Create: `web/src/hooks/use-drafts.ts`（`openDraftDb`、載入時 `sweep(Date.now())`、`onDraftsChanged` 重新讀取）
- Modify: `studio.tsx`：打開草稿 → 還原來源、擺放、路線，進入微調、`markOpened`；草稿的 `dataVersion` ≠ 引擎目前版本時顯示「這是 X 月 X 日路網上的路線」、可下載 GPX、要編輯先「重新驗證」（`postVerify`；通過就 `update` 成新版本；`verify_failed` 時把 `segments` 列出的段標紅並提供「回到自動吸附」）

- [ ] 單元測試：`use-drafts` 載入時 sweep、BroadcastChannel 觸發重新讀取；舊路網判斷；縮圖的 SVG path 產生（純函式）。實作、全套驗證、commit `feat(web): drafts panel and re-verify`

### Task D6：e2e 與文件

**Files:**
- Modify: `web/e2e/smoke.spec.ts`（或新檔 `web/e2e/edit.spec.ts`，假引擎比照現有 e2e 的 route mock）：進入微調 → 剪掉 → 復原 → 重做 → 完成 → 移動圖形 → 「我的路線」有這份草稿 → 打開 → 路線與擺放還原。
- docs：handoff 目前狀態、本機待辦（部署順序：引擎先、網站後；使用者實際操作一次再調 200 m 與錯誤說明）。

- [ ] 跑網站全套驗證；commit `test(web): route editing e2e`
