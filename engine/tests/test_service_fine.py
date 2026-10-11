"""match_shape 的 fine 流程（P2.5 計畫 B3）：假 router＋固定的規劃結果（plan_shape 換成假的），不需要 Valhalla。

驗證串接規則本身：照著走的段之間、照著走與退回 fast 的段之間一律經過 join.connect（接點 ≤ 1 m），絕不直接串；
退回 fast 的目標從相鄰照著走那段的實際端點接著畫；預算用完的部分交給 fast；反向用規劃器的 backward。
"""

import pytest

from engine.matching import service
from engine.matching.deadline import Deadline, MatchCancelled
from engine.matching.service import StrokeInput, _Fallback, _fallback_points, match_shape
from engine.matching.types import JOIN_TOLERANCE_M, Segment
from engine.matching.stroke import densify
from engine.planner.follow import FOLLOW_RADIUS_M, FOLLOW_STEP_M, start_loop_mid_side
from engine.planner.plan import PlannedPiece, StrokePlan
from engine.routing.geo import haversine_m, polyline_length_m
from engine.routing.router import RouteResult, RoutingError

LAT = 25.05
RADIUS = 50.0


def row(lon_from: float, lon_to: float, n: int = 8) -> list[tuple[float, float]]:
    return [(lon_from + (lon_to - lon_from) * i / n, LAT) for i in range(n + 1)]


class Router:
    """trace：輸入點原樣回傳（fail 裡的經度區間失敗）；route：直接回兩端點。記下每次 trace 的點與半徑。"""

    def __init__(self, fail: tuple[float, float] | None = None) -> None:
        self.fail = fail
        self.traces: list[tuple[list[tuple[float, float]], float]] = []

    def trace(self, points, *, search_radius_m):
        self.traces.append((list(points), search_radius_m))
        if self.fail and any(self.fail[0] <= lon <= self.fail[1] for lon, _ in points):
            raise RoutingError("fail")
        return RouteResult(points=list(points), length_km=polyline_length_m(points) / 1000)

    def route(self, start, end, *, shortest=False):
        return RouteResult(points=[start, end], length_km=haversine_m(start, end) / 1000)

    def follows(self) -> list[list[tuple[float, float]]]:
        return [p for p, r in self.traces if r == FOLLOW_RADIUS_M]


class Graph:
    def region(self, *bbox):
        return None  # 假的 plan_shape 不看 region


def use_plans(monkeypatch: pytest.MonkeyPatch, plans: list[StrokePlan]) -> list[dict]:
    calls: list[dict] = []

    def fake_plan(region, strokes, closed, *, deadline, budget_s):
        calls.append({"strokes": strokes, "closed": closed, "budget_s": budget_s})
        return plans

    monkeypatch.setattr(service, "plan_shape", fake_plan)
    return calls


def piece(points, target_range) -> PlannedPiece:
    return PlannedPiece(points, [(0, True)], target_range, 0.0)


def followed(points):
    """假 router 照著走的結果：follow 送出的加密點列原樣回傳。"""
    return densify(points, FOLLOW_STEP_M)


def fine(router, strokes, **kwargs):
    return match_shape(router, strokes, search_radius_m=RADIUS, deadline=Deadline(30), quality="fine", graph=Graph(),
                       **kwargs)


def assert_joined(segments: list[Segment]) -> None:
    for a, b in zip(segments, segments[1:]):
        assert haversine_m(a.points[-1], b.points[0]) <= JOIN_TOLERANCE_M


TARGET = row(121.50, 121.53, n=12)


def test_followed_pieces_that_do_not_touch_are_joined_by_connect_never_directly(monkeypatch) -> None:
    """兩段照著走的結果之間差 200 m：用 connect 補一段合法 route（detour），不直接串成一條直線。
    把 _link_blocks 的 connect 換成直接串接時，這裡會變成 DiscontinuousRoute。"""
    first, second = row(121.50, 121.51, n=4), row(121.512, 121.53, n=8)
    use_plans(monkeypatch, [StrokePlan([piece(first, (0, 4)), piece(second, (4, 12))], [], [], [])])
    router = Router()
    result = fine(router, [StrokeInput(TARGET, False)])
    assert [s.kind for s in result.segments] == ["stroke", "detour", "stroke"]
    assert result.segments[0].points == followed(first) and result.segments[2].points == followed(second)
    assert_joined(result.segments)
    assert result.warnings == []


