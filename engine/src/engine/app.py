"""HTTP 介面。只在 hl 內網提供服務，由 web 轉發請求。"""

import asyncio
import functools
import logging
import math
import os
import tempfile
import threading
import time
from collections.abc import AsyncIterable, Callable, Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.sse import EventSourceResponse, ServerSentEvent
from starlette.concurrency import run_in_threadpool

from engine.api.models import (
    BuildInfo,
    ErrorResponse,
    MatchRequest,
    MatchResponse,
    PlacementOut,
    SearchBest,
    SearchCandidate,
    SearchEvent,
    SearchFailed,
    SearchProgress,
    SearchQueued,
    SearchRequest,
    SearchResult,
    StatusResponse,
    ValidationErrorResponse,
)
from engine.data.layout import DataLayout
from engine.data.pipeline import read_meta
from engine.matching.deadline import Deadline, MatchCancelled, MatchTimeout
from engine.matching.service import (
    TAIWAN_BBOX,
    AllStrokesFailed,
    MatchResult,
    OutsideTaiwan,
    StrokeInput,
    TargetTooSmall,
    match_shape,
)
from engine.matching.types import StartHint
from engine.planner.graph import GraphDataMissing, RideGraph, has_graph_data
from engine.routing.router import FAST_TURN_PENALTY_FACTOR, Router, RoutingError
from engine.search.gate import SearchBusy, SearchGate
from engine.search.roads import RoadDataMissing, RoadIndex, has_road_data
from engine.search.service import run_search
from engine.search.types import Best, Candidate, NormStroke, Progress, SearchEventOut, SearchOutcome, SearchParams
from engine.service.current import CurrentRouter, NoDataError

logger = logging.getLogger(__name__)

NO_DATA_MESSAGE = "路網資料尚未就緒，請稍後再試"  # 固定字串：RoutingError 的訊息含 build 路徑，不能回給呼叫端
TIMEOUT_MESSAGE = "吸附超過時間上限，請縮小圖形或減少部件"
MAX_VALIDATION_ISSUES = 20
DISCONNECT_POLL_S = 0.05  # 多久看一次呼叫端還在不在（也是等名額時重新檢查的間隔）
CLIENT_CLOSED = 499  # nginx 的慣例：呼叫端已經離開，這個回應不會有人收到（uvicorn 也不會送出）

MATCH_ERRORS: dict[int | str, dict[str, Any]] = {
    400: {"model": ErrorResponse, "description": "圖形不在台灣範圍內（outside_taiwan）"},
    # 422 有兩種形狀：領域錯誤是物件（too_small、no_route），請求格式錯誤是陣列
    422: {"model": ErrorResponse | ValidationErrorResponse, "description": "圖形太小、找不到路線，或請求格式錯誤"},
    503: {"model": ErrorResponse, "description": "路網資料尚未就緒（no_data）"},
    504: {"model": ErrorResponse, "description": "吸附超過時間上限（timeout）"},
}


def _error(status: int, code: str, message: str, **extra: Any) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message, **extra})


def _json_error(description: str, *schemas: str) -> dict[str, Any]:
    """/search 的錯誤回應是 JSON（串流開始前就回）；不能用 "model"：FastAPI 會把它記成路由的 text/event-stream。
    引用的 schema 已經因為 /match 而在 components 裡。"""
    refs = [{"$ref": f"#/components/schemas/{name}"} for name in schemas]
    schema = refs[0] if len(refs) == 1 else {"anyOf": refs}
    return {"description": description, "content": {"application/json": {"schema": schema}}}


