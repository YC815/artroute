"""單一部件的分治吸附（spec §4.3）。

整段 trace → 失敗就對半切 → 短於 min_piece_m 仍失敗的小段標記為失敗。
連續的失敗小段合併成一次合法 route 繞路（見 _merge、_detour_run）。
相鄰段落接不上時用 route 補；補不起來、或繞路被拒絕，就斷成新的 chain，絕不畫直線。

route 端點的容許誤差（legal_route）：要求的位置是原始輸入點（chain 的頭尾、繞路的切點）時可以吸附到
MAX_SNAP_GAP_RADII 倍搜尋半徑外；要求的位置已經在路上（相鄰 trace 的端點、橋接線兩端）時只容許 JOIN_TOLERANCE_M。
"""

import math
from dataclasses import dataclass
from itertools import groupby

from engine.matching.deadline import Deadline
from engine.matching.types import JOIN_TOLERANCE_M, MatchRouter, MatchWarning, Segment, legal_route, segment_from
from engine.routing.geo import haversine_m, polyline_length_m
from engine.routing.router import MAX_SNAP_GAP_RADII, MIN_LENGTH_RATIO, LonLat, RoutingError

DENSIFY_STEP_M = 25.0


@dataclass(frozen=True)
class StrokeMatch:
    chains: list[list[Segment]]
    warnings: list[MatchWarning]


@dataclass(frozen=True)
class _Failed:
    """trace 切到最小仍失敗的小段，還沒決定怎麼繞。drawn_m 是這一小段畫出來的長度。"""

    start: LonLat
    end: LonLat
    drawn_m: float


@dataclass(frozen=True)
class _Context:
    router: MatchRouter
    stroke_index: int
    search_radius_m: float
    min_piece_m: float
    deadline: Deadline

    @property
    def snap_m(self) -> float:
        """原始輸入點（不在路上）可以被 route 吸附多遠。"""
        return MAX_SNAP_GAP_RADII * self.search_radius_m


def densify(points: list[LonLat], step_m: float = DENSIFY_STEP_M) -> list[LonLat]:
    out = [points[0]]
    for a, b in zip(points, points[1:]):
        n = max(1, math.ceil(haversine_m(a, b) / step_m))
        out.extend((a[0] + (b[0] - a[0]) * i / n, a[1] + (b[1] - a[1]) * i / n) for i in range(1, n + 1))
    return out


def _route(ctx: _Context, start: LonLat, end: LonLat, start_tolerance_m: float, end_tolerance_m: float) -> Segment | None:
    # 附近沒有可騎的路時，Valhalla 預設會抓 35 km 內的路：端點檢查擋掉的就是這種遠方繞路，結果是 no_route
    result = legal_route(
        ctx.router, ctx.deadline, start, end, start_tolerance_m=start_tolerance_m, end_tolerance_m=end_tolerance_m
    )
    return None if result is None else segment_from("detour", ctx.stroke_index, result)


def _solve(ctx: _Context, piece: list[LonLat]) -> list[Segment | _Failed]:
    """回傳依序排列的結果：trace 成功的 Segment，或切到最小仍失敗的 _Failed。這裡不呼叫 route。"""
    ctx.deadline.check()
    try:
        return [segment_from("stroke", ctx.stroke_index, ctx.router.trace(piece, search_radius_m=ctx.search_radius_m))]
    except RoutingError:
        pass
    if len(piece) < 3 or polyline_length_m(piece) <= ctx.min_piece_m:
        return [_Failed(piece[0], piece[-1], polyline_length_m(piece))]
    mid = len(piece) // 2
    return _solve(ctx, piece[: mid + 1]) + _solve(ctx, piece[mid:])


def _detour_run(
    ctx: _Context, run: list[_Failed], start: LonLat, end: LonLat, start_tolerance_m: float, end_tolerance_m: float
) -> list[Segment | None]:
    """連續失敗的小段：整串只 route 一次，一次就繞完，不是每小段各繞一圈。

    繞路比畫出來的線短太多代表塌縮或切角（合法繞路只會更長），不能收；
    整串繞不出來或被拒絕就對半切，各自重試，直到單一小段為止，保住繞得過去的部分。
    切點是原始輸入點，兩半在切點那一端都用 ctx.snap_m。
    """
    whole = _route(ctx, start, end, start_tolerance_m, end_tolerance_m)
    if whole is not None and whole.length_m >= MIN_LENGTH_RATIO * sum(failed.drawn_m for failed in run):
        return [whole]
    if len(run) == 1:
        return [None]
    mid = len(run) // 2
    cut = run[mid].start  # 兩半共用的切點，兩邊從同一個輸入點出發，吸附結果會一致
    return _detour_run(ctx, run[:mid], start, cut, start_tolerance_m, ctx.snap_m) + _detour_run(
        ctx, run[mid:], cut, end, ctx.snap_m, end_tolerance_m
    )


