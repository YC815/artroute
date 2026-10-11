"""connect 的性質測試（不需要 Valhalla）：隨機的短片段鏈，加上 1–3 個不能轉彎的路口。

connect 有輸出時，每個接點都要通過跨段探針（tail_points／head_points，跟 bench 的 bad_turns 同一種量法）：
不論接點兩側的片段多短、新接上的 link 多接近既有的接點。輸入鏈本身的接點先要能騎（呼叫端就是這樣保證的：
鏈都是 connect 串出來的）。固定 seed，結果可重現。
"""

import random

import pytest

from engine.matching.deadline import Deadline
from engine.matching.join import connect
from engine.matching.types import Segment
from engine.routing.geo import haversine_m, polyline_length_m
from engine.routing.router import LonLat
from turn_router import TurnBlockRouter, east, every_junction_rideable, north

ORIGIN = (121.507, 25.05)
STEPS_M = [0.3, 0.6, 0.9, 2.0, 4.0, 6.0, 12.0, 25.0, 60.0]  # 很多不到 1 m 或不到 8 m 的片段
DIRECTIONS = [(1, 0), (0, 1), (-1, 0), (0, -1)]  # 東、北、西、南
OFFSETS_M = [0.0, 0.5, 5.0, 30.0, 50.0]  # 左鏈終點與右鏈起點的距離
SEEDS = range(10_000)
MIN_EMITTED = 2_000  # 通過前提而且接得上的案例至少這麼多，測試才不是空的
MIN_WITH_CONNECTOR = 1_000  # 其中有補 link 的至少這麼多


def walk(rng: random.Random, start: LonLat, heading: int, steps: int) -> tuple[list[LonLat], int]:
    points = [start]
    for _ in range(steps):
        if rng.random() < 0.5:
            heading = (heading + rng.choice([1, 3])) % 4
        dx, dy = DIRECTIONS[heading]
        meters = rng.choice(STEPS_M)
        points.append(north(east(points[-1], dx * meters), dy * meters))
    return points, heading


def as_chain(rng: random.Random, points: list[LonLat]) -> list[Segment]:
    cuts = sorted(rng.sample(range(1, len(points) - 1), k=min(len(points) - 2, rng.randint(0, 3))))
    bounds = [0, *cuts, len(points) - 1]
    # length_m 刻意比幾何長 0–1 m：Valhalla 摘要的 km 只有 3 位小數，記的長度不會剛好是探針走的那條折線
    pieces = list(zip(bounds, bounds[1:]))
    return [Segment("stroke", 0, points[a : b + 1], polyline_length_m(points[a : b + 1]) + rng.random()) for a, b in pieces]


def blocking(corners: list[LonLat]) -> TurnBlockRouter:
    return TurnBlockRouter(blocked=corners[0], also_blocked=tuple(corners[1:]))


def has_gap(segments: list[Segment]) -> bool:
    return any(haversine_m(a.points[-1], b.points[0]) > 1.0 for a, b in zip(segments, segments[1:]))


def test_connect_never_emits_a_junction_valhalla_would_not_ride() -> None:
    emitted = with_connector = 0
    bad_seeds: list[int] = []
    for seed in SEEDS:
        rng = random.Random(seed)
        left_points, heading = walk(rng, ORIGIN, rng.randrange(4), rng.randint(2, 6))
        dx, dy = DIRECTIONS[rng.randrange(4)]
        offset = rng.choice(OFFSETS_M)
        right_start = north(east(left_points[-1], dx * offset), dy * offset)
        right_points, _ = walk(rng, right_start, rng.randrange(4), rng.randint(2, 6))
        left, right = as_chain(rng, left_points), as_chain(rng, right_points)
        corners = rng.sample(left_points[1:-1] + right_points[1:-1] + [left_points[-1], right_points[0]], k=rng.randint(1, 3))
        if not (every_junction_rideable(blocking(corners), left) and every_junction_rideable(blocking(corners), right)):
            continue  # 前提：輸入鏈本身的接點能騎
        connected = connect(blocking(corners), Deadline(60), left, right, "connector", None)
        if connected is None:
            continue
        emitted += 1
        with_connector += any(s.kind == "connector" for s in connected)
        if has_gap(connected) or not every_junction_rideable(blocking(corners), connected):
            bad_seeds.append(seed)
    assert bad_seeds == [], f"{len(bad_seeds)} 個案例輸出了不能騎的接點，前 10 個 seed：{bad_seeds[:10]}"
    assert emitted >= MIN_EMITTED and with_connector >= MIN_WITH_CONNECTOR, (emitted, with_connector)
