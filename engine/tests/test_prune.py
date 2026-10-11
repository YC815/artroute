"""剪小刺與小圈（P2.5 A4）：假 router，不需要 Valhalla。合成地圖＋真的 Valhalla 的場景在 test_prune_grid.py。"""

import pytest

from engine.matching import prune
from engine.matching.compose import flat
from engine.matching.deadline import Deadline, MatchCancelled, MatchTimeout
from engine.matching.prune import (
    MAX_PRUNE_ATTEMPTS,
    MIN_LOOP_M,
    PRUNE_BUDGET_S,
    PRUNE_RESERVE_S,
    _candidates,
    _cut_loop,
    prune_loops,
)
from engine.matching.score import Score, target_extent_m
from engine.matching.types import JOIN_TOLERANCE_M, Segment
from engine.routing.geo import haversine_m, polyline_length_m
from engine.routing.router import RouteResult
from turn_router import TurnBlockRouter, east, every_junction_rideable, north

LAT = 25.05
W = (121.50, LAT)
LINE_M = 1000.0
C = east(W, LINE_M)
TARGET = [[W, C]]  # 目標：一條 1 km 的東西向直線
EXTENT_M = target_extent_m(TARGET)


def seg(points, kind="stroke", index=0) -> Segment:
    return Segment(kind, index, list(points), polyline_length_m(points))


def square(at, side_m):
    """從 at 往北繞一個正方形回到 at（不經過 at 東邊的直線）。"""
    return [at, north(at, side_m), east(north(at, side_m), side_m), east(at, side_m), at]


def all_points(segments):
    return [p for s in segments for p in s.points]


def assert_joins_within_tolerance(segments) -> None:
    for a, b in zip(segments, segments[1:]):
        assert haversine_m(a.points[-1], b.points[0]) <= JOIN_TOLERANCE_M


def assert_slices_of(result, original) -> None:
    """輸出的每一段都是原本某一段的連續切片（點全是原本的點、順序不變），長度是切片的幾何長度。"""
    for s in result:
        source = [o for o in original if o.kind == s.kind and o.stroke_index == s.stroke_index]
        assert any(
            o.points[k : k + len(s.points)] == s.points for o in source for k in range(len(o.points) - len(s.points) + 1)
        )
        assert s.length_m == pytest.approx(polyline_length_m(s.points)) or s in original


def run(segments, *, router=None, targets=TARGET, deadline=None, extent_m=EXTENT_M):
    return prune_loops(router or TurnBlockRouter(), segments, targets, deadline or Deadline(30), extent_m=extent_m)


def test_spur_across_two_segments_is_cut_at_the_shared_point() -> None:
    x = east(W, 500)
    tip = north(x, 60)
    out, back = seg([W, x, tip]), seg([tip, x, C], "detour")  # W→X→死巷底→X→C：兩段在死巷底相接
    result = run([out, back])
    assert [s.points for s in result] == [[W, x], [x, C]]
    assert [s.kind for s in result] == ["stroke", "detour"]
    assert [s.length_m for s in result] == pytest.approx([500, 500], rel=0.01)
    assert_slices_of(result, [out, back])
    assert every_junction_rideable(TurnBlockRouter(), result)


def test_loop_inside_one_segment_splits_it_in_two() -> None:
    x = east(W, 400)
    only = seg([W, *square(x, 30), C], "stroke", 3)
    result = run([only])
    assert [s.points for s in result] == [[W, x], [x, C]]
    assert [(s.kind, s.stroke_index) for s in result] == [("stroke", 3), ("stroke", 3)]
    assert [s.length_m for s in result] == pytest.approx([400, 600], rel=0.01)


def test_loop_spanning_three_segments_drops_the_middle_one() -> None:
    x = east(W, 400)
    a = seg([W, x, north(x, 30)])
    b = seg([north(x, 30), east(north(x, 30), 30)], "connector", None)
    c = seg([east(north(x, 30), 30), east(x, 30), x, C], "stroke", 1)
    result = run([a, b, c])
    assert [s.points for s in result] == [[W, x], [x, C]]
    assert [(s.kind, s.stroke_index) for s in result] == [("stroke", 0), ("stroke", 1)]
    assert_slices_of(result, [a, b, c])


