import subprocess
import sys

from valhalla import Actor, get_config


def test_python_api_importable() -> None:
    assert callable(Actor)
    assert callable(get_config)


def test_build_tiles_executable_bundled() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "valhalla", "--help"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert "valhalla_build_tiles" in result.stdout + result.stderr
