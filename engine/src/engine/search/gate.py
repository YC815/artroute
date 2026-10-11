"""自動搜尋的併發上限（spec §5）：整個引擎同時只跑 1 個搜尋，其餘排隊並回報排隊位置。

uvicorn 有多個 worker 行程，所以名額用檔案鎖（flock）跨行程共用；同一個行程內的等待者先到先得。
排隊位置只算得到這個行程內的等待者：另一個 worker 也有人在等時，實際位置可能更後面（近似值）。
flock 綁在開啟的檔案描述上：行程結束（包括崩潰）時鎖自動釋放，不會留下死鎖。
"""

import asyncio
import fcntl
import os
import time
from collections.abc import Callable
from pathlib import Path

POLL_S = 0.2


class SearchBusy(Exception):
    """排隊超過時間上限。"""


class SearchGate:
    def __init__(self, lock_path: Path, poll_s: float = POLL_S) -> None:
        self._path = lock_path
        self._poll_s = poll_s
        self._fd: int | None = None
        self._queue: list[object] = []  # 這個行程內的等待者，先到先得
        self._running = False

    def _try_lock(self) -> bool:
        if self._fd is None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._fd = os.open(self._path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        return True

    async def acquire(
        self, timeout_s: float, cancelled: Callable[[], bool], on_queued: Callable[[int], None]
    ) -> bool:
        """拿到名額回傳 True；呼叫端離開回傳 False；等超過 timeout_s 丟 SearchBusy。
        還在排隊時，位置變了就呼叫 on_queued(位置)（位置 1 = 前面只有正在跑的那個）。"""
        me = object()
        self._queue.append(me)
        give_up = time.monotonic() + timeout_s
        reported: int | None = None
        try:
            while not cancelled():
                if self._queue[0] is me and not self._running and self._try_lock():
                    self._running = True
                    return True
                position = self._queue.index(me) + 1
                if position != reported:
                    on_queued(position)
                    reported = position
                if time.monotonic() >= give_up:
                    raise SearchBusy("排隊的搜尋太多，請稍後再試")
                await asyncio.sleep(self._poll_s)
            return False
        finally:
            self._queue.remove(me)

    def release(self) -> None:
        if not self._running:
            return
        self._running = False
        if self._fd is not None:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
