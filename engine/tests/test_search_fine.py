"""精算：起點條件、公里數修正、停止條件、平行 Router、取消與逾時。路網用「吸附 = 原樣」的假 router。"""

import threading
import time

import pytest

from engine.matching.deadline import Deadline, MatchCancelled, MatchTimeout
from engine.matching.service import StrokeInput, match_shape
from engine.matching.types import StartHint
from engine.routing.geo import haversine_m, polyline_length_m
from engine.routing.router import RouteResult
from engine.search import fine
from engine.search.fine import FineStage, rank_key
from engine.search.geometry import from_local
from engine.search.types import Candidate, NormStroke, Placement, SearchParams

CENTER = (121.5, 25.05)
SQUARE = [NormStroke([(-0.5, -0.5), (0.5, -0.5), (0.5, 0.5), (-0.5, 0.5)], closed=True)]


class EchoRouter:
    """trace 原樣回傳（完美吸附）、route 直線；同一個 router 被兩個 thread 同時用就記下來。"""

    def __init__(self, delay_s: float = 0.0) -> None:
        self.delay_s = delay_s
        self.busy = False
        self.overlapped = False
        self.calls = 0
        self.threads: set[int] = set()

    def _enter(self) -> None:
        if self.busy:
            self.overlapped = True
        self.busy = True
        self.calls += 1
        self.threads.add(threading.get_ident())
        time.sleep(self.delay_s)

    def trace(self, points, *, search_radius_m):
        self._enter()
        self.busy = False
        return RouteResult(points=list(points), length_km=polyline_length_m(points) / 1000)

    def route(self, start, end, *, shortest=False):
        self._enter()
        self.busy = False
        return RouteResult(points=[start, end], length_km=haversine_m(start, end) / 1000)


def params(**overrides) -> SearchParams:
    base = dict(
        strokes=SQUARE, center=CENTER, radius_m=2000.0, min_km=4.5, max_km=5.5,
        rotation_min_deg=-15.0, rotation_max_deg=15.0,
    )
    return SearchParams(**{**base, **overrides})


def placement(east: float = 0.0, north: float = 0.0, size: float = 1200.0) -> Placement:
    return Placement(from_local(CENTER, east, north), size, 0.0)


def stage(p: SearchParams, routers=None, deadline: Deadline | None = None, **kwargs) -> FineStage:
    return FineStage(
        p, routers or [EchoRouter()], deadline or Deadline(60), on_progress=lambda d, t: None,
        on_best=lambda c: None, **kwargs,
    )


def test_length_out_of_range_is_rescaled_once() -> None:
    # 1000 m 的正方形完美吸附 = 4.0 km < 4.5：依比例修正到範圍內側 10%（4.6 km）再算一次
    result = stage(params()).run([placement(size=1000)])
    assert len(result) == 1
    assert result[0].length_ok
    assert result[0].placement.size_m == pytest.approx(1150, rel=1e-3)
    assert result[0].result.route_length_m == pytest.approx(4600, rel=0.01)


def test_rescale_happens_only_once() -> None:
    # 要 200–210 km，大小被夾在上限 20 km（周長 80 km）：修正一次後仍不合格，不會無限重算，留下比較接近的那個。
    # 擺在東邊 10 km：1 km 的版本整個在圈外（被淘汰），20 km 的版本左邊剛好經過圓心
    s = stage(params(min_km=200, max_km=210))
    result = s.run([placement(east=10_000, size=1000)])
    assert s.done == 2
    assert len(result) == 1 and not result[0].length_ok
    assert result[0].placement.size_m == pytest.approx(20_000)


def test_start_outside_circle_is_dropped() -> None:
    # 圖形整個在圓外 5 km：吸附成功，但起點不在圈內
    result = stage(params(radius_m=500)).run([placement(east=5000, size=1200)])
    assert result == []


