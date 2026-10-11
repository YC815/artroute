"""拼接與安全檢查（route editing spec §3.2）：新換上的部分只來自 Valhalla，新接點 ≤ 1 m、用嚴格版轉彎檢查，
離新接點不到 8 m 的既有接點重量，最後整條路線連續；任何一項沒過都回錯，不回半成品。假 router 見 tests/edit_router.py。
"""

import pytest

from engine.edit.position import Cut, RoutePos, precedes, resolve, split
from engine.edit.splice import (
    DEFAULT_MESSAGES,
    EditRejected,
    edit_segment,
    replaced_stroke,
    route_between,
    splice,
)
from engine.matching.deadline import Deadline, MatchTimeout
from engine.matching.join import junction_ok
from engine.matching.service import DiscontinuousRoute
from engine.matching.types import JOIN_TOLERANCE_M, Segment
from engine.routing.geo import haversine_m, polyline_length_m
from engine.routing.router import RouteResult
from edit_router import FakeRouter
from turn_router import east, north

LAT = 25.05
J = (121.505, LAT)


def seg(points, kind="stroke", stroke_index: int | None = 0) -> Segment:
    return Segment(kind, stroke_index, points, polyline_length_m(points))


def deadline() -> Deadline:
    return Deadline(60)


def rejected_code(router, before, middle, after) -> str:
    with pytest.raises(EditRejected) as info:
        splice(router, deadline(), before, middle, after)
    return info.value.code


# ---- splice：新接點的缺口 ----

WEST_TO_J = seg([east(J, -100), J])
J_TO_K = seg([J, east(J, 100)], kind="edit")
K_ONWARD = seg([east(J, 100), east(J, 200)])


@pytest.mark.parametrize("before, middle, after", [
    ([WEST_TO_J], [seg([east(J, 3), east(J, 100)], kind="edit")], [K_ONWARD]),  # 起點側
    ([WEST_TO_J], [J_TO_K], [seg([east(J, 103), east(J, 200)])]),  # 終點側
    ([WEST_TO_J], [], [seg([east(J, 3), east(J, 100)])]),  # 純剪掉
], ids=["start", "end", "cut"])
def test_gap_at_a_new_junction_is_rejected(before, middle, after) -> None:
    """新接點離 3 m 是「接不回原路線」（no_route），不是程式錯誤。
    拿掉 splice 的新接點距離檢查（檢查 1）會紅：缺口會留到 check_continuous，丟的是 DiscontinuousRoute（500）。"""
    assert rejected_code(FakeRouter(), before, middle, after) == "no_route"


# ---- splice：新接點用嚴格版 ----

STRAIGHT_THROUGH_J = (east(J, -8), east(J, 8))  # 在 J 直行（0°）也騎不過去：例如下層道路接到正上方的高架


@pytest.mark.parametrize("before, middle, after, junction", [
    ([WEST_TO_J], [J_TO_K], [K_ONWARD], 0),
    ([seg([east(J, -200), east(J, -100)])], [seg([east(J, -100), J], kind="edit")], [seg([J, east(J, 100)])], 1),
    ([WEST_TO_J], [], [seg([J, east(J, 100)])], 0),
], ids=["start", "end", "cut"])
def test_new_junction_uses_strict_turn_check(before, middle, after, junction) -> None:
    """J 的轉角是 0°：非嚴格版直接放行、不量；嚴格版交給 route 量，繞一大圈 → turn_not_rideable。
    把 splice 對新接點的 junction_ok 改成 strict=False 會紅。"""
    router = FakeRouter(blocked=[STRAIGHT_THROUGH_J])
    segments = [*before, *middle, *after]
    assert junction_ok(router, deadline(), segments, junction, strict=False)  # 前提：非嚴格版會放行
    assert rejected_code(router, before, middle, after) == "turn_not_rideable"
    assert splice(FakeRouter(), deadline(), before, middle, after) == segments  # 對照：J 騎得過去就收