def test_input_is_not_modified() -> None:
    x = east(W, 400)
    segments = [seg([W, *square(x, 30), C])]
    before = list(segments)
    run(segments)
    assert segments == before


def test_candidates_go_from_short_to_long_and_then_by_position() -> None:
    small, big, same = east(W, 600), east(W, 200), east(W, 800)
    points = [W, *square(big, 40), *square(small, 20), *square(same, 20), C]
    loops = _candidates(points, 1000)
    assert [round(loop.length_m) for loop in loops] == [80, 80, 160]
    assert loops[0].start < loops[1].start
    assert all(loop.length_m >= MIN_LOOP_M for loop in loops)


def test_repeated_points_give_one_candidate_per_loop() -> None:
    # route 的結果常在頭尾重複一點：同一個位置的一串重複點只算一次（i 取最後一個、j 取第一個）
    x = east(W, 400)
    loop = square(x, 30)
    points = [W, x, x, *loop[1:-1], x, x, C]
    (only,) = _candidates(points, 1000)
    assert (only.start, only.end) == (2, 6)


def test_loops_shorter_than_the_minimum_or_longer_than_the_cap_are_not_candidates() -> None:
    x = east(W, 400)
    tiny = [W, *square(x, 3.9), C]  # 周長 15.6 m < 16 m
    assert _candidates(tiny, 1000) == []
    big = [W, *square(x, 40), C]  # 周長 160 m
    assert _candidates(big, 159) == [] and len(_candidates(big, 161)) == 1


def test_nested_loops_are_found_again_after_each_cut() -> None:
    # 大圈裡還有一個小圈：先剪小的（短），之後重新找，大圈的索引已經變了，用舊索引會切錯地方
    x = east(W, 300)
    p = north(x, 15)
    inner = square(p, 5)  # 在 p 繞一圈（周長 20 m）
    outer = [x, p, *inner[1:], east(p, 25), east(x, 25), x]  # 在 x 繞一圈（含小圈約 100 m）
    segments = [seg([W, *outer, C])]
    assert len(_candidates(all_points(segments), 1000)) >= 2
    result = run(segments)
    assert [s.points for s in result] == [[W, x], [x, C]]


def test_loop_drawn_in_the_target_is_kept() -> None:
    x = east(W, 400)
    loop = square(x, 30)
    segments = [seg([W, *loop, C])]
    assert run(segments, targets=[[W, C], loop]) == segments  # 目標本身有這個圈：剪了覆蓋下降


def test_cut_that_would_need_a_u_turn_is_not_made_and_costs_no_route() -> None:
    # 直線走到 X、繞一圈回到 X、原路回 W：剪掉圈就是在 X 原地迴轉，嚴格版不放行（不呼叫 route）
    x = east(W, 400)
    segments = [seg([W, *square(x, 30), W])]
    (loop,) = _candidates(all_points(segments), 0.15 * 1000)  # 前提：周長 120 m 的圈是候選（上限 150 m）
    assert round(loop.length_m) == 120
    router = TurnBlockRouter()
    assert run(segments, router=router, targets=[[W, x]], extent_m=1000) == segments
    assert router.route_calls == []


def test_cut_whose_new_turn_valhalla_would_not_ride_is_not_made() -> None:
    # 從西往東到 X、往北繞一圈回到 X、再往北：剪完在 X 左轉，但 X 不能轉
    x = east(W, 500)
    segments = [seg([W, x, east(x, 30), east(north(x, -30), 30), north(x, -30), x, north(x, 400)])]
    targets = [[W, x, north(x, 400)]]
    assert run(segments, router=TurnBlockRouter(blocked=x), targets=targets) == segments
    free = run(segments, targets=targets)  # 對照：X 能轉彎就剪
    assert [s.points for s in free] == [[W, x], [x, north(x, 400)]]