SEARCH_ERRORS: dict[int | str, dict[str, Any]] = {
    400: _json_error("圓心不在台灣範圍內（outside_taiwan）", "ErrorResponse"),
    422: _json_error("圖形太小（too_small），或請求格式錯誤", "ErrorResponse", "ValidationErrorResponse"),
    503: _json_error("路網資料尚未就緒（no_data），或目前的路網沒有搜尋用的道路資料（no_search_data）", "ErrorResponse"),
}
SEARCH_TIMEOUT_MESSAGE = "搜尋超過時間上限，請縮小半徑或放寬條件"
SEARCH_INTERNAL_MESSAGE = "搜尋發生內部錯誤"
NO_SEARCH_DATA_MESSAGE = "目前的路網還沒有自動搜尋需要的道路資料，要等路網重建"
GRAPH_RETRY_S = 60  # 路網圖的檔案都在卻讀不了時，多久之後再試（_CurrentGraph）
MIN_SHAPE_EXTENT = 1e-3  # 正規化座標的外框長邊下限：更小代表所有點幾乎重合，量不出大小


def _response(result: MatchResult, version: str) -> MatchResponse:
    return MatchResponse.model_validate(
        {
            "route": {"points": result.route_points, "length_m": result.route_length_m},
            "segments": [asdict(s) for s in result.segments],
            # 只送契約欄位：Score.exact（沒取整數的 overall）是引擎內部排名用的
            "score": {
                "coverage": result.score.coverage,
                "cleanliness": result.score.cleanliness,
                "direction": result.score.direction,
                "overall": result.score.overall,
            },
            "target_length_m": result.target_length_m,
            "warnings": [asdict(w) for w in result.warnings],
            "data_version": version,
        }
    )


async def _invalid_request(request: Request, error: RequestValidationError) -> JSONResponse:
    """維持 FastAPI 預設的 422 格式 {"detail": [...]}，但每筆只留 type / loc / msg，且最多回前 20 筆。

    預設會把出錯的 input 原樣回傳：NaN / Infinity 無法序列化成 JSON（整個變成 500），
    而且會把超大的請求內容整份回聲回去；壞點很多時錯誤筆數也會跟著放大（40 × 2000 點可達數十 MB）。
    """
    issues = error.errors()[:MAX_VALIDATION_ISSUES]
    detail = [{"type": e["type"], "loc": list(e["loc"]), "msg": e["msg"]} for e in issues]
    return JSONResponse(status_code=422, content={"detail": detail})


def _get_router(current: CurrentRouter) -> tuple[Any, str]:
    try:
        return current.get()
    except (NoDataError, RoutingError) as error:
        if isinstance(error, RoutingError):  # current 的 build 載入失敗：不退回舊版，下個請求會重試
            logger.error("載入目前的路網失敗：%s", error)
        raise _error(503, "no_data", NO_DATA_MESSAGE) from error


def _match(
    current: CurrentRouter, payload: MatchRequest, deadline: Deadline, graph: Callable[[str], Any] | None = None
) -> MatchResponse:
    """graph：build 名稱 → 那個 build 的可騎路網圖（沒有就 None）。只有 quality="fine" 才讀，fast 不碰。"""
    router, version = _get_router(current)
    strokes = [StrokeInput(points=list(s.points), closed=s.closed) for s in payload.strokes]
    start = None if payload.start_circle is None else StartHint(payload.start_circle.center, payload.start_circle.radius_m)
    fine = payload.quality == "fine"
    ride_graph = graph(version) if fine and graph is not None else None
    try:
        if fine:
            result = match_shape(
                router, strokes, search_radius_m=payload.search_radius_m, deadline=deadline, start=start,
                quality="fine", graph=ride_graph,
            )
        else:
            result = match_shape(router, strokes, search_radius_m=payload.search_radius_m, deadline=deadline, start=start)
    except OutsideTaiwan as error:
        raise _error(400, "outside_taiwan", str(error)) from error
    except TargetTooSmall as error:
        raise _error(422, "too_small", str(error)) from error
    except AllStrokesFailed as error:
        raise _error(422, "no_route", str(error), warnings=[asdict(w) for w in error.warnings]) from error
    except MatchTimeout as error:
        raise _error(504, "timeout", TIMEOUT_MESSAGE) from error
    return _response(result, version)