def test_junctions_given_as_strict_are_checked_like_new_ones() -> None:
    """呼叫端自己接好的整條路線（before、after 都是空的）：strict 列出的接點也用嚴格版。
    拿掉 splice 把 strict 併進新接點的那一行會紅。"""
    whole = [WEST_TO_J, J_TO_K, K_ONWARD]
    router = FakeRouter(blocked=[STRAIGHT_THROUGH_J])
    assert splice(router, deadline(), [], whole, []) == whole  # 對照：不說就只是保留的接點
    with pytest.raises(EditRejected) as info:
        splice(router, deadline(), [], whole, [], strict=[0])
    assert info.value.code == "turn_not_rideable"


def test_strict_junctions_must_be_inside_the_route() -> None:
    with pytest.raises(ValueError):
        splice(FakeRouter(), deadline(), [], [WEST_TO_J, J_TO_K], [], strict=[1])


# ---- splice：鄰近既有接點重量 ----

M = north(J, -7)  # 原路線在 J 由東轉南，7 m 後在 M 切開，換上往東的新路


def test_existing_junction_before_a_new_one_is_rechecked() -> None:
    """J 是 M（新接點）前面 7 m 的既有接點：它往後 8 m 的探針原本落在往南的原路上，現在落在往東的新路上，
    「西→南→東」這個急轉騎不過去。新接點本身與 J 原本的轉彎都能騎，只有重量 J 才擋得住。
    拿掉 splice 的鄰近接點重量（before_neighbours／after_neighbours 那一段）會紅。"""
    west, south = seg([east(J, -100), J]), seg([J, M])
    router = FakeRouter(blocked=[(east(J, -8), east(M, 1))])
    assert junction_ok(router, deadline(), [west, south, seg([M, north(M, -100)])], 0)  # 前提：原本能騎
    before, middle, after = [west, south], [seg([M, east(M, 100)], kind="edit")], [seg([east(M, 100), east(M, 200)])]
    assert junction_ok(router, deadline(), [*before, *middle, *after], 1, strict=True)  # 前提：新接點能騎
    assert rejected_code(router, before, middle, after) == "turn_not_rideable"
    assert splice(FakeRouter(), deadline(), before, middle, after) == [*before, *middle, *after]


def test_existing_junction_after_a_new_one_is_rechecked() -> None:
    """上一個測試反過來騎：新路由東往西到 M，接回原路往北 7 m 到 J、在 J 往西。
    拿掉 splice 的鄰近接點重量會紅。"""
    north_leg, west = seg([M, J]), seg([J, east(J, -100)])
    router = FakeRouter(blocked=[(east(M, 1), east(J, -8))])
    assert junction_ok(router, deadline(), [seg([north(M, -100), M]), north_leg, west], 1)  # 前提：原本能騎
    before, middle, after = [seg([east(M, 200), east(M, 100)])], [seg([east(M, 100), M], kind="edit")], [north_leg, west]
    assert junction_ok(router, deadline(), [*before, *middle, *after], 1, strict=True)  # 前提：新接點能騎
    assert rejected_code(router, before, middle, after) == "turn_not_rideable"
    assert splice(FakeRouter(), deadline(), before, middle, after) == [*before, *middle, *after]


# ---- splice：連續性與結果不能是空的 ----


def test_gap_inside_the_kept_part_is_a_program_error() -> None:
    """保留的部分本身有 3 m 的缺口（不是新接點）：輸出就會多出一條直線，是程式錯誤（DiscontinuousRoute）。
    拿掉 splice 的 check_continuous 會紅。"""
    before = [seg([east(J, -300), east(J, -200)]), seg([east(J, -197), J])]
    with pytest.raises(DiscontinuousRoute):
        splice(FakeRouter(), deadline(), before, [J_TO_K], [K_ONWARD])


@pytest.mark.parametrize("before, middle, after", [
    ([], [], []),
    ([seg([J, J])], [], []),
    ([], [seg([J, J], kind="edit")], [seg([J, J])]),
])
def test_nothing_left_is_rejected(before, middle, after) -> None:
    assert rejected_code(FakeRouter(), before, middle, after) == "nothing_to_cut"


# ---- 整個流程：route_between → split → splice ----

STEP = 0.001  # 約 100 m
A, B, C, D = [(121.500 + STEP * i, LAT) for i in range(4)]
E, MID, F = (121.503, LAT + STEP), (121.503, LAT + 1.5 * STEP), (121.503, LAT + 2 * STEP)
ROUTE = [
    seg([A, B, C]),
    seg([C, D], kind="detour"),
    seg([D, E], kind="connector", stroke_index=None),
    seg([E, MID, F]),
]
ORIGINAL = {p for s in ROUTE for p in s.points}