def stacked_case(*, loop_m: float = 15.0) -> tuple[list[Segment], list]:
    """立體交叉：路線在下層往東經過 X，繞一圈（東、南、西、北）回到 X 正上方 0.5 m 的 X'（高架，同向往東）。
    只有頂點算候選：下層與高架在平面上重疊的路段中間沒有頂點。剪掉圈 = 從下層直接接上高架，轉角 0°。"""
    x = east(W, 500)
    deck = north(x, 0.5)
    around = [east(x, loop_m), east(north(x, -20), loop_m), east(north(x, -20), -loop_m), east(deck, -loop_m)]
    return [seg([W, x, *around, deck, C])], [x, deck]


def test_cut_from_the_lower_road_onto_the_deck_right_above_it_is_not_made() -> None:
    # 剪完新接點轉角 0°：以前直行直接放行，輸出從下層跳到高架。現在嚴格版也用 route 量，X 不能直接接上就不剪
    segments, (x, deck) = stacked_case()
    (loop,) = _candidates(all_points(segments), 0.15 * 1000)  # 前提：X→X' 的圈是唯一候選
    assert haversine_m(x, deck) < JOIN_TOLERANCE_M and round(loop.length_m) == 100
    router = TurnBlockRouter(blocked=x)
    assert run(segments, router=router) == segments
    assert router.route_calls  # 是 route 量了才拒絕的（不是便宜判斷）
    # 對照：同樣的幾何、X 能接上就剪（證明不是別的條件擋住）
    free = TurnBlockRouter()
    result = run(segments, router=free)
    assert [s.points for s in result] == [[W, x], [deck, C]]
    assert free.route_calls
    assert_joins_within_tolerance(result)


NODE = east(W, 600)
WEST_LEG = seg([W, NODE])  # 由西往東到 NODE


def neighbour_case(south_m: float) -> list[Segment]:
    """WEST_LEG|往南 south_m 的短段|在 P 繞一圈、再往北經過 NODE。剪掉 P 的圈後，新接點 P 的轉彎本身能騎；
    但 south_m < TURN_PROBE_M 時，既有接點 WEST_LEG|短段 的往前探針會伸進新幾何、折回 NODE，量不出方向。"""
    p = north(NODE, -south_m)
    loop = [p, east(p, 30), east(north(p, -20), 30), north(p, -20), p, north(NODE, 300)]
    return [WEST_LEG, seg([NODE, p], "detour"), seg(loop, "stroke", 1)]


NEIGHBOUR_TARGET = [[W, NODE, north(NODE, 300)]]


def test_existing_junction_whose_probe_reaches_the_new_geometry_is_rechecked() -> None:
    segments = neighbour_case(south_m=4)
    router = TurnBlockRouter()
    assert every_junction_rideable(router, segments)  # 前提：原本的接點都能騎
    assert run(segments, router=router, targets=NEIGHBOUR_TARGET) == segments
    # 對照：短段 12 m，探針碰不到新幾何，照剪
    far = run(neighbour_case(south_m=12), targets=NEIGHBOUR_TARGET)
    assert len(far) == 3 and every_junction_rideable(TurnBlockRouter(), far)


def test_junction_beyond_one_meter_is_never_cut() -> None:
    # 繞一圈回來的點離出發點 1.005 m：KD-tree（平面近似、多抓 1%）會找到，haversine 確認後不剪
    x = east(W, 400)
    back = east(x, 1.005)
    loop = [x, north(x, 30), east(north(x, 30), 30), east(x, 30), back]
    segments = [seg([W, x]), seg([x, *loop[1:]], "detour"), seg([back, C], "stroke", 1)]
    assert haversine_m(x, back) > JOIN_TOLERANCE_M
    assert _candidates(all_points(segments), 1000)  # 候選有它
    result = run(segments)
    assert result == segments
    assert_joins_within_tolerance(result)


def test_loop_back_within_tolerance_is_cut() -> None:
    # 對照：回來的點離出發點 0.9 m，剪完的接點 0.9 m
    x = east(W, 400)
    back = east(x, 0.9)
    loop = [x, north(x, 30), east(north(x, 30), 30), east(x, 30), back]
    segments = [seg([W, x]), seg([x, *loop[1:]], "detour"), seg([back, C], "stroke", 1)]
    result = run(segments)
    assert [s.points for s in result] == [[W, x], [back, C]]
    assert_joins_within_tolerance(result)


