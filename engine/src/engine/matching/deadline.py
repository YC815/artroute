"""吸附的時間上限與取消：在每次呼叫 Valhalla 之前、每一輪 CPU 計算之間檢查（Valhalla 呼叫本身無法中斷）。"""

import threading
import time
from collections.abc import Callable


class MatchTimeout(Exception):
    pass


class MatchCancelled(Exception):
    """呼叫端已經離開：剩下的吸附沒有人會收到。"""


class Deadline:
    """parent：外層的期限（例如整個自動搜尋）。外層取消或到期時，這個期限的 check 也會丟同樣的例外。"""

    def __init__(
        self, seconds: float, clock: Callable[[], float] = time.monotonic, parent: "Deadline | None" = None
    ) -> None:
        self._clock = clock
        self._end = clock() + seconds
        self._cancelled = threading.Event()  # 事件迴圈上設定，跑吸附的 worker thread 讀
        self._parent = parent

    def cancel(self) -> None:
        self._cancelled.set()

    @property
    def cancelled(self) -> bool:
        return self._cancelled.is_set() or (self._parent is not None and self._parent.cancelled)

    def check(self) -> None:
        if self._parent is not None:
            self._parent.check()
        if self._cancelled.is_set():
            raise MatchCancelled("呼叫端已離開")
        if self._clock() > self._end:
            raise MatchTimeout("吸附超過時間上限")

    def child(self, seconds: float) -> "Deadline":
        """同一個時鐘、以自己為外層的子期限：子期限先到期時，呼叫端可以接住它的 MatchTimeout、外層照常進行。"""
        return Deadline(seconds, self._clock, parent=self)

    def remaining(self) -> float:
        """距離到期還有幾秒（外層更早到期時取外層）；到期後為 0（給等待時限這類不接受負數的地方用）。"""
        own = max(0.0, self._end - self._clock())
        return own if self._parent is None else min(own, self._parent.remaining())