async def _watch_disconnect(request: Request, deadline: Deadline) -> None:
    """唯一的斷線來源：排隊與吸附兩個階段共用。呼叫端一離開就取消 deadline。

    is_disconnected 不會吃掉別的訊息：body 在解依賴前就讀完了，uvicorn 之後的 receive 只會是 http.disconnect。
    """
    while not await request.is_disconnected():
        await asyncio.sleep(DISCONNECT_POLL_S)
    deadline.cancel()


async def _admit(admission: asyncio.Semaphore, deadline: Deadline) -> bool:
    """等名額：拿到回傳 True；等待中呼叫端離開回傳 False；到期丟 504。

    以 DISCONNECT_POLL_S 為單位重試，每次之間檢查取消與期限，所以不保證先到先得：
    即時吸附只在乎最新的請求，每個等待者又各有期限，可以接受。
    acquire 被 timeout 取消時，若名額其實已經交到手上，asyncio.Semaphore 會自己還回去。
    """
    while not deadline.cancelled:
        remaining = deadline.remaining()
        if remaining <= 0:
            raise _error(504, "timeout", TIMEOUT_MESSAGE)
        try:
            async with asyncio.timeout(min(DISCONNECT_POLL_S, remaining)):
                await admission.acquire()
        except TimeoutError:
            continue
        if deadline.cancelled:  # 拿到名額的同一刻呼叫端離開了
            admission.release()
            return False
        return True
    return False


@dataclass
class _MatchLog:
    """一個 /match 的紀錄：結束時寫一行 INFO，拿來確認取消有效、之後調查吸附品質。"""

    strokes: int
    arrived: float = field(default_factory=time.monotonic)
    admitted: float | None = None  # 拿到名額的時刻；沒拿到（離開、逾時）就是 None
    finished: float | None = None
    segments: int | None = None  # 吸附成功才有

    def emit(self, status: int, cancelled: bool) -> None:
        now = time.monotonic()
        if self.admitted is None:
            wait_ms, match_ms = (now - self.arrived) * 1000, 0.0
        else:
            finished = now if self.finished is None else self.finished
            wait_ms, match_ms = (self.admitted - self.arrived) * 1000, (finished - self.admitted) * 1000
        segments = "-" if self.segments is None else self.segments
        logger.info(
            "match status=%d wait_ms=%.0f match_ms=%.0f strokes=%d segments=%s cancelled=%s",
            status, wait_ms, match_ms, self.strokes, segments, cancelled,
        )


async def _admitted_match(
    admission: asyncio.Semaphore,
    current: CurrentRouter,
    payload: MatchRequest,
    deadline: Deadline,
    log: _MatchLog,
    graph: Callable[[str], Any] | None = None,
) -> Any:
    if not await _admit(admission, deadline):
        return Response(status_code=CLIENT_CLOSED)
    log.admitted = time.monotonic()
    try:
        response = await run_in_threadpool(_match, current, payload, deadline, graph)
        log.segments = len(response.segments)
        return response
    except MatchCancelled:
        return Response(status_code=CLIENT_CLOSED)
    finally:
        log.finished = time.monotonic()
        # run_in_threadpool 不會丟下還在跑的 thread（即使這個 task 被取消也會等它結束），
        # 所以名額一定在吸附真的結束後才釋放，Router 不會被兩個請求同時使用
        admission.release()


class _CurrentRoads:
    """每個 worker 一份：目前 build 的可騎道路索引（mmap），換版時重新載入。"""

    def __init__(self, loader: Callable[[Path], Any]) -> None:
        self._loader = loader
        self._lock = threading.Lock()
        self._build: Path | None = None
        self._roads: Any = None

    def get(self, build: Path) -> Any:
        with self._lock:
            if build != self._build or self._roads is None:
                self._roads = self._loader(build)  # 沒有資料丟 RoadDataMissing
                self._build = build
            return self._roads