def test_fallback_continues_from_where_the_followed_pieces_end(monkeypatch) -> None:
    """沒規劃的範圍交給 fast：頭尾換成前後照著走那段的實際端點，中間是範圍內的輸入點。"""
    first, second = row(121.50, 121.5085, n=4), row(121.5215, 121.53, n=4)
    use_plans(monkeypatch, [StrokePlan([piece(first, (0, 4)), piece(second, (8, 12))], [], [(3, 9)], [])])
    router = Router()
    result = fine(router, [StrokeInput(TARGET, False)])
    fallback = [p for p, r in router.traces if r == RADIUS]
    assert fallback and fallback[0][0] == first[-1] and fallback[0][-1] == second[0]
    middle = fallback[0][1:-1]
    assert all(p in middle for p in TARGET[4:9])  # 輸入點 4–8（densify 會在中間加點）
    assert_joined(result.segments)
    assert result.warnings == []


def test_follow_failure_falls_back_to_fast_for_that_range(monkeypatch) -> None:
    """照著走失敗（第二段落在 trace 失敗的區間）：那段與相鄰的沒規劃範圍合成一個範圍交給 fast。"""
    first, second = row(121.50, 121.5085, n=4), row(121.5215, 121.53, n=4)
    use_plans(monkeypatch, [StrokePlan([piece(first, (0, 4)), piece(second, (8, 12))], [], [(3, 9)], [])])

    class FailsSecond(Router):
        def trace(self, points, *, search_radius_m):
            if search_radius_m == FOLLOW_RADIUS_M and points[0][0] > 121.52:
                self.traces.append((list(points), search_radius_m))
                raise RoutingError("走不了")
            return super().trace(points, search_radius_m=search_radius_m)

    router = FailsSecond()
    result = fine(router, [StrokeInput(TARGET, False)])
    fallback = [p for p, r in router.traces if r == RADIUS]
    assert len(fallback) == 1 and fallback[0][0] == first[-1] and fallback[0][-1] == TARGET[-1]
    assert_joined(result.segments)


def test_out_of_budget_pieces_are_not_followed_and_the_result_is_fast(monkeypatch) -> None:
    monkeypatch.setattr(service, "PLANNER_BUDGET_S", 0.0)
    calls = use_plans(monkeypatch, [StrokePlan([piece(TARGET, (0, 12))], [piece(TARGET[::-1], (12, 0))], [], [])])
    router = Router()
    result = fine(router, [StrokeInput(TARGET, False)])
    assert calls[0]["budget_s"] == 0.0
    assert router.follows() == []
    fast = match_shape(Router(), [StrokeInput(TARGET, False)], search_radius_m=RADIUS, deadline=Deadline(30))
    assert result == fast


def test_budget_leaves_time_for_fast_when_the_deadline_is_short(monkeypatch) -> None:
    calls = use_plans(monkeypatch, [StrokePlan([], [], [(0, 12)], [(12, 0)])])
    match_shape(Router(), [StrokeInput(TARGET, False)], search_radius_m=RADIUS, deadline=Deadline(3.0), quality="fine",
                graph=Graph())
    assert calls[0]["budget_s"] == pytest.approx(3.0 - service.FINE_RESERVE_S, abs=0.05)
    calls.clear()
    fine(Router(), [StrokeInput(TARGET, False)])
    assert calls[0]["budget_s"] == pytest.approx(service.PLANNER_BUDGET_S, abs=0.05)


def test_reverse_uses_the_backward_plan_not_a_whole_trace(monkeypatch) -> None:
    """第二筆從遠端畫回來，串接器選反向：用規劃器 backward 的照著走結果，不是把整筆倒過來 trace 一次。"""
    one, two = row(121.50, 121.51), row(121.53, 121.52)
    back = row(121.52, 121.53)
    use_plans(monkeypatch, [StrokePlan([piece(one, (0, 8))], [piece(one[::-1], (8, 0))], [], []),
                            StrokePlan([piece(two, (0, 8))], [piece(back, (8, 0))], [], [])])
    router = Router()
    result = fine(router, [StrokeInput(one, False), StrokeInput(two, False)])
    second = [s for s in result.segments if s.stroke_index == 1]
    assert [s.points for s in second] == [followed(back)]
    assert not [p for p, r in router.traces if r == RADIUS and p[0][0] == pytest.approx(121.52)]  # 沒有 trace_whole
    assert_joined(result.segments)
    assert result.warnings == []