def test_zero_length_piece_left_by_the_cut_is_dropped_only_if_the_join_stays_within_tolerance() -> None:
    # 後段開頭重複了第一點（connector 可能長這樣）：從那一點剪，左邊只剩零長度的片段，要丟掉。
    # 丟掉後新邊界變成前一段的終點 ↔ 圈回來的點：前一段終點在出發點西邊 0.6 m
    x = east(W, 400)
    prev = seg([W, east(x, -0.6)])
    body = [x, x, north(x, 30), east(north(x, 30), 30), east(x, 30)]
    for back_m, cut in ((0.3, True), (0.7, False)):  # 回來的點在出發點東邊 0.3 m（離前一段終點 0.9 m）／0.7 m（1.3 m）
        back = east(x, back_m)
        segments = [prev, seg([*body, back, C], "connector", None)]
        positions = flat(segments)
        first = next(p for p in positions if p.point == x)
        last = next(p for p in positions if p.point == back)
        pruned = _cut_loop(segments, first, last)
        if cut:
            assert pruned is not None and pruned.junction == 0
            assert [s.points for s in pruned.segments] == [prev.points, [back, C]]
            assert_joins_within_tolerance(pruned.segments)
        else:
            assert pruned is None


def test_loop_at_the_very_start_is_cut_without_a_new_junction() -> None:
    x = W
    segments = [seg([*square(x, 30), C])]
    result = run(segments)
    assert [s.points for s in result] == [[x, C]]


def test_whole_route_being_the_loop_is_not_cut_to_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    loop = square(W, 30)
    segments = [seg(loop)]
    positions = flat(segments)
    assert _cut_loop(segments, positions[0], positions[-1]) is None
    # 就算評分說剪了比較好，也不會剪成空的路線
    monkeypatch.setattr(prune, "score", lambda targets, route: Score(0, 0, 0, 0, -float(len(route))))
    assert run(segments, targets=[[W, east(W, 200)]], extent_m=1000) == segments


def test_cut_that_removes_a_whole_stroke_is_not_made() -> None:
    # 審查 S2：筆畫 1 是直線旁 40 m 外一個 4 m 的小方形，用 connector 來回接上（約 96 m < 上限 150 m）。
    # 剪掉就整個筆畫都不見了，但 warnings 在剪之前就決定了（不會有 no_route、前端不會標紅）：不剪
    x = east(W, 500)
    d = north(x, 40)
    dot = [d, east(d, 4), east(north(d, 4), 4), north(d, 4), d]
    segments = [seg([W, x]), seg([x, d], "connector", None), seg(dot, "stroke", 1), seg([d, x], "connector", None), seg([x, C])]
    targets = [[W, C], dot]
    assert _candidates(all_points(segments), 0.15 * target_extent_m(targets))  # 前提：是候選
    result = run(segments, targets=targets, extent_m=target_extent_m(targets))
    assert any(s.stroke_index == 1 and s.kind == "stroke" for s in result)


def test_cut_that_leaves_only_a_few_meters_of_a_stroke_is_not_made() -> None:
    # 筆畫 1 繞完 80 m 小圈回到 X，收尾再往東 3 m。舊判斷只看還有兩個不同點，會把筆畫剪到只剩 3 m，
    # 而 v3 只看可見幾何的聯集，剪掉偏離目標直線的小圈後分數反而上升，不能靠評分擋住。
    x, tail = east(W, 500), east(W, 503)
    stroke = seg([*square(x, 20), tail], "stroke", 1)
    segments = [seg([W, x]), stroke, seg([tail, C], "connector", None)]
    targets = [[W, C]]
    positions = flat(segments)
    pruned = _cut_loop(segments, positions[1], next(p for p in positions if p.point == x and p.segment == 1))
    assert pruned is not None
    assert prune.score(targets, prune.route_points(pruned.segments)).exact >= prune.score(
        targets, prune.route_points(segments)
    ).exact

    assert run(segments, targets=targets, extent_m=target_extent_m(targets)) == segments


