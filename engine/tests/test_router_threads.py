"""valhalla.Actor 不是 thread-safe：binding 會釋放 GIL，多個 thread 同時打同一個 Actor 會 segfault。

在子行程裡跑，這樣就算崩潰也只是 returncode 非 0，不會把 pytest 本身帶走。
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from osm_fixture import grid_point

pytestmark = pytest.mark.valhalla

THREADS = 8
CALLS_PER_THREAD = 50

HAMMER_SCRIPT = """
import json, sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from engine.routing.router import Router

config_path, start, end, threads, calls = sys.argv[1:6]
start, end = json.loads(start), json.loads(end)
router = Router(Path(config_path))

def job(_):
    return round(router.route(start, end).length_km, 3)

with ThreadPoolExecutor(int(threads)) as pool:
    lengths = set(pool.map(job, range(int(threads) * int(calls))))
print(json.dumps(sorted(lengths)))
"""


def test_shared_router_survives_concurrent_calls(grid_config: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            HAMMER_SCRIPT,
            str(grid_config),
            json.dumps(grid_point(0, 3)),
            json.dumps(grid_point(6, 3)),
            str(THREADS),
            str(CALLS_PER_THREAD),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, f"子行程異常結束（{result.returncode}）：{result.stderr[-1000:]}"
    # 所有 thread 算出的長度必須一致（繞開快速道路的同一條路線）
    assert len(json.loads(result.stdout)) == 1, result.stdout
