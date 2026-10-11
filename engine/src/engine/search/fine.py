"""精算（spec §4.6 第 4 步）：粗篩的候選依序跑完整吸附（分治吸附、串接、評分），排出前 5 名。

- 每個候選都帶起點提示（StartHint = 搜尋的圓）吸附；路線起點不在圈內的直接淘汰（使用者的硬條件）。
- 公里數不在範圍內的，依實際比例修正大小後重算一次（排在佇列最前面），兩個結果留比較好的那個。
- 多個 Router 平行跑（每個 Router 一個 Actor，不能共用）：每個 thread 固定用自己的 Router。
- 停止條件：至少跑完 MIN_EVALUATED 個、而且已有 TOP_N 個合格的候選；或超過 SOFT_BUDGET_S 且已有 TOP_N 個
  起點在圈內的候選。都不成立就繼續，直到候選用完或整個搜尋的期限（Deadline）到期。
"""

import math
import threading
import time
from collections import deque
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from engine.matching.deadline import Deadline, MatchCancelled, MatchTimeout
from engine.matching.service import AllStrokesFailed, MatchResult, OutsideTaiwan, TargetTooSmall, match_shape
from engine.matching.types import MatchRouter, StartHint
from engine.routing.geo import haversine_m
from engine.routing.router import RoutingError
from engine.search.coarse import MAX_SIZE_M, MIN_SIZE_M
from engine.search.geometry import place
from engine.search.types import Candidate, Evaluation, Placement, SearchParams

TOP_N = 5
MIN_EVALUATED = 10  # spec §4.6：前 K=10 名跑完整吸附
SOFT_BUDGET_S = 25.0  # 完成條件是 30 秒內回傳前 5 名：超過這個時間、手上已有 5 個就不再開新的吸附
MATCH_TIMEOUT_S = 10.0  # 單一候選的吸附上限（/match 是 5 秒；搜尋的圖形可能比較大）
RESCALE_MARGIN = 0.1  # 重算時把目標公里數放在範圍內側 10% 處，避免依比例修正後又剛好落在邊界外


@dataclass(frozen=True)
class _Job:
    placement: Placement
    rescaled: bool  # 已經依公里數修正過一次（不再修正）
    origin: int  # 來自第幾個粗篩候選（修正前後算同一個）


def rank_key(candidate: Candidate, params: SearchParams) -> tuple:
    """排序：公里數合格的在前，依整體貼合度；不合格的依離範圍多遠，再看貼合度；最後看起點離圓心多近。"""
    length_km = candidate.result.route_length_m / 1000
    miss = max(0.0, params.min_km - length_km, length_km - params.max_km)
    return (not candidate.length_ok, miss, -candidate.result.score.overall, candidate.start_distance_m)


def _desired_km(params: SearchParams, length_km: float) -> float:
    margin = RESCALE_MARGIN * (params.max_km - params.min_km)
    return min(max(length_km, params.min_km + margin), params.max_km - margin)


