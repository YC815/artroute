"""單一部件的分治吸附（spec §4.3）。

整段 trace → 失敗就對半切 → 短於 min_piece_m 仍失敗的小段標記為失敗。
連續的失敗小段合併成一次合法 route 繞路（見 _merge）。
相鄰段落接不上時用 route 補；補不起來就斷成新的 chain，絕不畫直線。
"""

import math
from dataclasses import dataclass
from itertools import groupby

from engine.matching.deadline import Deadline
from engine.matching.types import JOIN_TOLERANCE_M, MatchRouter, MatchWarning, Segment, segment_from
from engine.routing.geo import haversine_m, polyline_length_m
from engine.routing.router import LonLat, RoutingError

DENSIFY_STEP_M = 25.0


@dataclass(frozen=True)
class StrokeMatch:
    chains: list[list[Segment]]
    warnings: list[MatchWarning]


@dataclass(frozen=True)
class _Failed:
    """trace 切到最小仍失敗的小段，還沒決定怎麼繞。"""

    start: LonLat
    end: LonLat


@dataclass(frozen=True)
class _Context:
    router: MatchRouter
    stroke_index: int
    search_radius_m: float
    min_piece_m: float
    deadline: Deadline


def densify(points: list[LonLat], step_m: float = DENSIFY_STEP_M) -> list[LonLat]:
    out = [points[0]]
    for a, b in zip(points, points[1:]):
        n = max(1, math.ceil(haversine_m(a, b) / step_m))
        out.extend((a[0] + (b[0] - a[0]) * i / n, a[1] + (b[1] - a[1]) * i / n) for i in range(1, n + 1))
    return out


def _route(ctx: _Context, start: LonLat, end: LonLat) -> Segment | None:
    ctx.deadline.check()
    try:
        return segment_from("detour", ctx.stroke_index, ctx.router.route(start, end))
    except RoutingError:
        return None


def _solve(ctx: _Context, piece: list[LonLat]) -> list[Segment | _Failed]:
    """回傳依序排列的結果：trace 成功的 Segment，或切到最小仍失敗的 _Failed。這裡不呼叫 route。"""
    ctx.deadline.check()
    try:
        return [segment_from("stroke", ctx.stroke_index, ctx.router.trace(piece, search_radius_m=ctx.search_radius_m))]
    except RoutingError:
        pass
    if len(piece) < 3 or polyline_length_m(piece) <= ctx.min_piece_m:
        return [_Failed(piece[0], piece[-1])]
    mid = len(piece) // 2
    return _solve(ctx, piece[: mid + 1]) + _solve(ctx, piece[mid:])


def _detour_run(ctx: _Context, run: list[_Failed]) -> list[Segment | None]:
    """連續失敗的小段：整串只 route 一次；整串找不到路才退回逐段 route，保住繞得過去的部分。"""
    whole = _route(ctx, run[0].start, run[-1].end)
    if whole is not None:
        return [whole]
    if len(run) == 1:
        return [None]  # 整串就是這一段，剛剛已經試過了
    return [_route(ctx, failed.start, failed.end) for failed in run]


def _merge(ctx: _Context, leaves: list[Segment | _Failed]) -> list[Segment | None]:
    """把每一串連續的 _Failed 合成一次繞路。

    逆向單行道上每個小段各自繞路，每段都得繞完整個街區（約 800 m）；
    整串合在一起只繞一圈。所以失敗的小段一定要先合併再 route。
    """
    pieces: list[Segment | None] = []
    for is_failed, group in groupby(leaves, key=lambda leaf: isinstance(leaf, _Failed)):
        pieces.extend(_detour_run(ctx, list(group)) if is_failed else group)
    return pieces


def _link(ctx: _Context, pieces: list[Segment | None]) -> list[list[Segment]]:
    chains: list[list[Segment]] = []
    chain: list[Segment] = []
    for segment in pieces:
        if segment is None:
            continue
        if chain and haversine_m(chain[-1].points[-1], segment.points[0]) > JOIN_TOLERANCE_M:
            bridge = _route(ctx, chain[-1].points[-1], segment.points[0])
            if bridge is None:
                chains.append(chain)
                chain = []
            else:
                chain.append(bridge)
        chain.append(segment)
    if chain:
        chains.append(chain)
    return chains


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
