"""HTTP 介面。只在 hl 內網提供服務，由 web 轉發請求。"""

import logging
import os
import threading
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

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
from engine.matching.deadline import Deadline, MatchTimeout
from engine.matching.service import AllStrokesFailed, MatchResult, OutsideTaiwan, StrokeInput, TargetTooSmall, match_shape
from engine.routing.router import Router, RoutingError
from engine.service.current import CurrentRouter, NoDataError

logger = logging.getLogger(__name__)

NO_DATA_MESSAGE = "路網資料尚未就緒，請稍後再試"  # 固定字串：RoutingError 的訊息含 build 路徑，不能回給呼叫端
TIMEOUT_MESSAGE = "吸附超過時間上限，請縮小圖形或減少部件"
MAX_VALIDATION_ISSUES = 20

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


def _match(current: CurrentRouter, request: MatchRequest, deadline: Deadline) -> MatchResponse:
    router, version = _get_router(current)
    strokes = [StrokeInput(points=list(s.points), closed=s.closed) for s in request.strokes]
    try:
        result = match_shape(router, strokes, search_radius_m=request.search_radius_m, deadline=deadline)
    except OutsideTaiwan as error:
        raise _error(400, "outside_taiwan", str(error)) from error
    except TargetTooSmall as error:
        raise _error(422, "too_small", str(error)) from error
    except AllStrokesFailed as error:
        raise _error(422, "no_route", str(error), warnings=[asdict(w) for w in error.warnings]) from error
    except MatchTimeout as error:
        raise _error(504, "timeout", TIMEOUT_MESSAGE) from error
    return _response(result, version)


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
    # 多出來的請求在這裡限時等待，不會各自卡在 Router 鎖上，到期後還各多跑一次 Valhalla 呼叫
    admission = threading.Semaphore(1)

    async def start_deadline() -> Deadline:
        # 必須是 async：同步的依賴會自己先去排 threadpool 的隊，起算點就太晚了
        return Deadline(match_timeout_s)

    # 以下兩個不碰 I/O，用 async 直接在事件迴圈上回應：threadpool 被等名額的請求佔滿時，健康檢查仍能即時回
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

    @app.post("/match", responses=MATCH_ERRORS)
    def match(request: MatchRequest, deadline: Annotated[Deadline, Depends(start_deadline)]) -> MatchResponse:
        # 期限在請求進 app、排進 threadpool 之前就起算，所以 threadpool 的排隊時間與等名額的時間都算在內。
        # 不含：讀取請求本文的時間、已經送進 Valhalla 的那一次呼叫（無法中斷）、已經在進行的 Router 載入。
        # 最壞情況：到期後最多再等一次進行中的 Valhalla 呼叫。
        if not admission.acquire(timeout=deadline.remaining()):
            raise _error(504, "timeout", TIMEOUT_MESSAGE)
        try:
            return _match(current, request, deadline)
        finally:
            admission.release()

    return app


app = create_app(Path(os.environ.get("DATA_DIR", "/data")))