def snap_off_route(point):
    """原本的頂點吸得準；頂點之間的內插點被吸到 5 m 外（落在 route 用不了的 edge 上）。"""
    return point if point in ORIGINAL else north(point, 5)


def edit(router, start: Cut, end: Cut) -> tuple[list[Segment], list[Segment], list[Segment], list[Segment]]:
    result, start, end = route_between(router, deadline(), start, end, ROUTE)
    before, middle, after = split(ROUTE, start, end)
    new = [edit_segment(result, replaced_stroke(middle))]
    return before, new, after, splice(router, deadline(), before, new, after)


def test_output_is_continuous_and_reuses_original_points() -> None:
    start, end = resolve(ROUTE, RoutePos(0, 150.0)), resolve(ROUTE, RoutePos(3, 40.0))
    router = FakeRouter()
    before, new, after, out = edit(router, start, end)
    assert out == [*before, *new, *after]
    for left, right in zip(out, out[1:]):
        assert haversine_m(left.points[-1], right.points[0]) <= JOIN_TOLERANCE_M
    kept = {p for s in [*before, *after] for p in s.points}
    assert kept <= ORIGINAL | {start.point, end.point}  # 保留的只切原本的幾何
    (_, route_start, route_end, shortest), = router.calls[:1]
    assert (route_start, route_end, shortest) == (start.point, end.point, True)
    assert new[0].points == [start.point, end.point]  # 新的部分原封不動來自 route
    assert (new[0].kind, new[0].stroke_index) == ("edit", 0)


def test_route_between_falls_back_to_neighbour_vertices() -> None:
    """內插的切點被吸到 5 m 外，頂點吸得準：改用頂點，回傳的 Cut 就是那個頂點。
    拿掉 route_between 的端點 ≤ JOIN_TOLERANCE_M 檢查會紅（會收下吸偏 5 m 的結果）。"""
    start, end = resolve(ROUTE, RoutePos(0, 150.0)), resolve(ROUTE, RoutePos(3, 40.0))
    router = FakeRouter(snap=snap_off_route)
    result, start_cut, end_cut = route_between(router, deadline(), start, end, ROUTE)
    assert start_cut == Cut(0, 1, B) and end_cut == Cut(3, 1, MID)  # 往外各退到最近的頂點
    assert (result.points[0], result.points[-1]) == (B, MID)
    before, middle, after = split(ROUTE, start_cut, end_cut)
    out = splice(FakeRouter(), deadline(), before, [edit_segment(result, replaced_stroke(middle))], after)
    assert [s.points for s in out] == [[A, B], [B, MID], [MID, F]]


def test_route_between_uses_the_segment_end_convention_for_the_last_vertex() -> None:
    """退到段尾的頂點時 Cut 照 position 的慣例：index 是倒數第二個點、point 是最後一個點。"""
    start = resolve(ROUTE, RoutePos(0, haversine_m(A, B)))  # 剛好在頂點 B 上
    end = resolve(ROUTE, RoutePos(3, haversine_m(E, MID) + 30.0))  # MID 與 F 之間
    _, start_cut, end_cut = route_between(FakeRouter(snap=snap_off_route), deadline(), start, end, ROUTE)
    assert start_cut == Cut(0, 1, B)
    assert end_cut == Cut(3, 1, F)
    assert split(ROUTE, start_cut, end_cut)[2] == []


def test_route_between_gives_up_after_nine_tries() -> None:
    """RoutingError 與吸偏的結果都算這一組失敗；3 × 3 組都不行就 no_route。
    拿掉端點 ≤ JOIN_TOLERANCE_M 檢查會紅（吸偏的那個 router 會被收下）。"""
    start, end = resolve(ROUTE, RoutePos(0, 150.0)), resolve(ROUTE, RoutePos(3, 40.0))
    for router in (FakeRouter(fail=True), FakeRouter(snap=lambda p: north(p, 5))):
        with pytest.raises(EditRejected) as info:
            route_between(router, deadline(), start, end, ROUTE)
        assert info.value.code == "no_route"
        assert len(router.calls) == 9 and len(set(router.calls)) == 9


