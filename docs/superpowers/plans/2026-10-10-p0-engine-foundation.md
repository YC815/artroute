# P0：引擎地基與可行性驗證 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 m1 上跑起只在內網、載入台灣路網的 `artroute-engine`，並用自動化測試證明 Valhalla 的 `bicycle` 會遵守單行道、避開快速道路。

**Architecture:** 先幫 hl 加上 `--internal` 部署模式（不對外路由）。引擎是 Python + FastAPI 專案，透過 pyvalhalla 在同一個程序裡呼叫 Valhalla。路網建置流程是：下載 Geofabrik 台灣 pbf → 建 tiles 到 `/data/builds/<id>` → 跑驗證查詢 → 原子切換 `/data/current`。單行道行為用 pyosmium 產生的合成地圖來測，結果固定、可重現。

**Tech Stack:** bash（hl）、Python 3.13、uv、FastAPI、pyvalhalla 3.9.1、httpx、pytest、osmium（pyosmium）、Docker（OrbStack on m1，linux arm64）。

**對應設計文件：** `docs/superpowers/specs/2026-10-10-strava-art-design.md` 的 §3（部署）、§4.3（吸附的 Valhalla 呼叫）、§6.1（資料更新）、§7 P0。

## Global Constraints

- Python 一律用 uv：`uv add`、`uv run`；不用 pip、不用系統 python。
- Python 版本 3.13（pyvalhalla 的 abi3 wheel 需要 ≥ 3.12）。
- pyvalhalla 版本釘在 `3.9.1`。
- 程式碼註解、使用者看得到的訊息一律用繁體中文；識別字用英文。
- 不在前景跑不會結束的指令（dev server、`hl logs -f`、`--watch`）。
- `hl rm`、`hl public` 只能對本計畫自己建立的 app 使用。
- 引擎絕不輸出直線補線；吸附失敗時明確報錯。
- 交通方式固定為 `bicycle`。
- StravaArt 的 commit 用 `<type>: <繁中描述>`；homelab repo 沿用它既有的英文 commit 風格。每個 commit 結尾都加：
  ```
  Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01Kg6bv9JV9xTSx6n6doWzzc
  ```

## File Structure

**homelab repo（`~/homelab`）**
- Modify `lib/hl-lib.sh`：`hl_render_compose` 增加 internal label；新增 `hl_parse_deploy_args`。
- Modify `bin/hl`：`deploy --internal`、內網健康檢查、`ls` 顯示 internal、`public` 拒絕內網 app。
- Modify `tests/test-hl-lib.sh`：新增上述純函式的測試。
- Modify `~/.claude/skills/homelab-deploy/SKILL.md`：記錄 `--internal`。

**StravaArt repo**
```
.gitignore
engine/
  pyproject.toml, uv.lock, Dockerfile, .dockerignore
  src/engine/
    __init__.py
    app.py                 FastAPI：/、/health、/status
    routing/
      __init__.py
      polyline.py          polyline 編碼與解碼
      tiles.py             產生 Valhalla 設定、建 tiles
      router.py            Router：trace / route / edges（包裝 valhalla.Actor）
    data/
      __init__.py
      layout.py            /data 目錄結構、原子切換、清舊版
      download.py          下載並驗證 md5
      pipeline.py          建置 → 驗證 → 啟用
      checks.py            台灣真實資料的驗證查詢
      cli.py               python -m engine.data.cli
  tests/
    conftest.py            合成地圖的 session fixture
    osm_fixture.py         用 pyosmium 產生合成地圖 pbf
    test_app.py
    test_valhalla_available.py
    test_polyline.py
    test_tiles.py
    test_router_bicycle_rules.py
    test_layout.py
    test_download.py
    test_pipeline.py
    test_checks.py
docs/p0-findings.md        P0 驗證結果
```

---

### Task 1: hl 加上 `--internal` 部署模式

**Files:**
- Modify: `~/homelab/lib/hl-lib.sh`（`hl_render_compose`，並新增 `hl_parse_deploy_args`）
- Modify: `~/homelab/bin/hl`（`usage`、`cmd_deploy`、`cmd_ls`、`cmd_public`，新增 `INTERNAL_HEALTH_SCRIPT` 與 `internal_health_check`）
- Test: `~/homelab/tests/test-hl-lib.sh`
- Modify: `~/.claude/skills/homelab-deploy/SKILL.md`

**Interfaces:**
- Produces:
  - `hl deploy [name] [--internal]`
  - 內網 app 的 compose 帶有 label `zone.chen.hl.internal: "1"`
  - 內網 app 只能從 hl Docker 網路以 `http://<name>:8080` 連到
- `hl_render_compose NAME [INTERNAL]`：`INTERNAL` 為 `1` 時加上 internal label。
- `hl_parse_deploy_args ARGS…`：設定全域變數 `HL_DEPLOY_NAME`、`HL_DEPLOY_INTERNAL`（`0` 或 `1`）；參數不合法時回傳 1。

- [ ] **Step 1：寫失敗的測試**

在 `~/homelab/tests/test-hl-lib.sh` 的 `echo "# hl_render_site"` 那一行之前插入：

```bash
echo "# hl_render_compose（內網）"
internal="$(hl_render_compose eng 1)"
assert_ok "內網 app 有 internal label" grep -qxF '      zone.chen.hl.internal: "1"' <<<"$internal"
assert_ok "內網 app 仍有 hl label" grep -qxF '      zone.chen.hl: "1"' <<<"$internal"
assert_ok "內網 app 仍掛在 network hl" grep -qxF '    networks: [hl]' <<<"$internal"
assert_fail "一般 app 沒有 internal label" grep -qF 'zone.chen.hl.internal' <<<"$compose"
assert_eq "$(hl_render_compose eng 0)" "$(hl_render_compose eng)" "第二個參數 0 等同省略"

echo "# hl_parse_deploy_args"
hl_parse_deploy_args eng --internal
assert_eq "$HL_DEPLOY_NAME $HL_DEPLOY_INTERNAL" "eng 1" "名稱＋--internal"
hl_parse_deploy_args --internal eng
assert_eq "$HL_DEPLOY_NAME $HL_DEPLOY_INTERNAL" "eng 1" "旗標順序不拘"
hl_parse_deploy_args eng
assert_eq "$HL_DEPLOY_NAME $HL_DEPLOY_INTERNAL" "eng 0" "預設不是內網"
parse_tmp="$(mktemp -d)"
mkdir "$parse_tmp/myapp"
assert_eq "$(cd "$parse_tmp/myapp" && hl_parse_deploy_args --internal && echo "$HL_DEPLOY_NAME $HL_DEPLOY_INTERNAL")" "myapp 1" "沒給名稱時用資料夾名稱"
rm -rf "$parse_tmp"
assert_fail "兩個名稱不合法" hl_parse_deploy_args a b
assert_fail "未知旗標不合法" hl_parse_deploy_args --nope
```

- [ ] **Step 2：跑測試，確認失敗**

Run: `bash ~/homelab/tests/test-hl-lib.sh`
Expected：結尾是 `N 項失敗`，至少包含 `FAIL 內網 app 有 internal label`，以及 `hl_parse_deploy_args: command not found` 造成的失敗。

- [ ] **Step 3：實作 lib 函式**

把 `~/homelab/lib/hl-lib.sh` 的 `hl_render_compose` 整個換成下面這段，並在它後面新增 `hl_parse_deploy_args`：

```bash
# 呼叫端必須先用 hl_validate_name 驗證名稱。
# service 以 app 名稱命名：network hl 上唯一的 DNS alias 就是這個名稱，不會跟別的 app 或 proxy 撞名
# 第二個參數是 1 代表內網 app：只掛在 network hl 上，不建對外路由（label 讓 hl ls/public 認得）
hl_render_compose() {
  local internal_label=""
  if [[ "${2:-0}" == 1 ]]; then
    internal_label=$'\n      zone.chen.hl.internal: "1"'
  fi
  cat <<EOF
services:
  $1:
    build: ./src
    container_name: $1
    restart: unless-stopped
    env_file: .env
    environment:
      PORT: "$HL_APP_PORT"
    volumes:
      - ./data:/data
    networks: [$HL_NETWORK]
    labels:
      zone.chen.hl: "1"$internal_label
networks:
  $HL_NETWORK:
    external: true
EOF
}

# hl deploy 的參數 → 設定 HL_DEPLOY_NAME（預設資料夾名稱）與 HL_DEPLOY_INTERNAL（0/1）
# shellcheck disable=SC2034  # 兩個變數由 bin/hl 使用
hl_parse_deploy_args() {
  HL_DEPLOY_NAME=""
  HL_DEPLOY_INTERNAL=0
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --internal) HL_DEPLOY_INTERNAL=1 ;;
      -*) echo "不認得的參數：$1" >&2; return 1 ;;
      *)
        if [[ -n "$HL_DEPLOY_NAME" ]]; then
          echo "只能指定一個名稱" >&2
          return 1
        fi
        HL_DEPLOY_NAME="$1" ;;
    esac
    shift
  done
  if [[ -z "$HL_DEPLOY_NAME" ]]; then HL_DEPLOY_NAME="$(basename "$PWD")"; fi
}
```

