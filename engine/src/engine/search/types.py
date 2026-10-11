"""自動搜尋共用的型別。"""

from dataclasses import dataclass, field
from typing import Literal

from engine.matching.service import MatchResult
from engine.routing.router import LonLat

type NormPoint = tuple[float, float]
type Stage = Literal["coarse", "refine", "match"]


@dataclass(frozen=True)
class NormStroke:
    """正規化座標（x 向右、y 向上）的一筆；closed=True 時最後一點不重複起點。"""

    points: list[NormPoint]
    closed: bool


@dataclass(frozen=True)
class Placement:
    """與 web 的 Transform 相同：size_m 是正規化座標 1 單位的公尺數，rotation_deg 順時針、0 = 上方朝北。"""

    center: LonLat
    size_m: float
    rotation_deg: float


@dataclass(frozen=True)
class SearchParams:
    strokes: list[NormStroke]
    center: LonLat
    radius_m: float
    min_km: float
    max_km: float
    rotation_min_deg: float
    rotation_max_deg: float


@dataclass(frozen=True)
class Candidate:
    placement: Placement
    result: MatchResult
    start_distance_m: float
    length_ok: bool


@dataclass(frozen=True)
class Progress:
    stage: Stage
    done: int
    total: int


@dataclass(frozen=True)
class Best:
    candidate: Candidate


type SearchEventOut = Progress | Best


@dataclass(frozen=True)
class Evaluation:
    """精算跑過的一次吸附（bench 用來量粗篩與精算排名的相關性，spec §6.3）。"""

    origin: int  # 第幾個粗篩候選（0 = 粗篩成本最低）
    rescaled: bool
    overall: int | None  # 吸附失敗為 None
    start_ok: bool
    length_ok: bool


@dataclass(frozen=True)
class SearchOutcome:
    candidates: list[Candidate]
    coarse_candidates: int
    evaluated: int
    notice: str | None
    evaluations: list[Evaluation] = field(default_factory=list)
    ranked: list[tuple[int, Candidate]] = field(default_factory=list)  # 所有起點在圈內的結果（粗篩名次, 候選），排好序
