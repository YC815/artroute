"""吸附的時間上限：在每次呼叫 Valhalla 之前檢查（呼叫本身無法中斷）。"""

import time
from collections.abc import Callable


class MatchTimeout(Exception):
    pass


class Deadline:
    def __init__(self, seconds: float, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._end = clock() + seconds

    def check(self) -> None:
        if self._clock() > self._end:
            raise MatchTimeout("吸附超過時間上限")

    def remaining(self) -> float:
        """距離到期還有幾秒；到期後為 0（給 acquire(timeout=...) 這類不接受負數的等待用）。"""
        return max(0.0, self._end - self._clock())
