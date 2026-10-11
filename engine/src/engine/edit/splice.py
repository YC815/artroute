"""把換上的新路線拼回原路線，並在拼接處做安全檢查（route editing spec §3.2）。

新的部分只來自 Valhalla（route_between），保留的部分只切既有的幾何（position.split）。
拼起來之後：新接點相距 ≤ JOIN_TOLERANCE_M、新接點用嚴格版轉彎檢查、離新接點不到 TURN_PROBE_M 的既有接點
（探針會伸進新幾何）用原本的規則重量，最後整條路線再跑一次連續性檢查。任何一項沒過都丟 EditRejected，不回半成品。
"""

from collections.abc import Sequence
from itertools import chain
from typing import Literal, Protocol

from engine.edit.position import Cut, precedes
from engine.matching.deadline import Deadline
from engine.matching.join import after_neighbours, before_neighbours, junction_ok
from engine.matching.service import check_continuous
from engine.matching.types import JOIN_TOLERANCE_M, MatchRouter, Segment, segment_from
from engine.routing.geo import haversine_m, polyline_length_m
from engine.routing.router import DRAG_SNAP_M, LonLat, RouteResult, RoutingError

type EditCode = Literal["not_on_route", "nothing_to_cut", "no_route", "turn_not_rideable", "no_road"]

DEFAULT_MESSAGES: dict[EditCode, str] = {
    "not_on_route": "選的位置不在路線上，請直接點在路線上",
    "nothing_to_cut": "這裡沒有可以剪掉的圈，可以改用「重畫・最短」把這段換掉",
    "no_route": "這兩個位置之間找不到能接回原路線的路，試試把範圍選大一點，或改用「拖曳」",
    "turn_not_rideable": "接回原路線的地方要迴轉或轉不過去，試試把範圍選大一點",
    "no_road": f"放開的位置 {DRAG_SNAP_M:.0f} 公尺內沒有可騎的路，或從那裡接不回原路線，試試放在路上",
}
NOTHING_LEFT_MESSAGE = "換完就沒有路線了，可以改用「重畫」把這段換成別的路"


class EditRejected(Exception):
    """編輯做不到（/edit 的 422）：message 是給使用者看的說明，附可以改用哪個操作。"""

    def __init__(self, code: EditCode, message: str | None = None) -> None:
        self.code = code
        self.message = DEFAULT_MESSAGES[code] if message is None else message
        super().__init__(self.message)


class EditRouter(MatchRouter, Protocol):
    def route_through(self, start: LonLat, via: LonLat, end: LonLat, *, shortest: bool = False) -> RouteResult: ...


def edit_segment(result: RouteResult, stroke_index: int | None) -> Segment:
    return segment_from("edit", stroke_index, result)


def replaced_stroke(middle: list[Segment]) -> int | None:
    """被換掉的範圍只屬於一個筆畫（connector 不屬於任何筆畫，不算）就沿用它，否則 None。"""
    strokes = {s.stroke_index for s in middle if s.kind != "connector"}
    return next(iter(strokes)) if len(strokes) == 1 else None


def _new_junctions(before: list[Segment], middle: list[Segment], after: list[Segment]) -> list[int]:
    """拼接後的新接點（segments[i] 與 [i + 1] 之間的 i）：相鄰兩個非空部分的交界。
    middle 是空的（純剪掉）時新接點是 before[-1]|after[0]；before 或 after 是空的那一側沒有接點。"""
    out, end = [], 0
    for part in (p for p in (before, middle, after) if p):
        if end:
            out.append(end - 1)
        end += len(part)
    return out


