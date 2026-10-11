"""HTTP 介面。只在 hl 內網提供服務，由 web 轉發請求。"""

import asyncio
import logging
import os
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from engine.api.models import (
    BuildInfo,
    ErrorResponse,
    MatchRequest,
    MatchResponse,
    StatusResponse,
    ValidationErrorResponse,
)
from engine.data.layout import DataLayout
from engine.data.pipeline import read_meta
from engine.matching.deadline import Deadline, MatchCancelled, MatchTimeout
from engine.matching.service import AllStrokesFailed, MatchResult, OutsideTaiwan, StrokeInput, TargetTooSmall, match_shape
from engine.routing.router import Router, RoutingError
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


def _response(result: MatchResult, version: str) -> MatchResponse:
    return MatchResponse.model_validate(
        {
            "route": {"points": result.route_points, "length_m": result.route_length_m},
            "segments": [asdict(s) for s in result.segments],
            "score": asdict(result.score),
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


def _match(current: CurrentRouter, payload: MatchRequest, deadline: Deadline) -> MatchResponse:
    router, version = _get_router(current)
    strokes = [StrokeInput(points=list(s.points), closed=s.closed) for s in payload.strokes]
    try:
        result = match_shape(router, strokes, search_radius_m=payload.search_radius_m, deadline=deadline)
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
    admission: asyncio.Semaphore, current: CurrentRouter, payload: MatchRequest, deadline: Deadline, log: _MatchLog
) -> Any:
    if not await _admit(admission, deadline):
        return Response(status_code=CLIENT_CLOSED)
    log.admitted = time.monotonic()
    try:
        response = await run_in_threadpool(_match, current, payload, deadline)
        log.segments = len(response.segments)
        return response
    except MatchCancelled:
        return Response(status_code=CLIENT_CLOSED)
    finally:
        log.finished = time.monotonic()
        # run_in_threadpool 不會丟下還在跑的 thread（即使這個 task 被取消也會等它結束），
        # 所以名額一定在吸附真的結束後才釋放，Router 不會被兩個請求同時使用
        admission.release()


def create_app(
    data_root: Path,
    *,
    router_factory: Callable[[Path], Any] = Router.from_build,
    match_timeout_s: float = 5.0,
) -> FastAPI:
    app = FastAPI(title="artroute-engine")
    app.add_exception_handler(RequestValidationError, _invalid_request)
    layout = DataLayout(data_root)
    current = CurrentRouter(layout, router_factory)
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
        return StatusResponse(data=BuildInfo.model_validate(asdict(meta)) if meta else None)

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
            response = await _admitted_match(admission, current, payload, deadline, log)
            status = response.status_code if isinstance(response, Response) else 200
            return response
        except HTTPException as error:
            status = error.status_code
            raise
        finally:
            watcher.cancel()
            log.emit(status, deadline.cancelled)

    return app


# uvicorn 只設定自己的 logger（uvicorn.*），engine.* 的紀錄要經 root logger 才會輸出。
# root 已經有 handler 時（pytest、自訂的 log config）basicConfig 什麼都不做
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
app = create_app(Path(os.environ.get("DATA_DIR", "/data")))