def test_cut_that_removes_a_stroke_drawn_only_by_a_detour_is_not_made() -> None:
    # 複審 R1：整段 trace 失敗時筆畫可能全是 detour（例如短筆畫逆著單行道），detour 不記警告：剪掉一樣會悄悄消失
    x = east(W, 500)
    d = north(x, 40)
    dot = [d, east(d, 4), east(north(d, 4), 4), north(d, 4), d]
    segments = [seg([W, x]), seg([x, d], "connector", None), seg(dot, "detour", 1), seg([d, x], "connector", None), seg([x, C])]
    targets = [[W, C], dot]
    assert _candidates(all_points(segments), 0.15 * target_extent_m(targets))  # 前提：是候選
    result = run(segments, targets=targets, extent_m=target_extent_m(targets))
    assert any(s.stroke_index == 1 and s.kind == "detour" for s in result)


def test_u_turn_hidden_by_a_vertex_on_only_one_side_is_not_cut() -> None:
    # 審查 B1：N 是 J 西邊 5 m 的路口、M 是 J 西邊 3 m 的段落邊界。剪掉 J 的圈會變成 W→N→J→M→N→北：
    # 在 N 東邊 5 m 的路中間迴轉。兩側的頂點不同（M 只在回程），探針繞過 J、route 量到 N 的合法轉彎
    j = east(W, 500)
    n, m = east(j, -5), east(j, -3)
    loop = [j, east(j, 15), east(north(j, -15), 15), north(j, -15), j]
    segments = [seg([W, n, *loop, m]), seg([m, n, north(n, 400)], "stroke", 1)]
    targets = [[W, n, north(n, 400)]]
    assert every_junction_rideable(TurnBlockRouter(), segments)  # 前提：原本的接點都能騎
    result = run(segments, targets=targets, extent_m=target_extent_m(targets))
    assert east(j, 15) not in all_points(result) or result == segments
    assert not any(q == j for q in all_points(result))  # 不留在 J 迴轉的小刺
    assert [s.points for s in result] == [[W, n], [n, north(n, 400)]]  # 改剪 N 的那一圈（含 J 的來回）


class NoLeftAtK:
    """K 不能從東西向的街左轉上往北的街：route 的起點在 K 西邊的街上（10 m 內）、終點在 K 北邊的街上時繞一大圈；其餘走直線。"""

    def __init__(self, k) -> None:
        self.k = k

    def trace(self, points, *, search_radius_m):
        raise AssertionError("剪圈不呼叫 trace")

    def route(self, start, end, *, shortest=False):
        on_west_street = abs(start[1] - self.k[1]) < 1e-7 and start[0] < self.k[0] and haversine_m(start, self.k) <= 10
        on_north_street = abs(end[0] - self.k[0]) < 1e-7 and end[1] > self.k[1]
        points = [start, north(start, 440), north(end, 440), end] if on_west_street and on_north_street else [start, end]
        return RouteResult(points=points, length_km=polyline_length_m(points) / 1000)


def test_existing_junction_near_a_trimmed_start_is_rechecked() -> None:
    # 審查 S4：路線開頭在 W 繞一圈（從西南方回到 W），往東 2 m 到 K 再往北。原本 K 的往回探針伸進圈裡，看起來像直行；
    # 剪掉開頭的圈後沒有新接點，但 K 的往回探針只剩 W→K：變成從東西向的街左轉，K 不能左轉 → 不剪
    k = east(W, 2)
    u = (1 / 3, -0.943)  # 圈最後一邊從 W 往回的方向（東南南）
    v = (W[0] + 20 * u[0] / 100_900, W[1] + 20 * u[1] / 110_574)
    loop = [W, east(W, -20), north(east(W, -20), -18.86), v, W]
    segments = [seg(loop, "connector", None), seg([W, k]), seg([k, north(k, 1000)])]
    targets = [[W, k, north(k, 1000)]]
    router = NoLeftAtK(k)
    assert every_junction_rideable(router, segments)  # 前提：原本的接點都能騎
    assert _candidates(all_points(segments), 0.15 * target_extent_m(targets))
    assert run(segments, router=router, targets=targets, extent_m=target_extent_m(targets)) == segments
    # 對照：K 能左轉就剪
    free = run(segments, targets=targets, extent_m=target_extent_m(targets))
    assert [s.points for s in free] == [[W, k], [k, north(k, 1000)]]