class FineStage:
    def __init__(
        self,
        params: SearchParams,
        routers: Sequence[MatchRouter],
        deadline: Deadline,
        on_progress: Callable[[int, int], None],
        on_best: Callable[[Candidate], None],
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.params = params
        self.routers = routers
        self.deadline = deadline
        self.on_progress = on_progress
        self.on_best = on_best
        self.clock = clock
        self.started = clock()
        self.lock = threading.Condition()  # 共用狀態的鎖；佇列空了但還有在跑的吸附時，在這裡等它回來
        self.in_flight = 0
        self.queue: deque[_Job] = deque()
        self.by_origin: dict[int, Candidate] = {}  # 每個粗篩候選只留最好的一個結果
        self.evaluations: list[Evaluation] = []
        self.done = 0
        self.best: Candidate | None = None
        self.stop = False
        self.failure: BaseException | None = None

    def _match(self, router: MatchRouter, placement: Placement) -> MatchResult | None:
        deadline = Deadline(MATCH_TIMEOUT_S, parent=self.deadline)
        strokes = place(self.params.strokes, placement)
        start = StartHint(self.params.center, self.params.radius_m)
        try:
            return match_shape(router, strokes, search_radius_m=None, deadline=deadline, start=start)
        except (AllStrokesFailed, OutsideTaiwan, TargetTooSmall, RoutingError):
            return None
        except MatchTimeout:
            if self.deadline.remaining() <= 0:
                raise  # 整個搜尋到期
            return None  # 只是這個候選太慢

    def _candidate(self, placement: Placement, result: MatchResult) -> Candidate | None:
        start_m = haversine_m(result.route_points[0], self.params.center)
        if start_m > self.params.radius_m:
            return None
        length_km = result.route_length_m / 1000
        ok = self.params.min_km <= length_km <= self.params.max_km
        return Candidate(placement, result, start_m, ok)

    def _rescaled(self, job: _Job, result: MatchResult) -> _Job | None:
        length_km = result.route_length_m / 1000
        if job.rescaled or length_km <= 0:
            return None
        size = job.placement.size_m * _desired_km(self.params, length_km) / length_km
        size = min(MAX_SIZE_M, max(MIN_SIZE_M, size))
        if math.isclose(size, job.placement.size_m, rel_tol=1e-3):
            return None
        return _Job(Placement(job.placement.center, size, job.placement.rotation_deg), True, job.origin)

    def _enough(self) -> bool:
        results = list(self.by_origin.values())
        qualified = sum(c.length_ok for c in results)
        if self.done >= MIN_EVALUATED and qualified >= TOP_N:
            return True
        return self.clock() - self.started >= SOFT_BUDGET_S and len(results) >= TOP_N

    def _record(self, job: _Job, result: MatchResult | None) -> None:
        with self.lock:
            try:
                self.done += 1
                candidate = None
                if result is not None:
                    candidate = self._candidate(job.placement, result)
                self.evaluations.append(
                    Evaluation(
                        job.origin, job.rescaled, None if result is None else result.score.overall,
                        candidate is not None, bool(candidate and candidate.length_ok),
                    )
                )
                if result is not None:
                    if candidate is not None:
                        previous = self.by_origin.get(job.origin)
                        if previous is None or rank_key(candidate, self.params) < rank_key(previous, self.params):
                            self.by_origin[job.origin] = candidate
                            if self.best is None or rank_key(candidate, self.params) < rank_key(self.best, self.params):
                                self.best = candidate
                                self.on_best(candidate)
                    if not (candidate and candidate.length_ok) and (follow := self._rescaled(job, result)):
                        self.queue.appendleft(follow)  # 起點在圈外的也修正一次：大小變了，起點可能跟著進圈
            finally:
                self.in_flight -= 1  # 例外也要減：不然其他 thread 會一直等它回來
                self.on_progress(self.done, self.done + len(self.queue))
                self.lock.notify_all()

    def _next(self) -> _Job | None:
        """下一個要跑的候選；該停了回傳 None。佇列空了但別的 thread 還在吸附時要等：
        它回來可能排進修正大小的重算（審查：直接停掉的話，最後幾個候選的公里數修正會被丟掉）。"""
        with self.lock:
            while True:
                if self.stop or self._enough():
                    self.stop = True
                    self.lock.notify_all()
                    return None
                if self.queue:
                    self.in_flight += 1
                    return self.queue.popleft()
                if self.in_flight == 0:
                    self.stop = True
                    self.lock.notify_all()
                    return None
                self.lock.wait()

    def _worker(self, router: MatchRouter) -> None:
        while True:
            job = self._next()
            if job is None:
                return
            try:
                result = self._match(router, job.placement)
            except BaseException as error:  # MatchCancelled／整個搜尋 MatchTimeout：其他 thread 也停
                with self.lock:
                    self.in_flight -= 1
                    self.stop = True
                    self.failure = self.failure or error
                    self.lock.notify_all()
                return
            try:
                self._record(job, result)
            except BaseException as error:
                with self.lock:
                    self.stop = True
                    self.failure = self.failure or error
                    self.lock.notify_all()
                return

    def run(self, placements: list[Placement]) -> list[Candidate]:
        self.queue.extend(_Job(p, False, k) for k, p in enumerate(placements))
        self.on_progress(0, len(self.queue))
        if self.queue:
            with ThreadPoolExecutor(max_workers=len(self.routers), thread_name_prefix="search") as pool:
                for future in [pool.submit(self._worker, router) for router in self.routers]:
                    future.result()
        results = sorted(self.by_origin.values(), key=lambda c: rank_key(c, self.params))
        if isinstance(self.failure, MatchCancelled):
            raise self.failure
        if self.failure is not None and not (isinstance(self.failure, MatchTimeout) and results):
            raise self.failure  # 整個搜尋到期而且一個結果都沒有，或程式錯誤
        return results[:TOP_N]
