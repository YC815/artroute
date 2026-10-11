"""整條路線一個簽章（route editing spec §3.4）：/edit 只接受引擎輸出過、未經修改的路線，
客戶端無法改點、重排、刪掉或重複段落來造出沒檢查過的接點。"""

import json
import threading
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pytest

from engine.edit.signature import KEY_BYTES, load_or_create_key, sign_route, verify_route
from engine.matching.types import Segment

KEY = bytes(range(KEY_BYTES))
VERSION = "20261011T095232Z"
SEGMENTS = [
    Segment("stroke", 0, [(121.5, 25.05), (121.501, 25.05), (121.502, 25.0505)], 160.123),
    Segment("detour", 0, [(121.502, 25.0505), (121.503, 25.051)], 113.4),
    Segment("connector", None, [(121.503, 25.051), (121.503, 25.052)], 111.2),
    Segment("edit", 1, [(121.503, 25.052), (121.504, 25.052)], 100.9),
]


def test_sign_then_verify_round_trips() -> None:
    sig = sign_route(KEY, VERSION, SEGMENTS)
    assert len(sig) == 64
    assert verify_route(KEY, VERSION, SEGMENTS, sig)


def _moved_point(segments: list[Segment]) -> list[Segment]:
    first = segments[0]
    (lon, lat), *rest = first.points
    return [replace(first, points=[(lon + 1e-6, lat), *rest]), *segments[1:]]


def _with(i: int, **changes) -> Callable[[list[Segment]], list[Segment]]:
    return lambda segments: [replace(s, **changes) if k == i else s for k, s in enumerate(segments)]


MUTATIONS = {
    "move a point 1e-6 deg": _moved_point,
    "change kind": _with(1, kind="stroke"),
    "change stroke_index": _with(0, stroke_index=1),
    "null stroke_index to 0": _with(2, stroke_index=0),
    "change length_m by 1 m": _with(0, length_m=SEGMENTS[0].length_m + 1),
    "swap two segments": lambda s: [s[1], s[0], *s[2:]],
    "drop a segment": lambda s: s[:-1],
    "duplicate a segment": lambda s: [*s, s[-1]],
}


@pytest.mark.parametrize("mutate", MUTATIONS.values(), ids=MUTATIONS.keys())
def test_any_change_breaks_the_signature(mutate) -> None:
    """拿掉 verify_route 的 hmac.compare_digest（改成恆回 True）會紅。"""
    sig = sign_route(KEY, VERSION, SEGMENTS)
    assert not verify_route(KEY, VERSION, mutate(SEGMENTS), sig)


def test_another_data_version_or_key_breaks_the_signature() -> None:
    """拿掉 verify_route 的 hmac.compare_digest（改成恆回 True）會紅；_encode 不編 data_version 也會紅。"""
    sig = sign_route(KEY, VERSION, SEGMENTS)
    assert not verify_route(KEY, "20261012T000000Z", SEGMENTS, sig)
    assert not verify_route(bytes(KEY_BYTES), VERSION, SEGMENTS, sig)


def test_json_formatting_does_not_matter() -> None:
    sig = sign_route(KEY, VERSION, SEGMENTS)
    text = json.dumps([{"kind": s.kind, "stroke_index": s.stroke_index, "points": s.points, "length_m": s.length_m}
                       for s in SEGMENTS], indent=2)
    assert "121.5," in text
    loaded = json.loads(text.replace("121.5,", "1.215e2,"))
    segments = [Segment(d["kind"], d["stroke_index"], [tuple(p) for p in d["points"]], d["length_m"]) for d in loaded]
    assert verify_route(KEY, VERSION, segments, sig)


@pytest.mark.parametrize("sig", ["", "0" * 63, "0" * 65, "g" * 64, "é" * 64, " " + "0" * 63])
def test_rejects_malformed_signature(sig: str) -> None:
    """拿掉 verify_route 開頭的格式檢查會紅：非 ASCII 的字串讓 hmac.compare_digest 丟 TypeError，而不是回 False。"""
    assert verify_route(KEY, VERSION, SEGMENTS, sig) is False


def test_unencodable_route_does_not_verify() -> None:
    """請求驗證之外的值（NaN、超出範圍的 stroke_index）不能讓驗簽丟例外：拿掉 verify_route 的 except 會紅。"""
    sig = sign_route(KEY, VERSION, SEGMENTS)
    nan = [replace(SEGMENTS[0], points=[(float("nan"), 25.05), (121.501, 25.05)]), *SEGMENTS[1:]]
    huge = [replace(SEGMENTS[0], stroke_index=2**40), *SEGMENTS[1:]]
    assert verify_route(KEY, VERSION, nan, sig) is False
    assert verify_route(KEY, VERSION, huge, sig) is False


def test_key_is_created_once_with_mode_600(tmp_path: Path) -> None:
    path = tmp_path / "edit-key"
    key = load_or_create_key(path)
    assert len(key) == KEY_BYTES
    assert load_or_create_key(path) == key
    assert path.stat().st_mode & 0o777 == 0o600
    assert list(tmp_path.iterdir()) == [path]  # 沒有殘留的暫存檔


def test_concurrent_creation_yields_one_key(tmp_path: Path) -> None:
    path = tmp_path / "edit-key"
    barrier = threading.Barrier(8)
    keys: list[bytes] = []
    errors: list[BaseException] = []

    def worker() -> None:
        barrier.wait()
        try:
            keys.append(load_or_create_key(path))
        except BaseException as error:  # thread 裡的例外不會讓測試失敗：收起來在主 thread 斷言
            errors.append(error)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    assert len(keys) == 8 and len(set(keys)) == 1
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize("content", [b"", b"x" * (KEY_BYTES - 1), b"x" * (KEY_BYTES + 1)])
def test_wrong_length_key_file_is_an_error(tmp_path: Path, content: bytes) -> None:
    path = tmp_path / "edit-key"
    path.write_bytes(content)
    with pytest.raises(ValueError, match="長度"):
        load_or_create_key(path)
    assert path.read_bytes() == content  # 不覆寫：換掉金鑰等於讓所有草稿失效