class _CurrentGraph:
    """每個 worker 一份：目前 build 的可騎路網圖（mmap），換版時重新載入；沒有圖就是 None（fine 走 fast），不報錯。

    載入失敗分兩種：
    - build 本來就沒有圖的檔案（舊 build）：記住 None 到換 build 為止，fine 請求不會每次都去碰檔案系統。
    - 檔案都在卻讀不了：可能是暫時的（開檔數用完、記憶體不足），也可能是建圖驗證漏掉的壞檔。記 ERROR，
      GRAPH_RETRY_S 秒內走 fast、之後的請求再試一次（ERROR 每個 worker 最多每 GRAPH_RETRY_S 一則）。
      不記到換 build：暫時性的錯誤會讓這個 worker 一直用 fast 直到下次重建路網；也不每個請求都重試：壞檔時每個
      fine 請求都要多讀一次、多一則 ERROR。
    """

    def __init__(
        self, layout: DataLayout, loader: Callable[[Path], Any], *, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._layout = layout
        self._loader = loader
        self._clock = clock
        self._lock = threading.Lock()
        self._build: Path | None = None
        self._graph: Any = None
        self._retry_at: float | None = None  # 檔案在卻讀不了時，什麼時候再試（None：不再試）

    def get(self, version: str) -> Any:
        """version 是 Router 所屬 build 的名稱（CurrentRouter.get 回傳的）。current 已經換成別的 build 時回 None：
        圖與 Router 必須來自同一個 build，對不上就這一次用 fast，不拿新版的圖配舊版的路網。"""
        with self._lock:
            build = self._layout.current()
            if build is None or build.name != version:
                return None
            retry = self._retry_at is not None and self._clock() >= self._retry_at
            if build != self._build or retry:
                self._build, self._graph, self._retry_at = build, None, None
                try:
                    self._graph = self._loader(build)
                except GraphDataMissing as error:
                    if has_graph_data(build):
                        logger.error("可騎路網圖載入失敗，%d 秒內 fine 退回 fast：%s", GRAPH_RETRY_S, error)
                        self._retry_at = self._clock() + GRAPH_RETRY_S
            return self._graph


@dataclass(frozen=True)
class _PreparedSearch:
    params: SearchParams
    build: Path
    roads: Any


def _search_params(payload: SearchRequest) -> SearchParams:
    return SearchParams(
        strokes=[NormStroke(points=[(x, y) for x, y in s.points], closed=s.closed) for s in payload.strokes],
        center=payload.center,
        radius_m=payload.radius_m,
        min_km=payload.min_km,
        max_km=payload.max_km,
        rotation_min_deg=payload.rotation_min_deg,
        rotation_max_deg=payload.rotation_max_deg,
    )


def _rotation_out(deg: float) -> float:
    """[0, 360)。極小的負角度（例如 -1.4e-14）% 360 會得到 360.0，超出契約（審查），歸成 0。"""
    out = deg % 360.0
    return 0.0 if out >= 360.0 else out


def _candidate_out(candidate: Candidate, version: str) -> SearchCandidate:
    placement = candidate.placement
    return SearchCandidate(
        placement=PlacementOut(
            center=placement.center, size_m=placement.size_m, rotation_deg=_rotation_out(placement.rotation_deg)
        ),
        match=_response(candidate.result, version),
        start_distance_m=candidate.start_distance_m,
        length_ok=candidate.length_ok,
    )


def _event_out(event: SearchEventOut, version: str) -> SearchProgress | SearchBest:
    if isinstance(event, Progress):
        return SearchProgress(stage=event.stage, done=event.done, total=event.total)
    if isinstance(event, Best):
        return SearchBest(candidate=_candidate_out(event.candidate, version))
    raise TypeError(f"未知的搜尋事件：{event!r}")


def _result_out(outcome: SearchOutcome, version: str, elapsed_s: float) -> SearchResult:
    return SearchResult(
        candidates=[_candidate_out(c, version) for c in outcome.candidates],
        coarse_candidates=outcome.coarse_candidates,
        evaluated=outcome.evaluated,
        elapsed_ms=round(elapsed_s * 1000),
        notice=outcome.notice,
    )


def _sse(event: Any) -> ServerSentEvent:
    return ServerSentEvent(data=event, event=event.type)


def _env_number(name: str, raw: str) -> float:
    try:
        value = float(raw)
    except ValueError:
        raise ValueError(f"環境變數 {name} 必須是數字，收到 {raw!r}") from None
    if not math.isfinite(value):
        raise ValueError(f"環境變數 {name} 必須是有限的數字，收到 {raw!r}")
    return value


def trace_turn_penalty_factor(raw: str | None) -> float | None:
    """TRACE_TURN_PENALTY_FACTOR：不設 = FAST_TURN_PENALTY_FACTOR；`none`（不分大小寫）= 不帶，由 meili 設定決定；
    其他必須是 ≥ 0 的數字。"""
    if raw is None:
        return FAST_TURN_PENALTY_FACTOR
    if raw.strip().lower() == "none":
        return None
    value = _env_number("TRACE_TURN_PENALTY_FACTOR", raw)
    if value < 0:
        raise ValueError(f"環境變數 TRACE_TURN_PENALTY_FACTOR 不能是負數，收到 {raw!r}")
    return value


def meili_sigma_z(raw: str | None) -> float | None:
    """MEILI_SIGMA_Z：不設 = 建圖時的設定（Valhalla 預設 4.07 m）；設了必須是 > 0 的數字。"""
    if raw is None:
        return None
    value = _env_number("MEILI_SIGMA_Z", raw)
    if value <= 0:
        raise ValueError(f"環境變數 MEILI_SIGMA_Z 必須大於 0，收到 {raw!r}")
    return value


def router_factory_from_env(environ: Mapping[str, str]) -> Callable[[Path], Router]:
    """吸附參數（P2.5 spec §5.1）從環境變數來；壞值直接丟 ValueError，服務起不來，不悄悄退回預設。"""
    return functools.partial(
        Router.from_build,
        sigma_z=meili_sigma_z(environ.get("MEILI_SIGMA_Z")),
        turn_penalty_factor=trace_turn_penalty_factor(environ.get("TRACE_TURN_PENALTY_FACTOR")),
    )


def create_app(
    data_root: Path,
    *,
    router_factory: Callable[[Path], Any] = Router.from_build,
    match_timeout_s: float = 5.0,
    road_loader: Callable[[Path], Any] = RoadIndex.load,
    graph_loader: Callable[[Path], Any] = RideGraph.load,
    search_timeout_s: float = 60.0,
    search_queue_timeout_s: float = 120.0,
    search_routers: int = 2,
    search_gate: SearchGate | None = None,
    search: Callable[..., SearchOutcome] = run_search,
) -> FastAPI:
    app = FastAPI(title="artroute-engine")
    app.add_exception_handler(RequestValidationError, _invalid_request)
    layout = DataLayout(data_root)
    current = CurrentRouter(layout, router_factory)
    graph_cache = _CurrentGraph(layout, graph_loader)
    # 每個 worker 同時只進行一個吸附：Router 本來就把 Valhalla 呼叫序列化，所以不損失吞吐量；
    # 多出來的請求在事件迴圈上限時等待（不佔 thread），不會各自卡在 Router 鎖上，到期後還各多跑一次 Valhalla 呼叫
    admission = asyncio.Semaphore(1)

    async def start_deadline() -> Deadline:
        # 必須是 async：同步的依賴會自己先去排 threadpool 的隊，起算點就太晚了
        return Deadline(match_timeout_s)

    # 以下兩個不碰 I/O，用 async 直接在事件迴圈上回應
    @app.get("/")
    async def root() -> dict[str, str]:
        return {"service": "artroute-engine"}

    @app.get("/health")
    async def health() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/status")
    def status() -> StatusResponse:
        build = layout.current()
        meta = read_meta(build) if build else None
        return StatusResponse(
            data=BuildInfo.model_validate(asdict(meta)) if meta else None,
            search_ready=bool(meta and build and has_road_data(build)),
            planner_ready=bool(meta and build and has_graph_data(build)),
        )

    @app.post("/match", response_model=MatchResponse, responses=MATCH_ERRORS)
    async def match(
        request: Request, payload: MatchRequest, deadline: Annotated[Deadline, Depends(start_deadline)]
    ) -> Any:
        # 期限在解依賴時起算（請求本文已讀完），等名額與吸附的時間都算在內；等名額不佔 thread。
        # 不含：讀取請求本文的時間、已經送進 Valhalla 的那一次呼叫（無法中斷）、已經在進行的 Router 載入。
        # 最壞情況：到期（或呼叫端離開）後，最多再多一次進行中的 Valhalla 呼叫，或串接排序（2-opt）的一列。
        # 呼叫端離開（web 放開、微調或離開頁面時取消在途的請求）：排隊中的立刻離開佇列，吸附中的在下一次檢查時停下，都回 499。
        log, status = _MatchLog(strokes=len(payload.strokes)), 500  # 沒被下面接住的例外會變成 500
        watcher = asyncio.create_task(_watch_disconnect(request, deadline))
        try:
            response = await _admitted_match(admission, current, payload, deadline, log, graph_cache.get)
            status = response.status_code if isinstance(response, Response) else 200
            return response
        except HTTPException as error:
            status = error.status_code
            raise
        finally:
            watcher.cancel()
            log.emit(status, deadline.cancelled)

    roads_cache = _CurrentRoads(road_loader)
    # 跨 worker 行程共用的名額：同一個容器裡的 worker 共用 /tmp
    gate = search_gate or SearchGate(Path(tempfile.gettempdir()) / "artroute-search.lock")

    async def prepare_search(payload: SearchRequest) -> _PreparedSearch:
        """開始串流之前的檢查：出錯時回一般的 JSON 錯誤（串流開始後狀態碼就改不了了）。"""
        min_lon, min_lat, max_lon, max_lat = TAIWAN_BBOX
        lon, lat = payload.center
        if not (min_lon <= lon <= max_lon and min_lat <= lat <= max_lat):
            raise _error(400, "outside_taiwan", "圓心不在台灣範圍內")
        xs = [x for s in payload.strokes for x, _ in s.points]
        ys = [y for s in payload.strokes for _, y in s.points]
        # 每一筆都是同一點重複、但筆與筆分開時外框不小，總長卻是 0（審查）：兩個都要擋
        length = sum(
            math.dist(a, b) for s in payload.strokes for a, b in zip(s.points, [*s.points[1:], *s.points[:1] * s.closed])
        )
        if max(max(xs) - min(xs), max(ys) - min(ys)) < MIN_SHAPE_EXTENT or length < MIN_SHAPE_EXTENT:
            raise _error(422, "too_small", "圖形的所有點幾乎重合，量不出大小")
        build = layout.current()
        if build is None or not build.is_dir():
            raise _error(503, "no_data", NO_DATA_MESSAGE)
        try:
            roads = await run_in_threadpool(roads_cache.get, build)
        except RoadDataMissing as error:
            raise _error(503, "no_search_data", NO_SEARCH_DATA_MESSAGE) from error
        return _PreparedSearch(_search_params(payload), build, roads)

    def _run(prepared: _PreparedSearch, deadline: Deadline, emit: Callable[[SearchEventOut], None]) -> SearchOutcome:
        # 搜尋用自己的 Router（各自的 Actor）：不佔 /match 的名額，即時吸附不必等整個搜尋跑完
        routers = [router_factory(prepared.build) for _ in range(max(1, search_routers))]
        return search(prepared.params, routers, prepared.roads, deadline, emit)

    @app.post("/search", response_class=EventSourceResponse, responses=SEARCH_ERRORS)
    async def search_endpoint(prepared: Annotated[_PreparedSearch, Depends(prepare_search)]) -> AsyncIterable[SearchEvent]:
        """自動搜尋（SSE）。事件依序：queued（排隊時）→ progress／best（多次）→ result 或 error（最後一個）。
        呼叫端離開（連線中斷）時，排隊中的立刻離開，搜尋中的在下一次檢查時停下。"""
        version = prepared.build.name
        notices: asyncio.Queue[int] = asyncio.Queue()
        acquiring = asyncio.create_task(gate.acquire(search_queue_timeout_s, lambda: False, notices.put_nowait))
        handed_over = False
        try:
            while not acquiring.done():
                notice = asyncio.ensure_future(notices.get())
                await asyncio.wait({acquiring, notice}, return_when=asyncio.FIRST_COMPLETED)
                if notice.done():
                    yield _sse(SearchQueued(position=notice.result()))
                else:
                    notice.cancel()
            try:
                acquiring.result()
            except SearchBusy as error:
                yield _sse(SearchFailed(code="busy", message=str(error)))
                return
            except Exception:  # 例如鎖檔開不了：串流仍要以失敗事件結束
                logger.exception("搜尋拿名額失敗")
                yield _sse(SearchFailed(code="internal", message=SEARCH_INTERNAL_MESSAGE))
                return
            handed_over = True
        finally:
            if not acquiring.done():
                acquiring.cancel()  # 排隊中呼叫端離開：離開佇列
            elif not handed_over and not acquiring.cancelled() and acquiring.exception() is None:
                gate.release()  # 拿到名額的同一刻呼叫端離開了
        loop = asyncio.get_running_loop()
        events: asyncio.Queue[Any] = asyncio.Queue()
        deadline = Deadline(search_timeout_s)
        started = time.monotonic()
        done = object()

        def send(item: Any) -> None:
            loop.call_soon_threadsafe(events.put_nowait, item)

        def job() -> None:
            """在 thread 裡跑：進度、結果、錯誤與結束記號全都走 send（同一個 thread 依序 call_soon_threadsafe），
            順序一定和產生的順序相同。結果若改由 to_thread 回傳，可能比還在路上的進度事件早到（測試實際遇過）。"""
            try:
                outcome = _run(prepared, deadline, send)
                send(_result_out(outcome, version, time.monotonic() - started))
            except MatchTimeout:
                send(SearchFailed(code="timeout", message=SEARCH_TIMEOUT_MESSAGE))
            except MatchCancelled:
                pass
            except RoutingError as error:
                logger.error("搜尋載入路網失敗：%s", error)
                send(SearchFailed(code="no_data", message=NO_DATA_MESSAGE))
            except Exception:
                logger.exception("搜尋失敗")
                send(SearchFailed(code="internal", message=SEARCH_INTERNAL_MESSAGE))
            finally:
                send(done)

        async def work() -> None:
            await asyncio.to_thread(job)

        task = asyncio.create_task(work())
        # 名額在搜尋的 thread 真的結束後才釋放（呼叫端先離開也一樣），下一個搜尋不會和它重疊
        task.add_done_callback(lambda _: gate.release())
        finished = False
        try:
            while (event := await events.get()) is not done:
                yield _sse(event if isinstance(event, SearchResult | SearchFailed) else _event_out(event, version))
            finished = True
        finally:
            deadline.cancel()  # 呼叫端離開（產生器被取消）時讓搜尋在下一次檢查停下；正常結束時沒有作用
            logger.info(
                "search center=%s radius_m=%.0f elapsed_ms=%.0f cancelled=%s",
                prepared.params.center, prepared.params.radius_m, (time.monotonic() - started) * 1000, not finished,
            )

    return app


# uvicorn 只設定自己的 logger（uvicorn.*），engine.* 的紀錄要經 root logger 才會輸出。
# root 已經有 handler 時（pytest、自訂的 log config）basicConfig 什麼都不做
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
app = create_app(
    Path(os.environ.get("DATA_DIR", "/data")),
    # /match 與搜尋的 Router 都由 router_factory 建，兩邊用同一組吸附參數
    router_factory=router_factory_from_env(os.environ),
    # 搜尋精算用幾個 Router（各一個 Valhalla Actor、各自的 tile 快取上限 256 MiB，搜尋結束就釋放）
    search_routers=int(os.environ.get("SEARCH_ROUTERS", "2")),
)
