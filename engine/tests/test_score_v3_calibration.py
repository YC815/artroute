"""評分 v3 和使用者盲測標註的一致率（P2.5 spec §4.4，資料在 tests/data/likeness/）。

這是防退步，不是 80% 門檻：第一輪有表態的配對只有 15 組，分不出評分的好壞（使用者決定，理由見主 spec §8）。
v3 用 spec 的初始參數、配對用 rank 比較、不取整數。對照：v2（score_v2 的 overall，同樣套 rank）也是 10/15。
"""

from collections.abc import Sequence

import pytest

from engine.bench.likeness import Pair, Sample, agreement, initial_v3, kind, load

# 2026-10-11 實測（python -m engine.bench.likeness）：有表態 15 組一致 10 組；現行 vs V2 1/2、現行 vs V3 4/6、不同擺放 5/7
V3_AGREED = 10


def test_score_v3_calibration() -> None:
    samples, pairs = load()
    report = agreement(samples, pairs, initial_v3)
    assert report.decided == 15
    assert report.agreed >= V3_AGREED
    assert report.loco_agreed == report.agreed  # 初始參數沒有擬合任何東西：交叉驗證等於樣本內
    # 「差不多」的配對分數差距應該比有表態的小（只是健全性檢查：第一輪是 0.65 對 1.12）
    assert report.same_gap_median < report.decided_gap_median


def test_load_rebuilds_targets_and_routes() -> None:
    samples, pairs = load()
    assert len(pairs) == 36
    assert all((p.case, p.left) in samples and (p.case, p.right) in samples for p in pairs)
    heart = samples[("heart@taipei_daan", "V00")]
    assert len(heart.targets) == 1 and heart.targets[0][0] == heart.targets[0][-1]  # 封閉筆畫補回起點
    assert all(a != b for a, b in zip(heart.route, heart.route[1:]))  # 相鄰段共用的點只留一次


# ---- 一致率的算法（假的評分，不讀資料） ----


def fake_sample(case: str, key: str) -> Sample:
    return Sample(case, key, [], [], 0.0)


SAMPLES = {(c, k): fake_sample(c, k) for c in ("a", "b") for k in ("V00", "V01", "V20", "V30")}
PAIRS = [
    Pair(1, "a", "V00", "V20", "L"),
    Pair(2, "a", "V30", "V00", "R"),
    Pair(3, "a", "V00", "V01", "same"),
    Pair(4, "b", "V01", "V00", "L"),
    Pair(5, "b", "V00", "V30", "R"),
]


def fixed(values: dict[str, float]):
    def fit(_training: Sequence[Pair]):
        return lambda sample: values[sample.key]

    return fit


def test_ties_count_as_disagreement() -> None:
    report = agreement(SAMPLES, PAIRS, fixed({"V00": 1.0, "V01": 1.0, "V20": 1.0, "V30": 1.0}))
    assert (report.agreed, report.decided, report.loco_agreed) == (0, 4, 0)


def test_counts_by_kind_and_gaps() -> None:
    report = agreement(SAMPLES, PAIRS, fixed({"V00": 80.0, "V01": 79.0, "V20": 75.0, "V30": 90.0}))
    # 1：80 > 75 選 L ○；2：90 > 80 但選 R ×；4：79 < 80 選 L ×；5：80 < 90 選 R ○
    assert (report.agreed, report.decided) == (2, 4)
    assert report.by_kind == {"現行 vs V2": (1, 1), "現行 vs V3": (1, 2), "不同擺放": (0, 1)}
    assert report.same_gap_median == pytest.approx(1.0)
    assert report.decided_gap_median == pytest.approx((5 + 10) / 2)


def test_leave_one_case_out_fits_without_the_held_out_case() -> None:
    # 「挑參數」：訓練資料有 b 就挑讓每組都一致的評分（good），否則挑每組都不一致的（bad）
    good = {("a", "V00"): 80.0, ("a", "V01"): 80.0, ("a", "V20"): 70.0, ("a", "V30"): 70.0,
            ("b", "V00"): 70.0, ("b", "V01"): 80.0, ("b", "V20"): 70.0, ("b", "V30"): 80.0}  # fmt: skip
    seen: list[set[str]] = []

    def fit(training: Sequence[Pair]):
        cases = {p.case for p in training}
        seen.append(cases)
        sign = 1.0 if "b" in cases else -1.0
        return lambda sample: sign * good[(sample.case, sample.key)]

    report = agreement(SAMPLES, PAIRS, fit)
    assert seen == [{"a", "b"}, {"b"}, {"a"}]  # 先樣本內（全部），再依案例名稱各留一個
    assert report.agreed == 4  # 樣本內看得到 b：全對
    assert report.loco_agreed == 2  # 留 a（用 b 挑）：a 的 2 組對；留 b（只看到 a）：b 的 2 組錯


def test_kind_names() -> None:
    assert kind(Pair(1, "a", "V02", "V00", "L")) == "不同擺放"
    assert kind(Pair(1, "a", "V20", "V00", "L")) == kind(Pair(1, "a", "V00", "V20", "L")) == "現行 vs V2"
    assert kind(Pair(1, "a", "V00", "V31", "L")) == "現行 vs V3"
    assert kind(Pair(1, "a", "V20", "V30", "L")) == "V2 vs V3"