def test_start_hint_puts_route_start_inside_circle() -> None:
    # 兩筆：第 0 筆在圓外、第 1 筆在圓心。沒有提示時路線從第 0 筆開始（圈外），有提示時從第 1 筆開始
    far = [from_local(CENTER, 3000 + 100 * i, 0) for i in range(5)]
    near = [from_local(CENTER, 100 * i, 0) for i in range(5)]
    strokes = [StrokeInput(far, False), StrokeInput(near, False)]
    plain = match_shape(EchoRouter(), strokes, search_radius_m=50, deadline=Deadline(30))
    hinted = match_shape(
        EchoRouter(), strokes, search_radius_m=50, deadline=Deadline(30), start=StartHint(CENTER, 1000)
    )
    assert haversine_m(plain.route_points[0], CENTER) > 2500
    assert haversine_m(hinted.route_points[0], CENTER) < 1
    assert hinted.score == plain.score  # 只換起點，畫的東西相同


def test_stops_after_min_evaluated_with_enough_qualified() -> None:
    placements = [placement(east=100 * k, size=1200) for k in range(20)]  # 4.8 km：全部合格
    s = stage(params())
    result = s.run(placements)
    assert s.done == fine.MIN_EVALUATED
    assert len(result) == fine.TOP_N
    assert all(c.length_ok for c in result)


def test_soft_budget_stops_with_enough_results() -> None:
    now = [0.0]
    placements = [placement(east=100 * k, size=1200) for k in range(20)]
    s = stage(params(), clock=lambda: now[0])
    original = s._record

    def record(job, result):
        original(job, result)
        now[0] += 6.0  # 每個候選 6 秒：第 5 個之後就超過 25 秒

    s._record = record
    result = s.run(placements)
    assert s.done == 5
    assert len(result) == 5


def test_parallel_routers_are_never_shared() -> None:
    routers = [EchoRouter(delay_s=0.001) for _ in range(3)]
    placements = [placement(east=100 * k, size=1200) for k in range(12)]
    stage(params(), routers=routers).run(placements)
    assert not any(r.overlapped for r in routers)
    assert all(len(r.threads) == 1 for r in routers if r.calls)
    assert sum(1 for r in routers if r.calls) >= 2


def test_cancel_stops_everything() -> None:
    deadline = Deadline(60)
    deadline.cancel()
    with pytest.raises(MatchCancelled):
        stage(params(), deadline=deadline).run([placement()])


def test_search_timeout_keeps_results_found_so_far() -> None:
    deadline = Deadline(60)
    s = stage(params(), deadline=deadline)
    original = s._match
    calls = [0]

    def match(router, p):
        calls[0] += 1
        if calls[0] > 2:
            deadline._end = 0.0  # 整個搜尋到期
        return original(router, p)

    s._match = match
    result = s.run([placement(east=100 * k, size=1200) for k in range(8)])
    assert 1 <= len(result) <= 2


def test_search_timeout_without_results_raises() -> None:
    deadline = Deadline(60)
    deadline._end = 0.0
    with pytest.raises(MatchTimeout):
        stage(params(), deadline=deadline).run([placement()])


def test_rank_prefers_length_ok_then_score_then_start() -> None:
    p = params()
    base = stage(p).run([placement(size=1200)])[0]

    def variant(ok: bool, overall: int, start: float, length: float = 4800) -> Candidate:
        score = type(base.result.score)(overall, overall, overall)
        result = type(base.result)(
            base.result.route_points, length, base.result.segments, score, base.result.target_length_m, []
        )
        return Candidate(base.placement, result, start, ok)

    good, better, closer = variant(True, 60, 900), variant(True, 70, 900), variant(True, 70, 100)
    off_near, off_far = variant(False, 90, 0, length=5800), variant(False, 95, 0, length=9000)
    ranked = sorted([off_far, good, off_near, better, closer], key=lambda c: rank_key(c, p))
    assert ranked == [closer, better, good, off_near, off_far]