def _merge(ctx: _Context, leaves: list[Segment | _Failed]) -> list[Segment | None]:
    """把每一串連續的 _Failed 合成一次繞路。

    逆向單行道上每個小段各自繞路，每段都得繞完整個街區（約 800 m）；
    整串合在一起只繞一圈。所以失敗的小段一定要先合併再 route。

    繞路的起訖接前一段 trace 的實際終點、後一段 trace 的實際起點，而不是原始輸入點：
    trace 的端點會被吸附到道路上（最多差 2 倍搜尋半徑），route 又會各自吸附輸入點，
    兩邊對不上就得補橋，而在單行道上往回補橋又是一整圈。
    """
    groups = [(is_failed, list(group)) for is_failed, group in groupby(leaves, key=lambda x: isinstance(x, _Failed))]
    pieces: list[Segment | None] = []
    for i, (is_failed, group) in enumerate(groups):
        if not is_failed:
            pieces.extend(group)
            continue
        before = groups[i - 1][1][-1] if i > 0 else None  # 相鄰的一定是 trace 成功的 Segment
        after = groups[i + 1][1][0] if i + 1 < len(groups) else None
        # 接 trace 端點的那一端已經在路上，必須從那裡出發；只有 chain 頭尾（原始輸入點）可以吸附
        start, start_tolerance_m = (before.points[-1], JOIN_TOLERANCE_M) if before else (group[0].start, ctx.snap_m)
        end, end_tolerance_m = (after.points[0], JOIN_TOLERANCE_M) if after else (group[-1].end, ctx.snap_m)
        pieces.extend(_detour_run(ctx, group, start, end, start_tolerance_m, end_tolerance_m))
    return pieces


def _join(ctx: _Context, chain: list[Segment], segment: Segment) -> bool:
    """segment 能不能直接接在 chain 後面；差超過 JOIN_TOLERANCE_M 就先補一條橋接線（加進 chain），補不起來回傳 False。"""
    if not chain or haversine_m(chain[-1].points[-1], segment.points[0]) <= JOIN_TOLERANCE_M:
        return True
    bridge = _route(ctx, chain[-1].points[-1], segment.points[0], JOIN_TOLERANCE_M, JOIN_TOLERANCE_M)  # 兩端都在路上
    if bridge is None:
        return False
    chain.append(bridge)
    return True


def _link(ctx: _Context, pieces: list[Segment | None]) -> list[list[Segment]]:
    """依序串成 chain；接不上就斷開，斷開的部分交給串接器。

    None（繞不出來或被拒絕的繞路）一律斷開，不能改用橋接線補同一個缺口：
    否則被 MIN_LENGTH_RATIO 拒絕的塌縮、切角繞路會換個名字被收下（ledger L34）。
    """
    chains: list[list[Segment]] = [[]]
    for segment in pieces:
        if segment is None or not _join(ctx, chains[-1], segment):
            chains.append([])
        if segment is not None:
            chains[-1].append(segment)
    return [chain for chain in chains if chain]


def match_stroke(
    router: MatchRouter,
    points: list[LonLat],
    *,
    stroke_index: int,
    search_radius_m: float,
    min_piece_m: float,
    deadline: Deadline,
) -> StrokeMatch:
    ctx = _Context(router, stroke_index, search_radius_m, min_piece_m, deadline)
    pieces = _merge(ctx, _solve(ctx, densify(points)))
    chains = _link(ctx, pieces)
    if not chains:
        warning = MatchWarning(stroke_index, "no_route", "這個部件附近找不到可騎的道路")
        return StrokeMatch(chains=[], warnings=[warning])
    if None in pieces or len(chains) > 1:
        warning = MatchWarning(stroke_index, "partial", "部分路段找不到可騎的道路，已略過")
        return StrokeMatch(chains=chains, warnings=[warning])
    return StrokeMatch(chains=chains, warnings=[])
