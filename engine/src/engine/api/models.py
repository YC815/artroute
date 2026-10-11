"""/match、/search 與 /status 的請求、回應模型（spec §4.7）。"""

from typing import Annotated, Literal, Self

from pydantic import BaseModel, Field, model_validator

from engine.routing.tiles import MAX_SEARCH_RADIUS_M, MIN_SEARCH_RADIUS_M

# JSON 解析器會放行 NaN / Infinity，要在模型層擋掉，否則它們會一路流進地理計算
Finite = Annotated[float, Field(allow_inf_nan=False)]
LonLatPair = tuple[Finite, Finite]


class StrokeIn(BaseModel):
    points: list[LonLatPair] = Field(min_length=2, max_length=2000)
    closed: bool = False


# 自動搜尋的範圍（spec §4.6）：圓的半徑、作品路線本身的公里數（唯一定義處；web 的 limits.ts 以契約測試對齊）
MIN_SEARCH_AREA_RADIUS_M = 300
MAX_SEARCH_AREA_RADIUS_M = 10_000
MIN_SEARCH_KM = 1
MAX_SEARCH_KM = 300


class StartCircle(BaseModel):
    """路線起點想落在的圓（自動搜尋的範圍）。只是提示：串接器優先從圈內、離圓心最近的進入點出發，不保證做得到。"""

    center: LonLatPair
    radius_m: float = Field(ge=MIN_SEARCH_AREA_RADIUS_M, le=MAX_SEARCH_AREA_RADIUS_M, allow_inf_nan=False)


class MatchRequest(BaseModel):
    strokes: list[StrokeIn] = Field(min_length=1, max_length=40)
    search_radius_m: float | None = Field(
        default=None, ge=MIN_SEARCH_RADIUS_M, le=MAX_SEARCH_RADIUS_M, allow_inf_nan=False
    )
    # P2 新增，可省略：省略時與 P1 的行為完全相同
    start_circle: StartCircle | None = None
    # P2.5 新增（spec §6.5）：fast = 現行吸附；fine = 描形規劃器（目前的路網沒有路網圖時等於 fast）。省略（null）= fast
    quality: Literal["fast", "fine"] | None = None


class RouteOut(BaseModel):
    points: list[LonLatPair]
    length_m: float = Field(ge=0)


# edit：手動微調換上的段落，只有 /edit、/verify 會回（route editing spec §3.2）
SegmentKind = Literal["stroke", "detour", "connector", "edit"]


class SegmentOut(BaseModel):
    kind: SegmentKind
    stroke_index: int | None
    points: list[LonLatPair]
    length_m: float = Field(ge=0)


class ScoreOut(BaseModel):
    coverage: int = Field(ge=0, le=100)
    cleanliness: int = Field(ge=0, le=100)
    # P2.5（評分 v3）：路線方向與原圖方向的吻合度，量鋸齒與轉角
    direction: int = Field(ge=0, le=100)
    overall: int = Field(ge=0, le=100)


class WarningOut(BaseModel):
    stroke_index: int
    code: Literal["no_route", "partial"]
    message: str


class MatchResponse(BaseModel):
    route: RouteOut
    segments: list[SegmentOut]
    score: ScoreOut
    target_length_m: float = Field(ge=0)
    warnings: list[WarningOut]
    data_version: str
    # 整條路線（data_version＋依序的每段）的 HMAC-SHA256（route editing spec §3.4）：/edit、/verify 原封不動帶回來
    route_sig: str


# ---- POST /edit、POST /verify（route editing spec §3）：請求帶整條路線與它的簽章 ----


class RoutePosIn(BaseModel):
    """路線上的位置：第幾段、從該段起點沿折線量的公尺數（用經緯度會在來回騎的路上選錯是哪一趟）。"""

    segment: int = Field(ge=0)
    offset_m: Finite = Field(ge=0)


class CutOp(BaseModel):
    type: Literal["cut"]
    at: RoutePosIn


class RerouteOp(BaseModel):
    type: Literal["reroute"]
    from_: RoutePosIn = Field(alias="from")
    to: RoutePosIn
    mode: Literal["shortest", "shape"]


class DragOp(BaseModel):
    type: Literal["drag"]
    grab: RoutePosIn
    to: LonLatPair


EditOp = Annotated[CutOp | RerouteOp | DragOp, Field(discriminator="type")]


class SegmentIn(BaseModel):
    """與 SegmentOut 同形：必須是上一次回應的 segments 原封不動（簽章會驗）。"""

    kind: SegmentKind
    stroke_index: int | None = Field(ge=0)
    points: list[LonLatPair] = Field(min_length=2, max_length=20_000)
    length_m: Finite = Field(ge=0)


class RouteRequest(BaseModel):
    segments: list[SegmentIn] = Field(min_length=1, max_length=2000)
    data_version: str = Field(max_length=64)
    route_sig: str = Field(max_length=128)
    strokes: list[StrokeIn] = Field(min_length=1, max_length=40)


class VerifyRequest(RouteRequest):
    """舊路網的草稿重新檢查：data_version 可以是舊的，簽章仍用同一把金鑰驗。"""


class EditRequest(RouteRequest):
    op: EditOp


class BuildInfo(BaseModel):
    build_id: str
    osm_md5: str
    created_at: str


