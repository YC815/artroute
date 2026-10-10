"""單一部件的分治吸附（spec §4.3）。

整段 trace → 失敗就對半切 → 短於 min_piece_m 仍失敗的小段改用合法 route 繞路。
相鄰段落接不上時用 route 補；補不起來就斷成新的 chain，絕不畫直線。
"""

import math
from dataclasses import dataclass

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


def _solve(ctx: _Context, piece: list[LonLat]) -> list[Segment | None]:
    """回傳依序排列的段落；None 代表這一小段找不到任何合法路徑。"""
    ctx.deadline.check()
    try:
        return [segment_from("stroke", ctx.stroke_index, ctx.router.trace(piece, search_radius_m=ctx.search_radius_m))]
    except RoutingError:
        pass
    if len(piece) < 3 or polyline_length_m(piece) <= ctx.min_piece_m:
        return [_route(ctx, piece[0], piece[-1])]
    mid = len(piece) // 2
    return _solve(ctx, piece[: mid + 1]) + _solve(ctx, piece[mid:])


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
    pieces = _solve(ctx, densify(points))
    chains = _link(ctx, pieces)
    if not chains:
        warning = MatchWarning(stroke_index, "no_route", "這個部件附近找不到可騎的道路")
        return StrokeMatch(chains=[], warnings=[warning])
    if None in pieces or len(chains) > 1:
        warning = MatchWarning(stroke_index, "partial", "部分路段找不到可騎的道路，已略過")
        return StrokeMatch(chains=chains, warnings=[warning])
    return StrokeMatch(chains=chains, warnings=[])