- [ ] **Step 4：跑測試，確認通過**

Run: `bash ~/homelab/tests/run.sh`
Expected：每個測試檔都以 `全部通過` 結尾。

- [ ] **Step 5：實作 `bin/hl`**

(a) 在 `usage()` 裡，把 `hl deploy [name]` 那一行換成：

```
  hl deploy [name] [--internal]    部署（name 預設是資料夾名稱；--internal 只在 hl 網路內、不對外）
```

(b) 在 `health_check()` 函式後面新增：

```bash
# 內網 app：從 hl-proxy 容器裡打 http://NAME:PORT/，有任何 HTTP 回應就算活著（跟外部檢查一樣，只排除連不上）
# busybox wget 收到 4xx/5xx 也會印出含「HTTP/」的狀態行；連不上時只會印 can't connect
# shellcheck disable=SC2016  # 這段在遠端執行，$ 要留給遠端展開
INTERNAL_HEALTH_SCRIPT='end=$((SECONDS + 60))
while [ "$SECONDS" -lt "$end" ]; do
  if docker exec "$1" wget -S -q --spider -T 5 "http://$2:$3/" 2>&1 | grep -q "HTTP/"; then exit 0; fi
  sleep 2
done
exit 1'

internal_health_check() {
  local name="$1"
  # shellcheck disable=SC2029  # 參數刻意在本機展開
  if printf '%s\n%s\n' "$HL_REMOTE_PRELUDE" "$INTERNAL_HEALTH_SCRIPT" \
    | ssh "${HL_SSH_OPTS[@]}" "$HL_REMOTE" "bash -s -- $HL_PROXY_CONTAINER $name $HL_APP_PORT"; then
    echo "  內網回應正常"
  else
    echo "60 秒內在內網連不到 http://$name:$HL_APP_PORT/。最後 50 行 log：" >&2
    hl_remote "cd $(app_dir "$name") && docker compose -p hl-$name logs --tail 50" >&2 || true
    exit 1
  fi
}
```

(c) 把 `cmd_deploy()` 整個換成：

```bash
cmd_deploy() {
  local name dir host label public_id
  hl_parse_deploy_args "$@" || die "用法：hl deploy [name] [--internal]"
  name="$HL_DEPLOY_NAME"
  hl_validate_name "$name" || die "用 hl deploy <name> 指定一個合法名稱"
  [[ -f Dockerfile ]] || die "這個資料夾沒有 Dockerfile。先跑 hl init <nextjs|python|static>"
  dir="$(app_dir "$name")"
  host="$(hl_host "$name")"

  if [[ "$HL_DEPLOY_INTERNAL" == 1 ]]; then
    public_id="$(cf_find_public_app_id "$host")" || die "查不到 Cloudflare 的公開設定，無法確認 $host 是否公開"
    [[ -z "$public_id" ]] || die "$host 目前是公開的。先跑 hl private $name 再改成內網"
  fi

  step "同步原始碼 → $HL_REMOTE:~/$dir/src/"
  hl_remote "mkdir -p $dir/src $dir/data && touch $dir/.env && chmod 600 $dir/.env"
  rsync -e "ssh ${HL_SSH_OPTS[*]}" "${HL_RSYNC_ARGS[@]}" ./ "$HL_REMOTE:$dir/src/"
  hl_render_compose "$name" "$HL_DEPLOY_INTERNAL" | hl_remote "cat > $dir/compose.yml"

  step "build 並啟動（build 失敗的話，舊版本會繼續跑）"
  hl_remote "cd $dir && docker compose -p hl-$name up -d --build --remove-orphans"

  if [[ "$HL_DEPLOY_INTERNAL" == 1 ]]; then
    step "內網模式：移除對外路由（如果有）"
    hl_remote "rm -f $HL_REMOTE_HOMELAB_DIR/proxy/sites/$name.caddy"
    reload_proxy
    step "健康檢查（從 hl 網路內，最多 60 秒）"
    internal_health_check "$name"
    echo
    echo "完成：http://$name:$HL_APP_PORT （內網：只有 hl 網路上的 app 連得到）"
    return
  fi

  step "設定路由 $host"
  hl_render_site "$name" | hl_remote "cat > $HL_REMOTE_HOMELAB_DIR/proxy/sites/$name.caddy"
  reload_proxy

  step "健康檢查（最多 60 秒）"
  health_check "$name"

  step "確認外部存取"
  label="$(check_access "$host")" || exit 1

  echo
  echo "完成：https://$host （$label）"
}
```

(d) 把 `cmd_ls()` 整個換成：

```bash
cmd_ls() {
  local publics name state internal access url
  publics="$(cf_public_hosts 2>/dev/null)" || publics="?"
  printf '%-20s %-10s %-34s %s\n' NAME STATE URL ACCESS
  hl_remote "docker ps -a --filter label=zone.chen.hl=1 --format '{{.Names}} {{.State}} {{.Label \"zone.chen.hl.internal\"}}'" | sort | while read -r name state internal; do
    url="https://$(hl_host "$name")"
    if [[ "$internal" == 1 ]]; then
      access=internal
      url="http://$name:$HL_APP_PORT"
    elif [[ "$publics" == "?" ]]; then
      access="?"
    elif grep -qx "$(hl_host "$name")" <<<"$publics"; then
      access=public
    else
      access=private
    fi
    printf '%-20s %-10s %-34s %s\n' "$name" "$state" "$url" "$access"
  done
  echo
  echo "proxy: $(hl_remote "docker inspect -f '{{.State.Status}}' $HL_PROXY_CONTAINER 2>/dev/null || echo missing")"
}
```

(e) 在 `cmd_public()` 裡，`require_app "$name"` 那一行的下一行插入：

```bash
  if [[ "$(hl_remote "docker inspect -f '{{index .Config.Labels \"zone.chen.hl.internal\"}}' $name 2>/dev/null")" == 1 ]]; then
    die "$name 是內網 app，不能公開。要對外的話，用 hl deploy（不加 --internal）重新部署"
  fi
```

- [ ] **Step 6：lint 和單元測試**

Run: `shellcheck ~/homelab/bin/hl ~/homelab/lib/*.sh ~/homelab/tests/*.sh && bash ~/homelab/tests/run.sh`
Expected：shellcheck 沒有輸出；每個測試檔都以 `全部通過` 結尾。

- [ ] **Step 7：在 m1 上實際跑一次（用拋棄式 app）**

```bash
smoke="$(mktemp -d)/hl-internal-smoke" && mkdir "$smoke" && cd "$smoke" \
  && hl init static && echo ok > index.html && hl deploy --internal
```
Expected：最後一行是 `完成：http://hl-internal-smoke:8080 （內網：只有 hl 網路上的 app 連得到）`。

Run: `hl ls`
Expected：`hl-internal-smoke` 那一列的 URL 是 `http://hl-internal-smoke:8080`、ACCESS 是 `internal`。

Run: `hl public hl-internal-smoke`
Expected：以非零狀態結束，並印出 `錯誤：hl-internal-smoke 是內網 app，不能公開…`。

Run: `hl rm hl-internal-smoke`
Expected：最後印出 `已下架 hl-internal-smoke`。這個 app 是本步驟自己建立的，可以直接刪。

- [ ] **Step 8：更新 skill 文件**

在 `~/.claude/skills/homelab-deploy/SKILL.md` 的「常用管理指令」清單最後加一行：

