"""HTTP 介面。只在 hl 內網提供服務，由 web 轉發請求。"""

import logging
import os
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from engine.api.models import BuildInfo, MatchRequest, MatchResponse, StatusResponse
from engine.data.layout import DataLayout
from engine.data.pipeline import read_meta
from engine.matching.deadline import Deadline, MatchTimeout
from engine.matching.service import AllStrokesFailed, MatchResult, OutsideTaiwan, StrokeInput, TargetTooSmall, match_shape
from engine.routing.router import Router, RoutingError
from engine.service.current import CurrentRouter, NoDataError

logger = logging.getLogger(__name__)

NO_DATA_MESSAGE = "路網資料尚未就緒，請稍後再試"  # 固定字串：RoutingError 的訊息含 build 路徑，不能回給呼叫端


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
    """維持 FastAPI 預設的 422 格式 {"detail": [...]}，但每筆只留 type / loc / msg。

    預設會把出錯的 input 原樣回傳：NaN / Infinity 無法序列化成 JSON（整個變成 500），
    而且會把超大的請求內容整份回聲回去。
    """
    detail = [{"type": e["type"], "loc": list(e["loc"]), "msg": e["msg"]} for e in error.errors()]
    return JSONResponse(status_code=422, content={"detail": detail})


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

    @app.get("/")
    def root() -> dict[str, str]:
        return {"service": "artroute-engine"}

    @app.get("/health")
    def health() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/status")
    def status() -> StatusResponse:
        build = layout.current()
        meta = read_meta(build) if build else None
        return StatusResponse(data=BuildInfo(**asdict(meta)) if meta else None)

    @app.post("/match")
    def match(request: MatchRequest) -> MatchResponse:
        deadline = Deadline(match_timeout_s)  # 從進入請求開始算，含等 Router 鎖的時間
        try:
            router, version = current.get()
        except NoDataError as error:
            raise _error(503, "no_data", NO_DATA_MESSAGE) from error
        except RoutingError as error:  # current 的 build 載入失敗：不退回舊版，下個請求會重試
            logger.error("載入目前的路網失敗：%s", error)
            raise _error(503, "no_data", NO_DATA_MESSAGE) from error
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
            raise _error(504, "timeout", "吸附超過時間上限，請縮小圖形或減少部件") from error
        return _response(result, version)

    return app


app = create_app(Path(os.environ.get("DATA_DIR", "/data")))