def test_a_junction_made_by_an_earlier_cut_is_rechecked_strictly() -> None:
    # 審查 nit 2：先剪 X 的圈（80 m）造出新接點 X；路線最後在 X 東邊 0.5 m 的 Z 繞一圈結束（100 m）。
    # 剪 Z 的圈會讓路線停在 Z，X 的往前探針只剩 0.5 m：非嚴格版當成量不出來而放行，
    # 但 X 是剪出來的接點，要用嚴格版重量 → 不剪這一刀，改剪 X 到最後的 Z（路線停在 X，沒有 0.5 m 的尾巴接點）
    x = east(W, 500)
    z = east(x, 0.5)
    segments = [seg([W, *square(x, 20), *square(z, 25)])]
    result = run(segments)
    assert [s.points for s in result] == [[W, x]]


def test_nothing_is_tried_when_the_deadline_is_too_close() -> None:
    x = east(W, 400)
    segments = [seg([W, *square(x, 30), C])]
    router = TurnBlockRouter()
    assert run(segments, router=router, deadline=Deadline(PRUNE_RESERVE_S - 0.05)) == segments


def slow_scores(monkeypatch: pytest.MonkeyPatch, now: list[float], seconds: float, then=None) -> None:
    """每次評分讓假時鐘前進 seconds 秒（then：評分後要做的事，例如取消）。"""
    real_score = prune.score

    def slow(targets, route):
        now[0] += seconds
        if then is not None:
            then()
        return real_score(targets, route)

    monkeypatch.setattr(prune, "score", slow)


TWO_LOOPS = [seg([W, *square(east(W, 200), 20), *square(east(W, 600), 30), C])]  # 周長 80 m 與 120 m


def test_two_loops_are_both_cut_with_enough_time() -> None:
    points = all_points(run(TWO_LOOPS))
    assert north(east(W, 200), 20) not in points and north(east(W, 600), 30) not in points


def test_budget_running_out_mid_attempt_returns_what_was_cut_so_far(monkeypatch: pytest.MonkeyPatch) -> None:
    # 剪圈的子期限是 PRUNE_BUDGET_S（外層還很寬）。每次評分 0.4 s：剪前 0.4、第一刀 0.8 s 剪完；
    # 第二刀評分完已經 1.2 s，超過預算：接住、回傳剪了一刀的結果，不往外丟
    now = [0.0]
    slow_scores(monkeypatch, now, 0.4)
    result = run(TWO_LOOPS, deadline=Deadline(10, clock=lambda: now[0]))
    points = all_points(result)
    assert north(east(W, 200), 20) not in points  # 短的先剪
    assert north(east(W, 600), 30) in points  # 長的沒時間了
    assert 0.8 < PRUNE_BUDGET_S < 1.2


def test_budget_running_out_in_the_first_attempt_returns_the_input(monkeypatch: pytest.MonkeyPatch) -> None:
    now = [0.0]
    slow_scores(monkeypatch, now, 0.6)
    assert run(TWO_LOOPS, deadline=Deadline(10, clock=lambda: now[0])) == TWO_LOOPS


def test_the_budget_never_eats_into_the_reserve(monkeypatch: pytest.MonkeyPatch) -> None:
    # 外層只剩 0.8 s：子期限 = 0.8 − 0.3 = 0.5 s。每次評分 0.3 s，第一刀評分完 0.6 s 就停
    now = [0.0]
    slow_scores(monkeypatch, now, 0.3)
    assert run(TWO_LOOPS, deadline=Deadline(0.8, clock=lambda: now[0])) == TWO_LOOPS