```markdown
- `hl deploy <name> --internal`：內網 app，只在 hl Docker 網路上，其他 app 用 `http://<name>:8080` 呼叫；不建對外路由、不能 `hl public`
```

- [ ] **Step 9：Commit（homelab repo）**

```bash
cd ~/homelab && git add lib/hl-lib.sh bin/hl tests/test-hl-lib.sh \
  && git commit -m "feat: hl deploy --internal for network-only apps

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Kg6bv9JV9xTSx6n6doWzzc"
```

---

### Task 2：引擎專案骨架與 pyvalhalla 可用性

**Files:**
- Create: `.gitignore`
- Create: `engine/pyproject.toml`（用 `uv init` 產生，再修改）
- Create: `engine/src/engine/__init__.py`
- Create: `engine/src/engine/app.py`
- Create: `engine/Dockerfile`
- Create: `engine/.dockerignore`
- Test: `engine/tests/test_app.py`
- Test: `engine/tests/test_valhalla_available.py`

**Interfaces:**
- Produces:
  - `engine.app.create_app(data_root: Path) -> FastAPI`
  - 模組層級的 `engine.app.app`，`data_root` 取自 env `DATA_DIR`，預設 `/data`
  - `GET /` → `{"service": "artroute-engine"}`
  - `GET /health` → `{"ok": true}`
- Dockerfile 的 stage 順序是 `base` → `test` → `runtime`。`runtime` 必須放最後，這樣 hl 預設 build 的才是它。

- [ ] **Step 1：建立專案**

```bash
cd <repo>
mkdir engine && cd engine
uv init --package --name engine --python 3.13 --vcs none .
uv add fastapi 'uvicorn[standard]' httpx pyvalhalla==3.9.1
uv add --dev pytest osmium
```
Expected：產生 `pyproject.toml`、`uv.lock`、`src/engine/__init__.py`，`uv add` 全部成功。如果 `uv add` 回報 pyvalhalla 或 osmium 在 macOS arm64 找不到 wheel，**停下來**，把錯誤寫進 `docs/p0-findings.md`，回報使用者。

把 `src/engine/__init__.py` 的內容換成一行註解：

```python
"""artroute-engine：GPS art 路線吸附與搜尋引擎。"""
```

刪掉 `pyproject.toml` 裡 `[project.scripts]` 那一段（uv 範本產生的 `engine = "engine:main"`，用不到）。在 `pyproject.toml` 最後加上：

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
markers = ["valhalla: 會實際建 Valhalla tiles（較慢）"]
```

在 repo 根目錄建立 `.gitignore`：

```gitignore
.DS_Store
.venv/
__pycache__/
.pytest_cache/
node_modules/
.next/
.env*
```

- [ ] **Step 2：寫失敗的測試**

`engine/tests/test_app.py`：

```python
from pathlib import Path

from fastapi.testclient import TestClient

from engine.app import create_app


def client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(tmp_path))


def test_root_identifies_service(tmp_path: Path) -> None:
    assert client(tmp_path).get("/").json() == {"service": "artroute-engine"}


def test_health_ok(tmp_path: Path) -> None:
    response = client(tmp_path).get("/health")
    assert response.status_code == 200
    assert response.json() == {"ok": True}
```

`engine/tests/test_valhalla_available.py`：

```python
import subprocess
import sys

from valhalla import Actor, get_config


def test_python_api_importable() -> None:
    assert callable(Actor)
    assert callable(get_config)


def test_build_tiles_executable_bundled() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "valhalla", "--help"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert "valhalla_build_tiles" in result.stdout + result.stderr
```

- [ ] **Step 3：跑測試，確認失敗**

Run: `cd engine && uv run pytest -q`
Expected：`test_app.py` 因為 `ModuleNotFoundError: No module named 'engine.app'` 而失敗。`test_valhalla_available.py` 應該已經通過；如果沒過，就是 P0 的阻斷點：把輸出寫進 `docs/p0-findings.md`，停下來回報。

- [ ] **Step 4：實作**

`engine/src/engine/app.py`：

```python
"""HTTP 介面。只在 hl 內網提供服務，由 web 轉發請求。"""

import os
from pathlib import Path

from fastapi import FastAPI


def create_app(data_root: Path) -> FastAPI:
    app = FastAPI(title="artroute-engine")
    app.state.data_root = data_root

    @app.get("/")
    def root() -> dict[str, str]:
        return {"service": "artroute-engine"}

    @app.get("/health")
    def health() -> dict[str, bool]:
        return {"ok": True}

    return app


app = create_app(Path(os.environ.get("DATA_DIR", "/data")))
```

- [ ] **Step 5：跑測試，確認通過**

Run: `cd engine && uv run pytest -q`
Expected：`4 passed`

- [ ] **Step 6：記錄 pyvalhalla 附帶哪些執行檔**

Run: `cd engine && uv run python -m valhalla --help`

建立 `docs/p0-findings.md`，內容如下（把 `<…>` 換成實際輸出）：

```markdown
# P0 驗證結果

## pyvalhalla 3.9.1
- macOS arm64 wheel：可用
- `python -m valhalla --help` 列出的執行檔：<貼上清單>
- linux arm64（m1 容器）：待 Task 7
```

- [ ] **Step 7：Dockerfile**

`engine/Dockerfile`：

```dockerfile
# 多階段：runtime 必須是最後一個 stage（hl deploy 預設 build 它）
# 在 m1 跑測試：docker build --target test .
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS base
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

FROM base AS test
RUN uv sync --frozen --no-install-project
COPY . .
RUN uv sync --frozen
RUN uv run --no-sync pytest -q

FROM base AS runtime
COPY . .
RUN uv sync --frozen --no-dev
CMD exec uv run --no-sync uvicorn engine.app:app --host 0.0.0.0 --port "$PORT"
```

`engine/.dockerignore`：

```
.venv
__pycache__
.pytest_cache
.git
.env*
Dockerfile
.dockerignore
```

- [ ] **Step 8：Commit**

```bash
cd <repo>
git add .gitignore engine docs/p0-findings.md
git commit -m "feat: 引擎專案骨架（FastAPI、pyvalhalla、Dockerfile）

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Kg6bv9JV9xTSx6n6doWzzc"
```

---

### Task 3：合成地圖與建 tiles

**Files:**
- Create: `engine/src/engine/routing/__init__.py`（空檔）
- Create: `engine/src/engine/routing/tiles.py`
- Create: `engine/tests/osm_fixture.py`
- Create: `engine/tests/conftest.py`
- Test: `engine/tests/test_tiles.py`

**Interfaces:**
- Produces（`engine.routing.tiles`）：
  - `class TileBuildError(Exception)`
  - `write_config(tile_dir: Path, config_path: Path) -> dict`
  - `build_tiles(pbf: Path, out_dir: Path) -> Path`：建出 `out_dir/tiles/` 和 `out_dir/valhalla.json`，回傳 config 路徑。
- Produces（tests）：
  - `osm_fixture.grid_point(col: float, row: float) -> tuple[float, float]`，回傳 `(lon, lat)`
  - `osm_fixture.write_grid_pbf(path: Path, ways: list[GridWay]) -> None`
  - `osm_fixture.GridWay(id: int, nodes: list[tuple[int, int]], tags: dict[str, str])`
  - `osm_fixture.TEST_MAP: list[GridWay]`
  - 常數：`BOTTOM_ONEWAY_WAY_ID`、`CONTRAFLOW_WAY_ID`、`MOTORWAY_WAY_ID`
  - conftest 的 session fixture `grid_config: Path`（合成地圖的 `valhalla.json`）

合成地圖（col, row；每格約 200m）：

```
row 3:  (0,3) ══════════ motorway ══════════ (6,3)
          │                                    │      ← (0,1)-(0,3)、(6,1)-(6,3) residential
row 1:  (0,1)-(1,1)-(2,1)-(3,1)-(4,1)-(5,1)-(6,1)    ← 雙向 residential
          │     │     │           │     │     │
row 0:  (0,0)→(1,0)→(2,0)       (4,0)⇄(5,0)⇄(6,0)
        oneway=yes（只能往東）    oneway=yes + oneway:bicycle=no
```

- [ ] **Step 1：合成地圖產生器**

`engine/tests/osm_fixture.py`：