def splice(
    router: MatchRouter,
    deadline: Deadline,
    before: list[Segment],
    middle: list[Segment],
    after: list[Segment],
    *,
    strict: Sequence[int] = (),
) -> list[Segment]:
    """[*before, *middle, *after]，通過 §3.2 的檢查才回傳；接不回原路線丟 no_route、接點騎不過去丟 turn_not_rideable。
    strict：另外也算新接點的接點（拼好的整條路線上 segments[i] 與 [i + 1] 之間的 i）：呼叫端自己接好的部分
    （貼圖形用 join.connect 接回原路線，connect 只用原本的規則量）。
    DiscontinuousRoute（保留的部分自己有缺口）是程式錯誤，照樣往外丟。"""
    segments = [*before, *middle, *after]
    if not all(0 <= i < len(segments) - 1 for i in strict):
        raise ValueError(f"接點索引超出範圍：{list(strict)}（共 {len(segments)} 段）")
    new = sorted({*_new_junctions(before, middle, after), *strict})
    if any(haversine_m(segments[i].points[-1], segments[i + 1].points[0]) > JOIN_TOLERANCE_M for i in new):
        raise EditRejected("no_route")
    check_continuous(segments)
    if not any(polyline_length_m(s.points) > 0 for s in segments):
        raise EditRejected("nothing_to_cut", NOTHING_LEFT_MESSAGE)
    # 新接點可能是剪圈留下的立體交叉或原地迴轉：一律嚴格。它們也可能彼此相鄰（middle 很短），那時仍照嚴格算
    neighbours = dict.fromkeys(
        i for k in new for i in chain(before_neighbours(segments, k), after_neighbours(segments, k)) if i not in new
    )
    if not all(junction_ok(router, deadline, segments, i, strict=True) for i in new):
        raise EditRejected("turn_not_rideable")
    if not all(junction_ok(router, deadline, segments, i) for i in neighbours):
        raise EditRejected("turn_not_rideable")
    return segments


def _vertex(segments: Sequence[Segment], segment: int, k: int) -> Cut:
    """segments[segment].points[k] 這個頂點當切點（段尾照 position.Cut 的慣例：index 是倒數第二個點）。"""
    points = segments[segment].points
    return Cut(segment, min(k, len(points) - 2), points[k])


def _candidates(cuts: list[Cut]) -> list[Cut]:
    """去掉落在同一點的重複，保持順序。"""
    seen: set[LonLat] = set()
    out = []
    for c in cuts:
        if c.point not in seen:
            seen.add(c.point)
            out.append(c)
    return out


def route_between(
    router: EditRouter,
    deadline: Deadline,
    start: Cut,
    end: Cut,
    segments: Sequence[Segment],
    *,
    via: LonLat | None = None,
) -> tuple[RouteResult, Cut, Cut]:
    """start 到 end（有 via 就經過 via）的最短 route，回傳 (結果, 實際用的起點切點, 實際用的終點切點)。

    端點離要求的位置都要在 JOIN_TOLERANCE_M 內：Valhalla 會把落在用不了的 edge 上的起訖點吸到別處，
    收下的話接點就是一條直線。吸偏了就改用切點前後的頂點（先試把範圍放大的那個），最多 3 × 3 組，
    換了之後起點仍要在終點之前。RoutingError 算這一組失敗。實際用的切點要拿去重新 split，保留的部分才會跟著改。
    全部不行丟 no_route（拖曳由呼叫端改成 no_road）。
    """
    s, e = start.segment, end.segment
    starts = _candidates([start, _vertex(segments, s, start.index), _vertex(segments, s, start.index + 1)])
    ends = _candidates([end, _vertex(segments, e, end.index + 1), _vertex(segments, e, end.index)])
    pairs = sorted(
        ((i, j) for i in range(len(starts)) for j in range(len(ends)) if precedes(segments, starts[i], ends[j])),
        key=lambda ij: (sum(ij), ij),  # 先試只退一端的
    )
    for i, j in pairs:
        a, b = starts[i], ends[j]
        deadline.check()
        try:
            if via is None:
                result = router.route(a.point, b.point, shortest=True)
            else:
                result = router.route_through(a.point, via, b.point, shortest=True)
        except RoutingError:
            continue
        if (
            len(set(result.points)) >= 2
            and haversine_m(result.points[0], a.point) <= JOIN_TOLERANCE_M
            and haversine_m(result.points[-1], b.point) <= JOIN_TOLERANCE_M
        ):
            return result, a, b
    raise EditRejected("no_route")