def test_route_between_keeps_the_start_before_the_end() -> None:
    """同一條邊上的兩個切點：換成頂點後可能跑到另一個切點的另一邊（split 會倒著切），那些組合不試。
    拿掉 route_between 的 precedes 過濾會紅。"""
    start, end = resolve(ROUTE, RoutePos(0, 20.0)), resolve(ROUTE, RoutePos(0, 40.0))
    router = FakeRouter(fail=True)
    with pytest.raises(EditRejected):
        route_between(router, deadline(), start, end, ROUTE)
    tried = {(a, b) for _, a, b, _ in router.calls}
    assert tried == {(start.point, end.point), (start.point, B), (A, end.point), (A, B)}


def test_route_between_rejects_a_collapsed_result() -> None:
    """起訖都吸到同一點（端點仍在 1 m 內）：收下就是零長度的一段。拿掉 route_between 的「至少 2 個不同點」會紅。"""
    router = FakeRouter(snap=lambda p: J)
    with pytest.raises(EditRejected):
        route_between(router, deadline(), Cut(0, 0, J), Cut(0, 0, east(J, 0.5)), [seg([J, east(J, 1)])])


def test_route_between_routes_through_the_via_point() -> None:
    start, end = resolve(ROUTE, RoutePos(0, 150.0)), resolve(ROUTE, RoutePos(3, 40.0))
    via = north(C, 30)
    router = FakeRouter()
    result, _, _ = route_between(router, deadline(), start, end, ROUTE, via=via)
    assert router.calls == [("route_through", start.point, via, end.point, True)]
    assert result.points == [start.point, via, end.point]


def test_route_between_checks_the_deadline_before_each_try() -> None:
    start, end = resolve(ROUTE, RoutePos(0, 150.0)), resolve(ROUTE, RoutePos(3, 40.0))
    router = FakeRouter(fail=True)
    ticks = iter([0.0] + [0.0] * 3 + [99.0] * 100)  # 建立、前三次檢查還沒到期，之後到期
    with pytest.raises(MatchTimeout):
        route_between(router, Deadline(1, clock=lambda: next(ticks)), start, end, ROUTE)
    assert len(router.calls) == 3


# ---- 小工具 ----


def test_edit_segment_takes_the_route_as_is() -> None:
    result = RouteResult(points=[A, B, C], length_km=0.2)
    assert edit_segment(result, 3) == Segment("edit", 3, [A, B, C], 200.0)


@pytest.mark.parametrize("middle, expected", [
    ([seg([A, B], stroke_index=1), seg([B, C], kind="detour", stroke_index=1)], 1),
    ([seg([A, B], stroke_index=1), seg([B, C], kind="connector", stroke_index=None), seg([C, D], stroke_index=1)], 1),
    ([seg([A, B], stroke_index=1), seg([B, C], stroke_index=2)], None),
    ([seg([A, B], kind="connector", stroke_index=None)], None),
    ([seg([A, B], kind="edit", stroke_index=None), seg([B, C], stroke_index=1)], None),
    ([], None),
])
def test_replaced_stroke(middle, expected) -> None:
    assert replaced_stroke(middle) == expected


def test_every_code_has_a_message_for_the_user() -> None:
    assert set(DEFAULT_MESSAGES) == {"not_on_route", "nothing_to_cut", "no_route", "turn_not_rideable", "no_road"}
    assert EditRejected("no_route").message == DEFAULT_MESSAGES["no_route"]
    assert EditRejected("no_route", "兩個點太近").message == "兩個點太近"
    assert str(EditRejected("no_road")) == DEFAULT_MESSAGES["no_road"]


def test_precedes_compares_cuts_along_the_route() -> None:
    early, late = resolve(ROUTE, RoutePos(0, 20.0)), resolve(ROUTE, RoutePos(0, 40.0))
    assert precedes(ROUTE, early, late) and not precedes(ROUTE, late, early)
    assert not precedes(ROUTE, early, early)
    assert precedes(ROUTE, Cut(0, 1, C), Cut(1, 0, C))  # 段尾與下一段開頭是同一點，但在路線上有先後