class StatusResponse(BaseModel):
    data: BuildInfo | None
    # 目前的 build 有沒有自動搜尋需要的可騎道路資料（舊 build 沒有，要重建路網）
    search_ready: bool = False
    # P2.5：目前的 build 有沒有描形規劃器需要的路網圖（沒有時 quality="fine" 等於 fast）
    planner_ready: bool = False


class ErrorDetail(BaseModel):
    """領域錯誤的 detail；warnings 只有 /match 的 no_route 會帶，segments 只有 /verify 的 verify_failed 會帶。"""

    code: Literal[
        "outside_taiwan", "too_small", "no_route", "no_data", "timeout", "no_search_data",
        # 手動微調（route editing spec §3.1、§3.6）
        "stale_data", "invalid_route", "not_on_route", "nothing_to_cut", "turn_not_rideable", "no_road", "verify_failed",
    ]
    message: str
    warnings: list[WarningOut] | None = None
    segments: list[int] | None = None  # 在目前的路網上有問題的段落序號（接點有問題時兩側都列）


class ErrorResponse(BaseModel):
    detail: ErrorDetail


class ValidationIssue(BaseModel):
    type: str
    loc: list[str | int]
    msg: str


class ValidationErrorResponse(BaseModel):
    """請求格式錯誤（FastAPI 的 422 陣列形式）；每筆只有 type / loc / msg，最多 20 筆。"""

    detail: list[ValidationIssue]


# ---- POST /search（spec §4.6、§4.7）：回應是 SSE，每個事件的 data 是下面其中一種 ----

NormFloat = Annotated[float, Field(ge=-1, le=1, allow_inf_nan=False)]


class NormStrokeIn(BaseModel):
    """圖形的正規化座標（web 的 normalizeStrokes：x 向右、y 向上，外框中心為原點、長邊 = 1）。"""

    points: list[tuple[NormFloat, NormFloat]] = Field(min_length=2, max_length=2000)
    closed: bool = False


class SearchRequest(BaseModel):
    strokes: list[NormStrokeIn] = Field(min_length=1, max_length=40)
    center: LonLatPair
    radius_m: float = Field(ge=MIN_SEARCH_AREA_RADIUS_M, le=MAX_SEARCH_AREA_RADIUS_M, allow_inf_nan=False)
    min_km: float = Field(ge=MIN_SEARCH_KM, le=MAX_SEARCH_KM, allow_inf_nan=False)
    max_km: float = Field(ge=MIN_SEARCH_KM, le=MAX_SEARCH_KM, allow_inf_nan=False)
    # 順時針、0 = 圖形上方朝北（與 web 的 Transform.rotationDeg 相同）
    rotation_min_deg: float = Field(ge=-180, le=180, allow_inf_nan=False)
    rotation_max_deg: float = Field(ge=-180, le=180, allow_inf_nan=False)

    @model_validator(mode="after")
    def _ranges(self) -> Self:
        if self.min_km > self.max_km:
            raise ValueError("min_km 不能大於 max_km")
        if self.rotation_min_deg > self.rotation_max_deg:
            raise ValueError("rotation_min_deg 不能大於 rotation_max_deg")
        return self


class PlacementOut(BaseModel):
    """圖形的擺放，與 web 的 Transform 相同：size_m 是正規化座標 1 單位的公尺數，rotation_deg 順時針、介於 [0, 360)。"""

    center: LonLatPair
    size_m: float = Field(gt=0)
    rotation_deg: float = Field(ge=0, lt=360)


class SearchCandidate(BaseModel):
    placement: PlacementOut
    match: MatchResponse  # 這個擺放（帶 start_circle）的吸附結果，與 /match 的 200 回應同形
    start_distance_m: float = Field(ge=0)  # 路線起點離圓心的距離；一定 ≤ radius_m（圈外的候選不回傳）
    length_ok: bool  # route.length_m 落在 [min_km, max_km] 內


class SearchQueued(BaseModel):
    type: Literal["queued"] = "queued"
    position: int = Field(ge=1)  # 前面還有幾個搜尋（含正在跑的那個）


class SearchProgress(BaseModel):
    type: Literal["progress"] = "progress"
    stage: Literal["coarse", "refine", "match"]
    done: int = Field(ge=0)
    total: int = Field(ge=0)


class SearchBest(BaseModel):
    """到目前為止最好的候選（即時預覽）。"""

    type: Literal["best"] = "best"
    candidate: SearchCandidate


class SearchResult(BaseModel):
    """最後一個事件（成功）：前 5 名，依符合條件、貼合度排序；沒有符合公里數的就列出最接近的並附 notice。"""

    type: Literal["result"] = "result"
    candidates: list[SearchCandidate] = Field(max_length=5)
    coarse_candidates: int = Field(ge=0)  # 粗篩評估過的擺放數
    evaluated: int = Field(ge=0)  # 跑完整吸附的次數
    elapsed_ms: int = Field(ge=0)  # 從開始搜尋（拿到名額之後）到產生結果
    notice: str | None = None


class SearchFailed(BaseModel):
    """最後一個事件（失敗）。"""

    type: Literal["error"] = "error"
    code: Literal["timeout", "busy", "no_data", "internal"]
    message: str


SearchEvent = Annotated[
    SearchQueued | SearchProgress | SearchBest | SearchResult | SearchFailed, Field(discriminator="type")
]