def test_first_attempt_checks_the_budget_between_its_two_scores(monkeypatch: pytest.MonkeyPatch) -> None:
    # 複審 nit 1：外層 0.45 s → 子期限 0.15 s。每次評分 0.25 s：剪前那次評分完子期限已經到了，
    # 要在這裡就停（回傳輸入）；連算第二次會讓外層也到期（0.5 s），整個 /match 從有結果變成 504
    now = [0.0]
    slow_scores(monkeypatch, now, 0.25)
    assert run(TWO_LOOPS, deadline=Deadline(0.45, clock=lambda: now[0])) == TWO_LOOPS


def test_already_expired_deadline_skips_pruning_without_raising() -> None:
    # 串接最後一次 Valhalla 呼叫跨過期限：剪圈不丟例外，到期與否交給呼叫端
    assert run(TWO_LOOPS, deadline=Deadline(-1)) == TWO_LOOPS


def test_outer_deadline_expiring_mid_attempt_is_raised(monkeypatch: pytest.MonkeyPatch) -> None:
    now = [0.0]
    slow_scores(monkeypatch, now, 2.0)
    with pytest.raises(MatchTimeout):
        run(TWO_LOOPS, deadline=Deadline(1.5, clock=lambda: now[0]))


def test_cancel_mid_attempt_is_raised(monkeypatch: pytest.MonkeyPatch) -> None:
    now = [0.0]
    deadline = Deadline(10, clock=lambda: now[0])
    slow_scores(monkeypatch, now, 0.0, then=deadline.cancel)
    with pytest.raises(MatchCancelled):
        run(TWO_LOOPS, deadline=deadline)


def test_cancel_is_not_swallowed() -> None:
    deadline = Deadline(30)
    deadline.cancel()
    with pytest.raises(MatchCancelled):
        run([seg([W, *square(east(W, 400), 30), C])], deadline=deadline)


def many_loops(n: int) -> list[Segment]:
    """直線上每 20 m 一個邊長 5 m 的三角形小圈（周長約 17 m）。"""
    points = [W]
    for k in range(n):
        at = east(W, 20 * (k + 1))
        points += [at, north(at, 5), east(north(at, 5), 5), at]
    return [seg([*points, C])]


def test_at_most_max_attempts_are_scored(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    def worse_when_shorter(targets, route):  # 剪任何一個圈都讓分數下降：每個候選都是失敗的嘗試
        calls.append(len(route))
        return Score(0, 0, 0, 0, float(len(route)))

    monkeypatch.setattr(prune, "score", worse_when_shorter)
    segments = many_loops(MAX_PRUNE_ATTEMPTS + 5)
    assert len(_candidates(all_points(segments), 1000)) > MAX_PRUNE_ATTEMPTS
    assert run(segments) == segments
    assert len(calls) == 1 + MAX_PRUNE_ATTEMPTS  # 剪前算一次，之後每個嘗試一次


def test_successful_cuts_count_as_attempts_too(monkeypatch: pytest.MonkeyPatch) -> None:
    # 剪任何一個圈都讓分數上升：每個嘗試都成功
    monkeypatch.setattr(prune, "score", lambda targets, route: Score(0, 0, 0, 0, -float(len(route))))
    segments = many_loops(MAX_PRUNE_ATTEMPTS + 5)
    result = run(segments)
    assert len(_candidates(all_points(result), 1000)) == 5  # 剪了 40 個，剩 5 個沒試


def test_cheap_rejects_do_not_use_up_the_attempts(monkeypatch: pytest.MonkeyPatch) -> None:
    # 一條很長的來回死巷：裡面每一對來回點都是候選，但剪了都是原地迴轉，不用評分就淘汰，不佔嘗試次數
    x = east(W, 300)
    spur = [north(x, 2 * k) for k in range(1, 31)]  # 60 m 的死巷，每 2 m 一點
    segments = [seg([W, x, *spur, *spur[-2::-1], x, C])]
    calls = []
    real_score = prune.score

    def counting(targets, route):
        calls.append(1)
        return real_score(targets, route)

    monkeypatch.setattr(prune, "score", counting)
    result = run(segments)
    assert [s.points for s in result] == [[W, x], [x, C]]
    assert len(calls) <= 3