```python
"""用 pyosmium 產生小型合成地圖 pbf，讓 Valhalla 測試結果固定、可重現。"""

from dataclasses import dataclass, field
from pathlib import Path

import osmium

LON0, LAT0 = 121.50, 25.05
STEP = 0.002  # 經度約 201m、緯度約 222m


def grid_point(col: float, row: float) -> tuple[float, float]:
    """網格座標 → (lon, lat)。"""
    return (LON0 + col * STEP, LAT0 + row * STEP)


@dataclass(frozen=True)
class GridWay:
    id: int
    nodes: list[tuple[int, int]]
    tags: dict[str, str] = field(default_factory=dict)


def write_grid_pbf(path: Path, ways: list[GridWay]) -> None:
    node_ids: dict[tuple[int, int], int] = {}
    for way in ways:
        for coord in way.nodes:
            node_ids.setdefault(coord, len(node_ids) + 1)
    writer = osmium.SimpleWriter(str(path))
    try:
        for coord, node_id in sorted(node_ids.items(), key=lambda item: item[1]):
            writer.add_node(
                osmium.osm.mutable.Node(id=node_id, location=grid_point(*coord), version=1)
            )
        for way in ways:
            writer.add_way(
                osmium.osm.mutable.Way(
                    id=way.id,
                    nodes=[node_ids[c] for c in way.nodes],
                    tags=way.tags,
                    version=1,
                )
            )
    finally:
        writer.close()


RESIDENTIAL = {"highway": "residential"}
BOTTOM_ONEWAY_WAY_ID = 100
CONTRAFLOW_WAY_ID = 101
MOTORWAY_WAY_ID = 102

TEST_MAP: list[GridWay] = [
    GridWay(1, [(c, 1) for c in range(7)], RESIDENTIAL),
    *[GridWay(10 + c, [(c, 0), (c, 1)], RESIDENTIAL) for c in (0, 1, 2, 4, 5, 6)],
    GridWay(20, [(0, 1), (0, 2), (0, 3)], RESIDENTIAL),
    GridWay(21, [(6, 1), (6, 2), (6, 3)], RESIDENTIAL),
    GridWay(BOTTOM_ONEWAY_WAY_ID, [(0, 0), (1, 0), (2, 0)], {**RESIDENTIAL, "oneway": "yes"}),
    GridWay(
        CONTRAFLOW_WAY_ID,
        [(4, 0), (5, 0), (6, 0)],
        {**RESIDENTIAL, "oneway": "yes", "oneway:bicycle": "no"},
    ),
    GridWay(MOTORWAY_WAY_ID, [(c, 3) for c in range(7)], {"highway": "motorway"}),
]
```

- [ ] **Step 2：寫失敗的測試**

`engine/tests/conftest.py`：

```python
from pathlib import Path

import pytest

from engine.routing.tiles import build_tiles
from osm_fixture import TEST_MAP, write_grid_pbf


@pytest.fixture(scope="session")
def grid_config(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """合成地圖建好的 Valhalla 設定檔路徑（整個測試只建一次）。"""
    root = tmp_path_factory.mktemp("grid")
    pbf = root / "grid.osm.pbf"
    write_grid_pbf(pbf, TEST_MAP)
    return build_tiles(pbf, root / "build")
```

`engine/tests/test_tiles.py`：

```python
import json
from pathlib import Path

import pytest
from valhalla import Actor

from engine.routing.tiles import TileBuildError, build_tiles
from osm_fixture import grid_point

pytestmark = pytest.mark.valhalla


def test_build_tiles_writes_config_pointing_to_tiles(grid_config: Path) -> None:
    config = json.loads(grid_config.read_text())
    tile_dir = Path(config["mjolnir"]["tile_dir"])
    assert tile_dir.resolve() == (grid_config.parent / "tiles").resolve()
    assert any(tile_dir.rglob("*.gph"))


def test_built_tiles_can_route(grid_config: Path) -> None:
    (lon1, lat1), (lon2, lat2) = grid_point(0, 1), grid_point(6, 1)
    result = Actor(str(grid_config)).route(
        {"locations": [{"lon": lon1, "lat": lat1}, {"lon": lon2, "lat": lat2}], "costing": "bicycle"}
    )
    assert result["trip"]["summary"]["length"] == pytest.approx(1.2, abs=0.05)


def test_missing_pbf_raises(tmp_path: Path) -> None:
    with pytest.raises(TileBuildError):
        build_tiles(tmp_path / "nope.osm.pbf", tmp_path / "build")
```

- [ ] **Step 3：跑測試，確認失敗**

Run: `cd engine && uv run pytest tests/test_tiles.py -q`
Expected：collection error `ModuleNotFoundError: No module named 'engine.routing'`

- [ ] **Step 4：實作**

`engine/src/engine/routing/__init__.py`：空檔。

`engine/src/engine/routing/tiles.py`：

```python
"""產生 Valhalla 設定檔並從 pbf 建 tiles。"""

import json
import subprocess
import sys
from pathlib import Path

from valhalla import get_config


class TileBuildError(Exception):
    pass


def write_config(tile_dir: Path, config_path: Path) -> dict:
    """只用 tile_dir（不打包 tar），tile_extract 給空字串讓 Valhalla 直接讀資料夾。"""
    tile_dir.mkdir(parents=True, exist_ok=True)
    config = get_config(tile_extract="", tile_dir=tile_dir)
    config_path.write_text(json.dumps(config))
    return config


def build_tiles(pbf: Path, out_dir: Path) -> Path:
    if not pbf.is_file():
        raise TileBuildError(f"找不到 pbf：{pbf}")
    out_dir.mkdir(parents=True, exist_ok=True)
    config_path = out_dir / "valhalla.json"
    write_config(out_dir / "tiles", config_path)
    # cwd 設成 out_dir：valhalla_build_tiles 會在工作目錄寫中間檔
    result = subprocess.run(
        [sys.executable, "-m", "valhalla", "valhalla_build_tiles", "-c", str(config_path), str(pbf)],
        cwd=out_dir,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise TileBuildError(f"valhalla_build_tiles 失敗（{result.returncode}）：{result.stderr[-2000:]}")
    return config_path
```

- [ ] **Step 5：跑測試，確認通過**

Run: `cd engine && uv run pytest -q`
Expected：`7 passed`

如果 `test_built_tiles_can_route` 失敗，而且錯誤是找不到路，先確認合成 pbf 有被 Valhalla 讀到：看 `TileBuildError` 的訊息，或手動跑 `uv run python -m valhalla valhalla_build_tiles -c <config> <pbf>` 看 log。把原因和修正方式記到 `docs/p0-findings.md`。

- [ ] **Step 6：Commit**

```bash
git add engine/src/engine/routing engine/tests
git commit -m "feat: 合成地圖 fixture 與 Valhalla tiles 建置

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Kg6bv9JV9xTSx6n6doWzzc"
```

---

### Task 4：Router 與自行車交通規則驗證（P0 核心）

**Files:**
- Create: `engine/src/engine/routing/polyline.py`
- Create: `engine/src/engine/routing/router.py`
- Test: `engine/tests/test_polyline.py`
- Test: `engine/tests/test_router_bicycle_rules.py`
- Modify: `docs/p0-findings.md`

**Interfaces:**
- Consumes：`grid_config` fixture、`osm_fixture.grid_point`，以及 Task 3 的 way id 常數。
- Produces（`engine.routing.polyline`）：
  - `decode(encoded: str, precision: int = 6) -> list[tuple[float, float]]`，回傳 `[(lon, lat), …]`
  - `encode(points: list[tuple[float, float]], precision: int = 6) -> str`
- Produces（`engine.routing.router`）：
  - `type LonLat = tuple[float, float]`
  - `@dataclass(frozen=True) RouteResult(points: list[LonLat], length_km: float)`
  - `@dataclass(frozen=True) EdgeInfo(way_id: int, begin_heading: float)`
  - `class RoutingError(Exception)`
  - `class Router(config_path: Path)`，方法：
    - `trace(points: list[LonLat], *, search_radius_m: float = 50.0) -> RouteResult`：map matching
    - `route(start: LonLat, end: LonLat) -> RouteResult`：兩點路徑
    - `edges(points: list[LonLat]) -> list[EdgeInfo]`：對已經在路網上的路線取每段的 way id 和方向

- [ ] **Step 1：polyline 的失敗測試**

`engine/tests/test_polyline.py`：

```python
import pytest

from engine.routing.polyline import decode, encode

# Google 官方範例（precision 5）
GOOGLE_EXAMPLE = "_p~iF~ps|U_ulLnnqC_mqNvxq`@"
GOOGLE_POINTS = [(-120.2, 38.5), (-120.95, 40.7), (-126.453, 43.252)]


def test_decode_known_vector_precision5() -> None:
    assert decode(GOOGLE_EXAMPLE, precision=5) == pytest.approx(GOOGLE_POINTS)


def test_encode_known_vector_precision5() -> None:
    assert encode(GOOGLE_POINTS, precision=5) == GOOGLE_EXAMPLE


def test_round_trip_precision6() -> None:
    points = [(121.501234, 25.051234), (121.502, 25.05), (121.499999, 25.049999)]
    assert decode(encode(points)) == pytest.approx(points, abs=1e-6)


def test_decode_empty() -> None:
    assert decode("") == []
```

- [ ] **Step 2：跑測試，確認失敗**

Run: `cd engine && uv run pytest tests/test_polyline.py -q`
Expected：`ModuleNotFoundError: No module named 'engine.routing.polyline'`

- [ ] **Step 3：實作 polyline**

`engine/src/engine/routing/polyline.py`：

```python
"""Google encoded polyline。Valhalla 用 precision 6；座標一律以 (lon, lat) 表示。"""


