import threading

import pytest

from engine.matching.deadline import Deadline, MatchCancelled, MatchTimeout


class FakeClock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def test_remaining_counts_down_from_creation() -> None:
    clock = FakeClock()
    deadline = Deadline(5.0, clock)
    assert deadline.remaining() == pytest.approx(5.0)
    clock.now += 3.5
    assert deadline.remaining() == pytest.approx(1.5)


def test_remaining_never_goes_negative() -> None:
    clock = FakeClock()
    deadline = Deadline(1.0, clock)
    clock.now += 10.0
    assert deadline.remaining() == 0.0
    assert Deadline(-1.0, clock).remaining() == 0.0


def test_remaining_agrees_with_check() -> None:
    clock = FakeClock()
    deadline = Deadline(1.0, clock)
    clock.now += 1.0
    deadline.check()  # 剛好到期那一刻還不算逾時（check 用 >），remaining 為 0
    assert deadline.remaining() == 0.0
    clock.now += 0.001
    with pytest.raises(MatchTimeout):
        deadline.check()


def test_cancel_makes_check_raise_match_cancelled() -> None:
    deadline = Deadline(60)
    deadline.check()
    assert not deadline.cancelled
    deadline.cancel()
    assert deadline.cancelled
    with pytest.raises(MatchCancelled):
        deadline.check()


def test_cancelled_wins_over_timeout() -> None:
    # 呼叫端已經離開時，結果是「沒人要」而不是「逾時」：記錄成 499 而不是 504
    clock = FakeClock()
    deadline = Deadline(1.0, clock)
    clock.now += 10.0
    deadline.cancel()
    with pytest.raises(MatchCancelled):
        deadline.check()


def test_cancel_from_another_thread_is_seen_by_check() -> None:
    deadline = Deadline(60)
    worker = threading.Thread(target=deadline.cancel)
    worker.start()
    worker.join()
    with pytest.raises(MatchCancelled):
        deadline.check()


def test_child_deadline_follows_parent_cancel_and_timeout() -> None:
    now = [0.0]
    parent = Deadline(10, clock=lambda: now[0])
    child = Deadline(100, clock=lambda: now[0], parent=parent)
    child.check()
    assert child.remaining() == 10  # 外層較早到期
    now[0] = 11
    with pytest.raises(MatchTimeout):
        child.check()
    other = Deadline(100, clock=lambda: now[0], parent=Deadline(100, clock=lambda: now[0]))
    other._parent.cancel()
    assert other.cancelled
    with pytest.raises(MatchCancelled):
        other.check()


def test_child_deadline_times_out_on_its_own() -> None:
    now = [0.0]
    child = Deadline(1, clock=lambda: now[0], parent=Deadline(100, clock=lambda: now[0]))
    now[0] = 2
    with pytest.raises(MatchTimeout):
        child.check()
    assert not child.cancelled
