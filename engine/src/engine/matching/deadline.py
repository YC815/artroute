"""吸附的時間上限與取消：在每次呼叫 Valhalla 之前、每一輪 CPU 計算之間檢查（Valhalla 呼叫本身無法中斷）。"""

import threading
import time
from collections.abc import Callable


class MatchTimeout(Exception):
    pass


class MatchCancelled(Exception):
    """呼叫端已經離開：剩下的吸附沒有人會收到。"""


class Deadline:
    def __init__(self, seconds: float, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._end = clock() + seconds
        self._cancelled = threading.Event()  # 事件迴圈上設定，跑吸附的 worker thread 讀

    def cancel(self) -> None:
        self._cancelled.set()

    @property
    def cancelled(self) -> bool:
        return self._cancelled.is_set()

    def check(self) -> None:
        if self._cancelled.is_set():
            raise MatchCancelled("呼叫端已離開")
        if self._clock() > self._end:
            raise MatchTimeout("吸附超過時間上限")

    def remaining(self) -> float:
        """距離到期還有幾秒；到期後為 0（給等待時限這類不接受負數的地方用）。"""
        return max(0.0, self._end - self._clock())