def decode(encoded: str, precision: int = 6) -> list[tuple[float, float]]:
    factor = 10**precision
    points: list[tuple[float, float]] = []
    index = lat = lon = 0
    while index < len(encoded):
        deltas = []
        for _ in range(2):
            shift = result = 0
            while True:
                byte = ord(encoded[index]) - 63
                index += 1
                result |= (byte & 0x1F) << shift
                shift += 5
                if byte < 0x20:
                    break
            deltas.append(~(result >> 1) if result & 1 else result >> 1)
        lat += deltas[0]
        lon += deltas[1]
        points.append((lon / factor, lat / factor))
    return points


def _encode_value(value: int) -> str:
    value = ~(value << 1) if value < 0 else value << 1
    chunks = []
    while value >= 0x20:
        chunks.append(chr((0x20 | (value & 0x1F)) + 63))
        value >>= 5
    chunks.append(chr(value + 63))
    return "".join(chunks)


def encode(points: list[tuple[float, float]], precision: int = 6) -> str:
    factor = 10**precision
    out = []
    prev_lat = prev_lon = 0
    for lon, lat in points:
        lat_i, lon_i = round(lat * factor), round(lon * factor)
        out.append(_encode_value(lat_i - prev_lat))
        out.append(_encode_value(lon_i - prev_lon))
        prev_lat, prev_lon = lat_i, lon_i
    return "".join(out)
```

Run: `cd engine && uv run pytest tests/test_polyline.py -q`
Expected：`4 passed`

- [ ] **Step 4：交通規則的失敗測試**

`engine/tests/test_router_bicycle_rules.py`：

```python
"""P0 核心：bicycle 必須遵守單行道、允許 oneway:bicycle=no 的逆向、避開快速道路。"""

from pathlib import Path

import pytest

from engine.routing.router import Router, RoutingError
from osm_fixture import BOTTOM_ONEWAY_WAY_ID, CONTRAFLOW_WAY_ID, MOTORWAY_WAY_ID, grid_point

pytestmark = pytest.mark.valhalla


@pytest.fixture(scope="module")
def router(grid_config: Path) -> Router:
    return Router(grid_config)


def densify(start: tuple[float, float], end: tuple[float, float], n: int = 16) -> list[tuple[float, float]]:
    (x1, y1), (x2, y2) = start, end
    return [(x1 + (x2 - x1) * i / n, y1 + (y2 - y1) * i / n) for i in range(n + 1)]


def rides_westward_on_row(points: list[tuple[float, float]], row: int, col_min: int, col_max: int) -> bool:
    """路線是否在 row 這條街、col_min..col_max 之間往西騎。"""
    lat = grid_point(0, row)[1]
    lo, hi = grid_point(col_min, row)[0], grid_point(col_max, row)[0]
    for (x1, y1), (x2, y2) in zip(points, points[1:]):
        on_row = abs(y1 - lat) < 1e-5 and abs(y2 - lat) < 1e-5
        within = lo - 1e-6 <= min(x1, x2) and max(x1, x2) <= hi + 1e-6
        if on_row and within and x2 < x1 - 1e-7:
            return True
    return False


def touches_motorway_interior(points: list[tuple[float, float]]) -> bool:
    lat = grid_point(0, 3)[1]
    lo, hi = grid_point(0.5, 3)[0], grid_point(5.5, 3)[0]
    return any(abs(y - lat) < 1e-5 and lo < x < hi for x, y in points)


def test_trace_along_oneway_in_legal_direction(router: Router) -> None:
    result = router.trace(densify(grid_point(0, 0), grid_point(2, 0)))
    assert result.length_km == pytest.approx(0.40, abs=0.05)
    assert all(abs(lat - grid_point(0, 0)[1]) < 1e-4 for _, lat in result.points)


def test_route_against_oneway_detours(router: Router) -> None:
    result = router.route(grid_point(2, 0), grid_point(0, 0))
    assert not rides_westward_on_row(result.points, row=0, col_min=0, col_max=2)
    assert result.length_km > 0.8  # 必須繞上面那條街


def test_trace_against_oneway_never_rides_wrong_way(router: Router) -> None:
    try:
        result = router.trace(densify(grid_point(2, 0), grid_point(0, 0)))
    except RoutingError:
        return  # 找不到合法匹配時報錯，也算安全
    assert not rides_westward_on_row(result.points, row=0, col_min=0, col_max=2)


def test_contraflow_lets_bicycle_ride_against_oneway(router: Router) -> None:
    result = router.route(grid_point(6, 0), grid_point(4, 0))
    assert result.length_km == pytest.approx(0.40, abs=0.05)


def test_bicycle_route_avoids_motorway(router: Router) -> None:
    result = router.route(grid_point(0, 3), grid_point(6, 3))
    assert not touches_motorway_interior(result.points)
    assert result.length_km > 1.6


def test_edges_report_way_ids_and_heading(router: Router) -> None:
    forward = router.route(grid_point(0, 0), grid_point(2, 0))
    edges = router.edges(forward.points)
    oneway_edges = [e for e in edges if e.way_id == BOTTOM_ONEWAY_WAY_ID]
    assert oneway_edges
    assert all(45 <= e.begin_heading <= 135 for e in oneway_edges)  # 往東
    assert all(e.way_id not in (CONTRAFLOW_WAY_ID, MOTORWAY_WAY_ID) for e in edges)


def test_trace_far_from_roads_raises(router: Router) -> None:
    with pytest.raises(RoutingError):
        router.trace(densify((121.0, 24.0), (121.001, 24.0)))
```

- [ ] **Step 5：跑測試，確認失敗**

Run: `cd engine && uv run pytest tests/test_router_bicycle_rules.py -q`
Expected：`ModuleNotFoundError: No module named 'engine.routing.router'`

- [ ] **Step 6：實作 Router**

`engine/src/engine/routing/router.py`：

```python
"""包裝 valhalla.Actor：交通方式固定 bicycle，座標一律 (lon, lat)。"""

from dataclasses import dataclass
from pathlib import Path

from valhalla import Actor

from engine.routing.polyline import decode

type LonLat = tuple[float, float]

COSTING = "bicycle"


@dataclass(frozen=True)
class RouteResult:
    points: list[LonLat]
    length_km: float


@dataclass(frozen=True)
class EdgeInfo:
    way_id: int
    begin_heading: float


class RoutingError(Exception):
    pass


def _locations(points: list[LonLat]) -> list[dict[str, float]]:
    return [{"lon": lon, "lat": lat} for lon, lat in points]


def _route_result(response: dict) -> RouteResult:
    trip = response["trip"]
    points: list[LonLat] = []
    for leg in trip["legs"]:
        leg_points = decode(leg["shape"])
        if points and leg_points and points[-1] == leg_points[0]:
            leg_points = leg_points[1:]
        points.extend(leg_points)
    return RouteResult(points=points, length_km=trip["summary"]["length"])


class Router:
    def __init__(self, config_path: Path) -> None:
        self._actor = Actor(str(config_path))

    def _call(self, method: str, request: dict) -> dict:
        try:
            return getattr(self._actor, method)(request)
        except RuntimeError as error:
            raise RoutingError(str(error)) from error

    def trace(self, points: list[LonLat], *, search_radius_m: float = 50.0) -> RouteResult:
        response = self._call(
            "trace_route",
            {
                "shape": _locations(points),
                "costing": COSTING,
                "shape_match": "map_snap",
                "trace_options": {"search_radius": search_radius_m},
            },
        )
        return _route_result(response)

    def route(self, start: LonLat, end: LonLat) -> RouteResult:
        response = self._call("route", {"locations": _locations([start, end]), "costing": COSTING})
        return _route_result(response)

    def edges(self, points: list[LonLat]) -> list[EdgeInfo]:
        response = self._call(
            "trace_attributes",
            {
                "shape": _locations(points),
                "costing": COSTING,
                "shape_match": "edge_walk",
                "filters": {"attributes": ["edge.way_id", "edge.begin_heading"], "action": "include"},
            },
        )
        return [EdgeInfo(way_id=e["way_id"], begin_heading=e["begin_heading"]) for e in response["edges"]]