def test_incomplete_backward_keeps_the_drawn_direction(monkeypatch) -> None:
    """backward 有一段沒規劃、那段 trace_whole 也走不通：反向不完整，照畫的方向走。"""
    one, two = row(121.50, 121.51), row(121.53, 121.52)
    use_plans(monkeypatch, [StrokePlan([piece(one, (0, 8))], [piece(one[::-1], (8, 0))], [], []),
                            StrokePlan([piece(two, (0, 8))], [piece(row(121.52, 121.525, n=4), (8, 4))], [],
                                       [(5, 0)])])

    class RefusesEastFrom121525(Router):
        def trace(self, points, *, search_radius_m):
            if search_radius_m == RADIUS and points[0][0] >= 121.5249 and points[-1][0] > points[0][0]:
                self.traces.append((list(points), search_radius_m))
                raise RoutingError("逆向")
            return super().trace(points, search_radius_m=search_radius_m)

    result = fine(RefusesEastFrom121525(), [StrokeInput(one, False), StrokeInput(two, False)])
    second = [s for s in result.segments if s.stroke_index == 1]
    assert second[0].points[0][0] == pytest.approx(121.53) and second[-1].points[-1][0] == pytest.approx(121.52)


def test_fast_quality_or_missing_graph_never_plans(monkeypatch) -> None:
    def boom(*args, **kwargs):
        raise AssertionError("不該規劃")

    monkeypatch.setattr(service, "plan_shape", boom)
    strokes = [StrokeInput(TARGET, False)]
    baseline = match_shape(Router(), strokes, search_radius_m=RADIUS, deadline=Deadline(30))
    for kwargs in ({"quality": "fast", "graph": Graph()}, {"quality": None, "graph": Graph()},
                   {"quality": "fine", "graph": None}):
        assert match_shape(Router(), strokes, search_radius_m=RADIUS, deadline=Deadline(30), **kwargs) == baseline


def test_cancellation_during_fine_propagates(monkeypatch) -> None:
    use_plans(monkeypatch, [StrokePlan([piece(TARGET, (0, 12))], [], [], [])])
    deadline = Deadline(30)
    deadline.cancel()
    with pytest.raises(MatchCancelled):
        match_shape(Router(), [StrokeInput(TARGET, False)], search_radius_m=RADIUS, deadline=deadline, quality="fine",
                    graph=Graph())


def test_closed_stroke_fallback_wraps_around_to_its_neighbours() -> None:
    """封閉筆畫首尾相鄰：第一個 item 的前一段是最後一個 item。"""
    points = [(121.50, LAT), (121.51, LAT), (121.51, LAT + 0.01), (121.50, LAT + 0.01)]
    followed = [Segment("stroke", 0, [(121.5101, LAT + 0.005), (121.5101, LAT + 0.009)], 1.0)]
    items = [_Fallback(3, 5), followed]
    out = _fallback_points(points, items, 0, closed=True)
    assert out[0] == followed[0].points[-1] and out[-1] == followed[0].points[0]
    assert out[1:-1] == [points[0]]  # 輸入點 4 mod 4 = 0
    assert _fallback_points(points, [_Fallback(0, 4)], 0, closed=True) == [*points, points[0]]


def test_loop_is_followed_from_the_middle_of_its_longest_side() -> None:
    loop = [(121.50, LAT), (121.51, LAT), (121.51, LAT + 0.002), (121.50, LAT + 0.002), (121.50, LAT)]
    rotated = start_loop_mid_side(piece(loop, (0, 4)))
    middle = (121.505, LAT)
    assert rotated.points[0] == pytest.approx(middle) and rotated.points[-1] == rotated.points[0]
    assert rotated.points[1:-1] == [loop[1], loop[2], loop[3], loop[0]]
    assert start_loop_mid_side(piece(loop[:-1], (0, 3))).points == loop[:-1]  # 不是圈就不動
