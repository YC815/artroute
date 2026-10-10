"""/match 與 /status 的請求、回應模型（spec §4.7）。"""

from typing import Annotated, Literal

from pydantic import BaseModel, Field

# JSON 解析器會放行 NaN / Infinity，要在模型層擋掉，否則它們會一路流進地理計算
Finite = Annotated[float, Field(allow_inf_nan=False)]
LonLatPair = tuple[Finite, Finite]


class StrokeIn(BaseModel):
    points: list[LonLatPair] = Field(min_length=2, max_length=2000)
    closed: bool = False


class MatchRequest(BaseModel):
    strokes: list[StrokeIn] = Field(min_length=1, max_length=40)
    search_radius_m: float | None = Field(default=None, ge=30, le=150, allow_inf_nan=False)


class RouteOut(BaseModel):
    points: list[LonLatPair]
    length_m: float


class SegmentOut(BaseModel):
    kind: Literal["stroke", "detour", "connector"]
    stroke_index: int | None
    points: list[LonLatPair]
    length_m: float


class ScoreOut(BaseModel):
    coverage: int
    cleanliness: int
    overall: int


class WarningOut(BaseModel):
    stroke_index: int
    code: Literal["no_route", "partial"]
    message: str


class MatchResponse(BaseModel):
    route: RouteOut
    segments: list[SegmentOut]
    score: ScoreOut
    target_length_m: float
    warnings: list[WarningOut]
    data_version: str


class BuildInfo(BaseModel):
    build_id: str
    osm_md5: str
    created_at: str


class StatusResponse(BaseModel):
    data: BuildInfo | None