```

- [ ] **Step 7：跑測試，確認通過**

Run: `cd engine && uv run pytest -q`
Expected：全部通過（`18 passed`）。

如果失敗，先判斷屬於下面哪一種：
- `test_trace_far_from_roads_raises` 失敗，而且拋出的不是 `RuntimeError`：把 `_call` 裡 `except` 的型別改成實際的例外型別，在註解寫明原因，然後重跑。
- `test_edges_report_way_ids_and_heading` 因為 `edge_walk` 匹配失敗：把 `edges()` 的 `shape_match` 改成 `"map_snap"`，然後重跑。
- **交通規則測試失敗**（逆向、快速道路）：這是 P0 的阻斷點。不要修改測試去遷就結果。把失敗的輸出寫進 `docs/p0-findings.md`，停下來回報使用者。

- [ ] **Step 8：記錄「逆著單行道做 trace」的實際行為**

Run:
```bash
cd engine && uv run python -c "
import sys; sys.path.insert(0, 'tests')
from pathlib import Path
from osm_fixture import TEST_MAP, write_grid_pbf, grid_point
from engine.routing.tiles import build_tiles
from engine.routing.router import Router, RoutingError
import tempfile
d = Path(tempfile.mkdtemp())
write_grid_pbf(d / 'g.osm.pbf', TEST_MAP)
r = Router(build_tiles(d / 'g.osm.pbf', d / 'b'))
(x1, y1), (x2, y2) = grid_point(2, 0), grid_point(0, 0)
pts = [(x1 + (x2 - x1) * i / 16, y1 + (y2 - y1) * i / 16) for i in range(17)]
try:
    res = r.trace(pts); print('DETOUR', round(res.length_km, 3), 'km', len(res.points), 'points')
except RoutingError as e:
    print('ERROR', e)
"
```

把輸出整理後附加到 `docs/p0-findings.md`：

```markdown
## bicycle 交通規則（合成地圖）
- 順向 trace：通過
- 逆向 route：繞路，未逆向
- 逆向 trace 的實際行為：<DETOUR x km / ERROR 訊息>
- oneway:bicycle=no：允許逆向
- motorway：避開
- 對 P1 的影響：<若是 ERROR，P1 的吸附需要「失敗時改用部件端點之間的 route 補段」；若是 DETOUR，記下繞路長度>
```

- [ ] **Step 9：Commit**

```bash
git add engine/src/engine/routing engine/tests docs/p0-findings.md
git commit -m "feat: Router 與 bicycle 單行道／快速道路驗證

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Kg6bv9JV9xTSx6n6doWzzc"
```

---

### Task 5：路網資料建置流程

**Files:**
- Create: `engine/src/engine/data/__init__.py`（空檔）
- Create: `engine/src/engine/data/layout.py`
- Create: `engine/src/engine/data/download.py`
- Create: `engine/src/engine/data/pipeline.py`
- Create: `engine/src/engine/data/checks.py`
- Create: `engine/src/engine/data/cli.py`
- Test: `engine/tests/test_layout.py`
- Test: `engine/tests/test_download.py`
- Test: `engine/tests/test_pipeline.py`
- Test: `engine/tests/test_checks.py`

**Interfaces:**
- Consumes：`engine.routing.tiles.build_tiles`、`engine.routing.router.Router` / `EdgeInfo` / `RouteResult`
- Produces（`engine.data.layout`）：`class DataLayout(root: Path)`
  - 屬性：`osm_dir`、`builds_dir`、`current_link`
  - `new_build_dir(build_id: str) -> Path`
  - `current() -> Path | None`
  - `activate(build_dir: Path) -> None`
  - `prune(keep: int = 2) -> list[Path]`
- Produces（`engine.data.download`）：
  - `GEOFABRIK_TAIWAN: str`
  - `class DownloadError(Exception)`
  - `download_with_md5(url: str, dest: Path, client: httpx.Client) -> str`，回傳 md5
- Produces（`engine.data.pipeline`）：
  - `@dataclass(frozen=True) BuildMeta(build_id: str, osm_md5: str, created_at: str)`
  - `class ValidationFailed(Exception)`
  - `run_build(layout, pbf, osm_md5, build_id, builder: Callable[[Path, Path], object], validator: Callable[[Path], list[str]]) -> BuildMeta`
  - `read_meta(build_dir: Path) -> BuildMeta | None`
- Produces（`engine.data.checks`）：
  - `taiwan_checks(router) -> list[str]`：`router` 需要有 `.route` 和 `.edges`
  - `validate_build(build_dir: Path) -> list[str]`
- Produces（`engine.data.cli`）：`main() -> None`，用 `python -m engine.data.cli` 執行。

- [ ] **Step 1：layout 的失敗測試**

`engine/tests/test_layout.py`：

```python
from pathlib import Path

from engine.data.layout import DataLayout


def test_no_current_initially(tmp_path: Path) -> None:
    assert DataLayout(tmp_path).current() is None


def test_activate_points_current_to_build(tmp_path: Path) -> None:
    layout = DataLayout(tmp_path)
    build = layout.new_build_dir("20261010T000000Z")
    layout.activate(build)
    assert layout.current() == build.resolve()
    assert layout.current_link.is_symlink()
    assert not layout.current_link.readlink().is_absolute()  # 相對連結，換掛載路徑也有效


def test_activate_replaces_previous(tmp_path: Path) -> None:
    layout = DataLayout(tmp_path)
    first = layout.new_build_dir("20261010T000000Z")
    second = layout.new_build_dir("20261017T000000Z")
    layout.activate(first)
    layout.activate(second)
    assert layout.current() == second.resolve()
    assert not list(tmp_path.glob(".current-*"))  # 暫存連結沒有殘留


def test_prune_keeps_newest_and_current(tmp_path: Path) -> None:
    layout = DataLayout(tmp_path)
    ids = ["20261001T000000Z", "20261008T000000Z", "20261015T000000Z", "20261022T000000Z"]
    builds = [layout.new_build_dir(i) for i in ids]
    layout.activate(builds[0])  # current 是最舊的（例如回滾後）
    removed = layout.prune(keep=2)
    assert sorted(p.name for p in removed) == ["20261008T000000Z"]
    remaining = sorted(p.name for p in layout.builds_dir.iterdir())
    assert remaining == ["20261001T000000Z", "20261015T000000Z", "20261022T000000Z"]
```

- [ ] **Step 2：跑測試，確認失敗**

Run: `cd engine && uv run pytest tests/test_layout.py -q`
Expected：`ModuleNotFoundError: No module named 'engine.data'`

- [ ] **Step 3：實作 layout**

`engine/src/engine/data/__init__.py`：空檔。

`engine/src/engine/data/layout.py`：

```python
"""/data 的目錄結構：osm/ 放原始 pbf，builds/<id>/ 放各版 tiles，current 指向使用中的那版。"""

import os
import shutil
from pathlib import Path


class DataLayout:
    def __init__(self, root: Path) -> None:
        self.root = root

    @property
    def osm_dir(self) -> Path:
        return self.root / "osm"

    @property
    def builds_dir(self) -> Path:
        return self.root / "builds"

    @property
    def current_link(self) -> Path:
        return self.root / "current"

    def new_build_dir(self, build_id: str) -> Path:
        path = self.builds_dir / build_id
        path.mkdir(parents=True, exist_ok=False)
        return path

    def current(self) -> Path | None:
        if not self.current_link.is_symlink():
            return None
        return self.current_link.resolve()

    def activate(self, build_dir: Path) -> None:
        """先建暫存連結再 os.replace：rename 是原子操作，讀取端不會看到半套狀態。"""
        tmp = self.root / f".current-{build_dir.name}.tmp"
        tmp.unlink(missing_ok=True)
        tmp.symlink_to(build_dir.resolve().relative_to(self.root.resolve()))
        os.replace(tmp, self.current_link)

    def prune(self, keep: int = 2) -> list[Path]:
        builds = sorted(p for p in self.builds_dir.iterdir() if p.is_dir())
        current = self.current()
        keep_set = {p.resolve() for p in builds[-keep:]} | ({current} if current else set())
        removed = [p for p in builds if p.resolve() not in keep_set]
        for path in removed:
            shutil.rmtree(path)
        return removed
```

Run: `cd engine && uv run pytest tests/test_layout.py -q`
Expected：`4 passed`

- [ ] **Step 4：download 的失敗測試**

`engine/tests/test_download.py`：

```python
import hashlib
from pathlib import Path

import httpx
import pytest

from engine.data.download import DownloadError, download_with_md5

URL = "https://example.test/taiwan-latest.osm.pbf"
PAYLOAD = b"fake pbf bytes" * 1000


def client_serving(md5_text: str) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(".md5"):
            return httpx.Response(200, text=md5_text)
        return httpx.Response(200, content=PAYLOAD)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_download_verifies_md5(tmp_path: Path) -> None:
    md5 = hashlib.md5(PAYLOAD).hexdigest()
    dest = tmp_path / "taiwan.osm.pbf"
    assert download_with_md5(URL, dest, client_serving(f"{md5}  taiwan-latest.osm.pbf\n")) == md5
    assert dest.read_bytes() == PAYLOAD
    assert not dest.with_name(dest.name + ".part").exists()


