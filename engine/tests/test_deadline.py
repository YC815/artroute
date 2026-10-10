import pytest

from engine.matching.deadline import Deadline, MatchTimeout


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
