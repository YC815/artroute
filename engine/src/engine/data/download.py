"""下載 Geofabrik pbf，邊下載邊算 md5；驗證通過才取代舊檔。"""

import hashlib
import os
from pathlib import Path

import httpx

GEOFABRIK_TAIWAN = "https://download.geofabrik.de/asia/taiwan-latest.osm.pbf"


class DownloadError(Exception):
    pass


def download_with_md5(url: str, dest: Path, client: httpx.Client) -> str:
    md5_response = client.get(url + ".md5")
    md5_response.raise_for_status()
    expected = md5_response.text.split()[0]

    part = dest.with_name(dest.name + ".part")
    digest = hashlib.md5()
    try:
        with client.stream("GET", url) as response:
            response.raise_for_status()
            with part.open("wb") as file:
                for chunk in response.iter_bytes(1 << 20):
                    file.write(chunk)
                    digest.update(chunk)
        if digest.hexdigest() != expected:
            raise DownloadError(f"md5 不符：預期 {expected}，實際 {digest.hexdigest()}")
        os.replace(part, dest)
    finally:
        part.unlink(missing_ok=True)
    return expected