def test_md5_mismatch_keeps_old_file(tmp_path: Path) -> None:
    dest = tmp_path / "taiwan.osm.pbf"
    dest.write_bytes(b"old")
    with pytest.raises(DownloadError):
        download_with_md5(URL, dest, client_serving("0" * 32 + "  taiwan-latest.osm.pbf\n"))
    assert dest.read_bytes() == b"old"
    assert not dest.with_name(dest.name + ".part").exists()
```

- [ ] **Step 5：實作 download**

`engine/src/engine/data/download.py`：

```python
"""下載 Geofabrik pbf，邊下載邊算 md5；驗證通過才取代舊檔。"""

import hashlib
import os
from pathlib import Path

import httpx

GEOFABRIK_TAIWAN = "https://download.geofabrik.de/asia/taiwan-latest.osm.pbf"


class DownloadError(Exception):
    pass


def download_with_md5(url: str, dest: Path, client: httpx.Client) -> str:
    md5_response = client.get(url + ".md5")
    md5_response.raise_for_status()
    expected = md5_response.text.split()[0]

    part = dest.with_name(dest.name + ".part")
    digest = hashlib.md5()
    try:
        with client.stream("GET", url) as response:
            response.raise_for_status()
            with part.open("wb") as file:
                for chunk in response.iter_bytes(1 << 20):
                    file.write(chunk)
                    digest.update(chunk)
        if digest.hexdigest() != expected:
            raise DownloadError(f"md5 不符：預期 {expected}，實際 {digest.hexdigest()}")
        os.replace(part, dest)
    finally:
        part.unlink(missing_ok=True)
    return expected
```

Run: `cd engine && uv run pytest tests/test_download.py -q`
Expected：`2 passed`

- [ ] **Step 6：pipeline 的失敗測試**

`engine/tests/test_pipeline.py`：

```python
from pathlib import Path

import pytest

from engine.data.layout import DataLayout
from engine.data.pipeline import ValidationFailed, read_meta, run_build


def fake_builder(pbf: Path, build_dir: Path) -> None:
    (build_dir / "valhalla.json").write_text("{}")


def test_successful_build_is_activated_with_meta(tmp_path: Path) -> None:
    layout = DataLayout(tmp_path)
    meta = run_build(layout, tmp_path / "x.pbf", "abc", "20261010T000000Z", fake_builder, lambda d: [])
    assert layout.current() == (layout.builds_dir / "20261010T000000Z").resolve()
    assert read_meta(layout.current()) == meta
    assert meta.osm_md5 == "abc"


def test_failed_validation_keeps_previous_current(tmp_path: Path) -> None:
    layout = DataLayout(tmp_path)
    run_build(layout, tmp_path / "x.pbf", "old", "20261003T000000Z", fake_builder, lambda d: [])
    with pytest.raises(ValidationFailed, match="逆向"):
        run_build(layout, tmp_path / "x.pbf", "new", "20261010T000000Z", fake_builder, lambda d: ["逆向騎上單行道"])
    assert read_meta(layout.current()).osm_md5 == "old"
    assert not (layout.builds_dir / "20261010T000000Z").exists()


def test_builder_crash_keeps_previous_current(tmp_path: Path) -> None:
    layout = DataLayout(tmp_path)
    run_build(layout, tmp_path / "x.pbf", "old", "20261003T000000Z", fake_builder, lambda d: [])

    def crashing_builder(pbf: Path, build_dir: Path) -> None:
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        run_build(layout, tmp_path / "x.pbf", "new", "20261010T000000Z", crashing_builder, lambda d: [])
    assert read_meta(layout.current()).osm_md5 == "old"


def test_read_meta_missing(tmp_path: Path) -> None:
    assert read_meta(tmp_path) is None
```

- [ ] **Step 7：實作 pipeline**

`engine/src/engine/data/pipeline.py`：

```python
"""建置 → 驗證 → 啟用。任何一步失敗都刪掉新版、保留舊版繼續服務。"""

import json
import shutil
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from engine.data.layout import DataLayout

META_FILE = "meta.json"


@dataclass(frozen=True)
class BuildMeta:
    build_id: str
    osm_md5: str
    created_at: str


class ValidationFailed(Exception):
    pass


def run_build(
    layout: DataLayout,
    pbf: Path,
    osm_md5: str,
    build_id: str,
    builder: Callable[[Path, Path], object],
    validator: Callable[[Path], list[str]],
) -> BuildMeta:
    build_dir = layout.new_build_dir(build_id)
    try:
        builder(pbf, build_dir)
        failures = validator(build_dir)
        if failures:
            raise ValidationFailed("；".join(failures))
    except BaseException:
        shutil.rmtree(build_dir, ignore_errors=True)
        raise
    meta = BuildMeta(build_id=build_id, osm_md5=osm_md5, created_at=datetime.now(UTC).isoformat())
    (build_dir / META_FILE).write_text(json.dumps(asdict(meta)))
    layout.activate(build_dir)
    layout.prune(keep=2)
    return meta


def read_meta(build_dir: Path) -> BuildMeta | None:
    path = build_dir / META_FILE
    if not path.is_file():
        return None
    return BuildMeta(**json.loads(path.read_text()))
```

Run: `cd engine && uv run pytest tests/test_pipeline.py -q`
Expected：`4 passed`

- [ ] **Step 8：台灣驗證查詢的失敗測試**

`engine/tests/test_checks.py`：

```python
from engine.data.checks import ZHOUMEI_EAST, ZHOUMEI_ONEWAY_WAY_ID, ZHOUMEI_WEST, taiwan_checks
from engine.routing.router import EdgeInfo, RouteResult

WEST_TO_EAST = RouteResult(points=[ZHOUMEI_WEST, ZHOUMEI_EAST], length_km=0.4)
EAST_TO_WEST = RouteResult(points=[ZHOUMEI_EAST, (121.5, 25.102), ZHOUMEI_WEST], length_km=0.9)


class FakeRouter:
    """順向路線回 forward_edges，逆向路線回 backward_edges。"""

    def __init__(self, forward_edges: list[EdgeInfo], backward_edges: list[EdgeInfo]) -> None:
        self._forward_edges = forward_edges
        self._backward_edges = backward_edges

    def route(self, start, end) -> RouteResult:
        return WEST_TO_EAST if start == ZHOUMEI_WEST else EAST_TO_WEST

    def edges(self, points) -> list[EdgeInfo]:
        return self._forward_edges if points == WEST_TO_EAST.points else self._backward_edges


def test_passes_when_forward_uses_oneway_and_backward_avoids_it() -> None:
    router = FakeRouter([EdgeInfo(ZHOUMEI_ONEWAY_WAY_ID, 90.0)], [EdgeInfo(1, 0.0), EdgeInfo(2, 270.0)])
    assert taiwan_checks(router) == []


def test_fails_when_backward_rides_oneway_westward() -> None:
    router = FakeRouter([EdgeInfo(ZHOUMEI_ONEWAY_WAY_ID, 90.0)], [EdgeInfo(ZHOUMEI_ONEWAY_WAY_ID, 270.0)])
    assert any("逆向" in f for f in taiwan_checks(router))


def test_fails_loudly_when_way_disappears_from_osm() -> None:
    router = FakeRouter([EdgeInfo(1, 90.0)], [EdgeInfo(2, 270.0)])
    assert any("OSM" in f for f in taiwan_checks(router))
```

- [ ] **Step 9：實作 checks 與 cli**

`engine/src/engine/data/checks.py`：

```python
"""台灣真實路網的驗證查詢：新版路網必須全部通過才會啟用。"""

from pathlib import Path
from typing import Protocol

from engine.routing.router import EdgeInfo, LonLat, RouteResult, Router

# 洲美街單行段（OSM way 287016294，oneway=yes，節點順序由西往東）。座標取自該 way 的 geometry
ZHOUMEI_ONEWAY_WAY_ID = 287016294
ZHOUMEI_WEST: LonLat = (121.4994400, 25.1010677)
ZHOUMEI_EAST: LonLat = (121.5035557, 25.1011282)


class RouterLike(Protocol):
    def route(self, start: LonLat, end: LonLat) -> RouteResult: ...
    def edges(self, points: list[LonLat]) -> list[EdgeInfo]: ...


