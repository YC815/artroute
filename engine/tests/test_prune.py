"""剪小刺與小圈（P2.5 A4）：假 router，不需要 Valhalla。合成地圖＋真的 Valhalla 的場景在 test_prune_grid.py。"""

import pytest

from engine.matching import prune
from engine.matching.compose import flat
from engine.matching.deadline import Deadline, MatchCancelled
from engine.matching.prune import (
    MAX_PRUNE_ATTEMPTS,
    MIN_LOOP_M,
    PRUNE_RESERVE_S,
    _candidates,
    _cut_loop,
    prune_loops,
)
from engine.matching.score import Score, target_extent_m
from engine.matching.types import JOIN_TOLERANCE_M, Segment
from engine.routing.geo import haversine_m, polyline_length_m
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
    router = TurnBlockRouter()
    assert run(segments, router=router, targets=[[W, x]], extent_m=400) == segments
    assert router.route_calls == []


def test_cut_whose_new_turn_valhalla_would_not_ride_is_not_made() -> None:
    # 從西往東到 X、往北繞一圈回到 X、再往北：剪完在 X 左轉，但 X 不能轉
    x = east(W, 500)
    segments = [seg([W, x, east(x, 30), east(north(x, -30), 30), north(x, -30), x, north(x, 400)])]
    targets = [[W, x, north(x, 400)]]
    assert run(segments, router=TurnBlockRouter(blocked=x), targets=targets) == segments
    free = run(segments, targets=targets)  # 對照：X 能轉彎就剪
    assert [s.points for s in free] == [[W, x], [x, north(x, 400)]]


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


def test_whole_route_being_the_loop_is_not_cut_to_nothing() -> None:
    loop = square(W, 30)
    segments = [seg(loop)]
    assert run(segments, targets=[[W, east(W, 200)]], extent_m=1000) == segments


def test_nothing_is_tried_when_the_deadline_is_too_close() -> None:
    x = east(W, 400)
    segments = [seg([W, *square(x, 30), C])]
    router = TurnBlockRouter()
    assert run(segments, router=router, deadline=Deadline(PRUNE_RESERVE_S - 0.05)) == segments


def test_pruning_stops_once_the_reserve_is_reached(monkeypatch: pytest.MonkeyPatch) -> None:
    # 兩個可以剪的圈；剪完第一個之後時間只剩不到保留量：停下，回傳剪了一個的結果
    now = [0.0]
    deadline = Deadline(1.0, clock=lambda: now[0])
    real_score = prune.score

    def slow_score(targets, route):
        now[0] += 0.4  # 每次評分 0.4 s
        return real_score(targets, route)

    monkeypatch.setattr(prune, "score", slow_score)
    segments = [seg([W, *square(east(W, 200), 20), *square(east(W, 600), 30)[1:], C])]
    result = run(segments, deadline=deadline)
    points = all_points(result)
    assert north(east(W, 200), 20) not in points  # 短的先剪
    assert north(east(W, 600), 30) in points  # 長的沒時間了


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
