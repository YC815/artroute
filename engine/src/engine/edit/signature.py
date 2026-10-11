"""整條路線一個簽章（route editing spec §3.4）：/edit 只動引擎輸出過、未經修改的路線。

每段各自簽章擋不住重排、刪掉或重複段落（兩段已簽章的路首尾相接就造出沒檢查過的接點），所以簽整條路線。
編碼不經過 JSON：點量化成 1e-7 度、長度量化成 mm 的整數，加長度前綴，同一條路線不管 JSON 怎麼寫都得到同一串 bytes。
"""

import hashlib
import hmac
import os
import re
import secrets
import struct
from collections.abc import Sequence
from pathlib import Path

from engine.matching.types import Segment, SegmentKind

KEY_BYTES = 32

_DOMAIN = b"artroute-route-v1\0"
_KIND_CODES: dict[SegmentKind, int] = {"stroke": 0, "detour": 1, "connector": 2, "edit": 3}
_SIG_RE = re.compile(r"[0-9a-f]{64}")


def _read_key(path: Path) -> bytes:
    key = path.read_bytes()
    if len(key) != KEY_BYTES:
        raise ValueError(f"編輯金鑰 {path} 長度 {len(key)} bytes，應為 {KEY_BYTES}")
    return key


def load_or_create_key(path: Path) -> bytes:
    """讀 path 的金鑰；不存在就建立（權限 600）。

    多個 worker 可能同時啟動：只用 O_EXCL 直接建 path 的話，別的 worker 可能在內容寫完前讀到空檔。
    所以先把完整的金鑰寫進同目錄的暫存檔，再 os.link 到 path（目標已存在就失敗、改讀別人建好的），讀到的一定是完整的。
    長度不對丟 ValueError、不覆寫：換掉金鑰等於讓所有草稿的簽章失效。
    """
    try:
        return _read_key(path)
    except FileNotFoundError:
        pass
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as file:
            file.write(os.urandom(KEY_BYTES))
            file.flush()
            os.fsync(file.fileno())
        try:
            os.link(tmp, path)
        except FileExistsError:
            pass
        else:
            # 目錄項目也要落盤：當機後金鑰檔消失，下次啟動會建新的一把，所有草稿都驗不過
            dir_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
    finally:
        tmp.unlink(missing_ok=True)
    return _read_key(path)


def _encode(data_version: str, segments: Sequence[Segment]) -> bytes:
    version = data_version.encode()
    parts = [_DOMAIN, struct.pack(">I", len(version)), version, struct.pack(">I", len(segments))]
    for segment in segments:
        stroke_index = -1 if segment.stroke_index is None else segment.stroke_index
        parts.append(struct.pack(">Biq", _KIND_CODES[segment.kind], stroke_index, round(segment.length_m * 1000)))
        parts.append(struct.pack(">I", len(segment.points)))
        parts.extend(struct.pack(">qq", round(lon * 1e7), round(lat * 1e7)) for lon, lat in segment.points)
    return b"".join(parts)


def sign_route(key: bytes, data_version: str, segments: Sequence[Segment]) -> str:
    """路網版本＋依序的每段（種類、筆畫序號、長度、點）的 HMAC-SHA256（hex）。"""
    return hmac.new(key, _encode(data_version, segments), hashlib.sha256).hexdigest()


def verify_route(key: bytes, data_version: str, segments: Sequence[Segment], sig: str) -> bool:
    # compare_digest 遇到非 ASCII 的字串會丟 TypeError；格式不對的簽章不可能對，直接回 False
    if not _SIG_RE.fullmatch(sig):
        return False
    try:
        expected = sign_route(key, data_version, segments)
    except (ValueError, OverflowError, KeyError, struct.error):  # NaN、超出編碼範圍、不認得的 kind：不可能是簽過的路線
        return False
    return hmac.compare_digest(expected, sig)
