import hashlib
from pathlib import Path

import httpx
import pytest

from engine.data.download import DownloadError, download_with_md5

URL = "https://example.test/taiwan-latest.osm.pbf"
PAYLOAD = b"fake pbf bytes" * 1000


def client_serving(md5_text: str) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(".md5"):
            return httpx.Response(200, text=md5_text)
        return httpx.Response(200, content=PAYLOAD)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_download_verifies_md5(tmp_path: Path) -> None:
    md5 = hashlib.md5(PAYLOAD).hexdigest()
    dest = tmp_path / "taiwan.osm.pbf"
    assert download_with_md5(URL, dest, client_serving(f"{md5}  taiwan-latest.osm.pbf\n")) == md5
    assert dest.read_bytes() == PAYLOAD
    assert not dest.with_name(dest.name + ".part").exists()


def test_md5_mismatch_keeps_old_file(tmp_path: Path) -> None:
    dest = tmp_path / "taiwan.osm.pbf"
    dest.write_bytes(b"old")
    with pytest.raises(DownloadError):
        download_with_md5(URL, dest, client_serving("0" * 32 + "  taiwan-latest.osm.pbf\n"))
    assert dest.read_bytes() == b"old"
    assert not dest.with_name(dest.name + ".part").exists()
