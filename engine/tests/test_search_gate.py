"""搜尋名額：跨行程（兩個 SearchGate 開同一個鎖檔，等同兩個 uvicorn worker）與行程內先到先得。"""

import asyncio
import subprocess
import sys
from pathlib import Path

import pytest

from engine.search.gate import SearchBusy, SearchGate


def run(coro):
    return asyncio.run(coro)


def test_two_gates_on_one_file_exclude_each_other(tmp_path: Path) -> None:
    async def scenario() -> None:
        a, b = SearchGate(tmp_path / "lock", poll_s=0.01), SearchGate(tmp_path / "lock", poll_s=0.01)
        assert await a.acquire(1, lambda: False, lambda p: None)
        positions: list[int] = []
        with pytest.raises(SearchBusy):
            await b.acquire(0.1, lambda: False, positions.append)
        assert positions == [1]
        a.release()
        assert await b.acquire(1, lambda: False, lambda p: None)
        b.release()

    run(scenario())


def test_lock_is_held_across_processes(tmp_path: Path) -> None:
    holder = (
        "import fcntl, os, sys, time\n"
        f"fd = os.open({str(tmp_path / 'lock')!r}, os.O_RDWR | os.O_CREAT)\n"
        "fcntl.flock(fd, fcntl.LOCK_EX)\n"
        "print('held', flush=True)\n"
        "sys.stdin.read()\n"
    )
    proc = subprocess.Popen([sys.executable, "-c", holder], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        assert proc.stdout.readline().strip() == "held"

        async def blocked() -> None:
            with pytest.raises(SearchBusy):
                await SearchGate(tmp_path / "lock", poll_s=0.01).acquire(0.1, lambda: False, lambda p: None)

        run(blocked())
    finally:
        proc.communicate("")
    # 另一個行程結束（鎖跟著釋放）後拿得到
    run(SearchGate(tmp_path / "lock", poll_s=0.01).acquire(1, lambda: False, lambda p: None))


def test_waiters_in_one_process_are_served_in_order(tmp_path: Path) -> None:
    async def scenario() -> list[str]:
        gate = SearchGate(tmp_path / "lock", poll_s=0.005)
        order: list[str] = []
        reports: dict[str, list[int]] = {"b": [], "c": []}
        assert await gate.acquire(1, lambda: False, lambda p: None)

        async def wait(name: str) -> None:
            assert await gate.acquire(2, lambda: False, reports[name].append)
            order.append(name)
            await asyncio.sleep(0.02)
            gate.release()

        tasks = [asyncio.create_task(wait("b"))]
        await asyncio.sleep(0.01)
        tasks.append(asyncio.create_task(wait("c")))
        await asyncio.sleep(0.03)
        gate.release()
        await asyncio.gather(*tasks)
        assert reports == {"b": [1], "c": [2, 1]}
        return order

    assert run(scenario()) == ["b", "c"]


def test_caller_leaving_while_queued_leaves_the_queue(tmp_path: Path) -> None:
    async def scenario() -> None:
        gate = SearchGate(tmp_path / "lock", poll_s=0.005)
        assert await gate.acquire(1, lambda: False, lambda p: None)
        gone = [False]
        waiter = asyncio.create_task(gate.acquire(5, lambda: gone[0], lambda p: None))
        await asyncio.sleep(0.02)
        gone[0] = True
        assert await waiter is False
        cancelled = asyncio.create_task(gate.acquire(5, lambda: False, lambda p: None))
        await asyncio.sleep(0.02)
        cancelled.cancel()
        with pytest.raises(asyncio.CancelledError):
            await cancelled
        assert gate._queue == []
        gate.release()

    run(scenario())