def taiwan_checks(router: RouterLike) -> list[str]:
    failures: list[str] = []
    forward = router.edges(router.route(ZHOUMEI_WEST, ZHOUMEI_EAST).points)
    if not any(e.way_id == ZHOUMEI_ONEWAY_WAY_ID for e in forward):
        failures.append(
            f"順向路線沒有走洲美街單行段（way {ZHOUMEI_ONEWAY_WAY_ID}）：OSM 資料可能變了，需要更新這項檢查"
        )
    for edge in router.edges(router.route(ZHOUMEI_EAST, ZHOUMEI_WEST).points):
        if edge.way_id == ZHOUMEI_ONEWAY_WAY_ID and not 45 <= edge.begin_heading <= 135:
            failures.append(f"逆向騎上洲美街單行段（heading {edge.begin_heading}）")
    return failures


def validate_build(build_dir: Path) -> list[str]:
    return taiwan_checks(Router(build_dir / "valhalla.json"))
```

`engine/src/engine/data/cli.py`：

```python
"""完整建置一次台灣路網：python -m engine.data.cli（在引擎容器裡執行）。"""

import json
import os
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

import httpx

from engine.data.checks import validate_build
from engine.data.download import GEOFABRIK_TAIWAN, download_with_md5
from engine.data.layout import DataLayout
from engine.data.pipeline import run_build
from engine.routing.tiles import build_tiles


def main() -> None:
    layout = DataLayout(Path(os.environ.get("DATA_DIR", "/data")))
    layout.osm_dir.mkdir(parents=True, exist_ok=True)
    pbf = layout.osm_dir / "taiwan-latest.osm.pbf"
    timeout = httpx.Timeout(30.0, read=300.0)
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        md5 = download_with_md5(GEOFABRIK_TAIWAN, pbf, client)
    build_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    meta = run_build(layout, pbf, md5, build_id, builder=build_tiles, validator=validate_build)
    print(json.dumps(asdict(meta), ensure_ascii=False))


if __name__ == "__main__":
    main()
```

- [ ] **Step 10：跑全部測試**

Run: `cd engine && uv run pytest -q`
Expected：全部通過（`31 passed`）。

- [ ] **Step 11：Commit**

```bash
git add engine/src/engine/data engine/tests
git commit -m "feat: 路網資料下載、建置、驗證與原子切換

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Kg6bv9JV9xTSx6n6doWzzc"
```

---

### Task 6：`/status` 回報目前的路網版本

**Files:**
- Modify: `engine/src/engine/app.py`
- Test: `engine/tests/test_app.py`

**Interfaces:**
- Consumes：`DataLayout`、`read_meta`、`BuildMeta`
- Produces：`GET /status` 回傳：
  - 有啟用中的路網：`{"data": {"build_id": str, "osm_md5": str, "created_at": str}}`
  - 沒有：`{"data": null}`

- [ ] **Step 1：寫失敗的測試**

在 `engine/tests/test_app.py` 最後加上：

```python
from engine.data.layout import DataLayout
from engine.data.pipeline import run_build


def test_status_without_data(tmp_path: Path) -> None:
    assert client(tmp_path).get("/status").json() == {"data": None}


def test_status_reports_active_build(tmp_path: Path) -> None:
    layout = DataLayout(tmp_path)
    meta = run_build(
        layout,
        tmp_path / "x.pbf",
        "abc",
        "20261010T000000Z",
        lambda pbf, d: (d / "valhalla.json").write_text("{}"),
        lambda d: [],
    )
    data = client(tmp_path).get("/status").json()["data"]
    assert data == {"build_id": meta.build_id, "osm_md5": "abc", "created_at": meta.created_at}
```

- [ ] **Step 2：跑測試，確認失敗**

Run: `cd engine && uv run pytest tests/test_app.py -q`
Expected：兩個新測試失敗，`/status` 回傳 404。

- [ ] **Step 3：實作**

把 `engine/src/engine/app.py` 換成：

```python
"""HTTP 介面。只在 hl 內網提供服務，由 web 轉發請求。"""

import os
from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI

from engine.data.layout import DataLayout
from engine.data.pipeline import read_meta


def create_app(data_root: Path) -> FastAPI:
    app = FastAPI(title="artroute-engine")
    layout = DataLayout(data_root)

    @app.get("/")
    def root() -> dict[str, str]:
        return {"service": "artroute-engine"}

    @app.get("/health")
    def health() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/status")
    def status() -> dict[str, dict[str, str] | None]:
        current = layout.current()
        meta = read_meta(current) if current else None
        return {"data": asdict(meta) if meta else None}

    return app


app = create_app(Path(os.environ.get("DATA_DIR", "/data")))
```

- [ ] **Step 4：跑全部測試**

Run: `cd engine && uv run pytest -q`
Expected：全部通過（`33 passed`）。

- [ ] **Step 5：Commit**

```bash
git add engine/src/engine/app.py engine/tests/test_app.py
git commit -m "feat: /status 回報目前路網版本

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Kg6bv9JV9xTSx6n6doWzzc"
```

---

### Task 7：部署到 m1，用真實台灣資料完成 P0 驗證

**Files:**
- Modify: `docs/p0-findings.md`

**Interfaces:**
- Consumes：Task 1 的 `hl deploy --internal`、Task 2 的 Dockerfile、Task 5 的 `python -m engine.data.cli`、Task 6 的 `/status`。
- Produces：m1 上的 `artroute-engine`（內網）、`/data/current` 指向通過驗證的台灣路網，以及寫完的 `docs/p0-findings.md`。

- [ ] **Step 1：部署**

```bash
cd <repo>/engine && hl deploy artroute-engine --internal
```
Expected：最後一行是 `完成：http://artroute-engine:8080 （內網：只有 hl 網路上的 app 連得到）`。

build 失敗時，看 `hl deploy` 的輸出。如果原因是 pyvalhalla 或 osmium 在 linux arm64 找不到 wheel，就是 P0 的阻斷點：記到 findings，停下來回報。

- [ ] **Step 2：在 linux arm64 上跑完整測試**

```bash
ssh m1 'export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.orbstack/bin:$PATH"; cd apps/artroute-engine/src && docker build --target test --progress=plain . 2>&1 | tail -30'
```
Expected：輸出中有 pytest 的 `33 passed`，而且 build 成功。

- [ ] **Step 3：第一次建台灣路網（背景執行）**

用 Bash 的 `run_in_background: true` 執行：

```bash
ssh m1 'export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.orbstack/bin:$PATH"; /usr/bin/time -l docker exec artroute-engine uv run --no-sync python -m engine.data.cli' 2>&1 | tail -40
```

建置過程中，另外跑一次 `ssh m1 '…; docker stats --no-stream artroute-engine'`，記下記憶體用量。
Expected：最後印出一行 JSON：`{"build_id": "…", "osm_md5": "…", "created_at": "…"}`。

如果出現 `ValidationFailed`：讀訊息，判斷是規則真的被違反，還是 OSM 資料變了。把結論寫進 findings，停下來回報，**不要修改檢查去讓它通過**。

- [ ] **Step 4：從內網確認狀態**

```bash
ssh m1 'export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.orbstack/bin:$PATH"; docker exec hl-proxy wget -qO- http://artroute-engine:8080/status; echo; du -sh ~/apps/artroute-engine/data/builds/* ~/apps/artroute-engine/data/osm'
```
Expected：`{"data":{"build_id":"…"…}}`，build_id 和 Step 3 一致；並列出 tiles 和 pbf 的大小。

- [ ] **Step 5：寫完 findings**

把 `docs/p0-findings.md` 補齊成下面的樣子（`<…>` 換成實測數字）：

```markdown
## linux arm64（m1 容器）
- pyvalhalla wheel：<可用／不可用>
- osmium wheel：<可用／不可用>
- 容器內測試：<33 passed>

## 台灣路網建置
- pbf 大小：<…>
- tiles 大小：<…>
- 建置時間：<…>
- 建置時尖峰記憶體：<…>
- 驗證查詢（洲美街單行段）：<通過>

## hl --internal
- 部署、ls、public 拒絕：<通過>

## 結論
- P0 四項（pyvalhalla on m1、台灣 tiles、bicycle 單行道、hl --internal）：<全部通過／第 N 項不通過，原因…>
- 對設計的影響：<無／需要修改 spec 的哪一節>
```

- [ ] **Step 6：Commit**

```bash
cd <repo>
git add docs/p0-findings.md
git commit -m "docs: P0 驗證結果

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Kg6bv9JV9xTSx6n6doWzzc"
```

- [ ] **Step 7：回報使用者**

用白話說明四項驗證的結果、建置時間和資源用量，以及有沒有需要修改的設計。P1 的計畫等使用者看過結果再寫。
