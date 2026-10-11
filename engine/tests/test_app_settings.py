"""部署設定：吸附參數從環境變數讀進來，經 router_factory 交給 /match 與搜尋的 Router；壞值要明確報錯。"""

import functools
import os
import subprocess
import sys
from pathlib import Path

import pytest

from engine.app import meili_sigma_z, router_factory_from_env, trace_turn_penalty_factor
from engine.routing.router import FAST_TURN_PENALTY_FACTOR, Router


def test_turn_penalty_factor_defaults_to_fast_value() -> None:
    assert trace_turn_penalty_factor(None) == FAST_TURN_PENALTY_FACTOR


@pytest.mark.parametrize("raw", ["none", "None", " NONE "])
def test_turn_penalty_factor_none_means_do_not_send(raw: str) -> None:
    assert trace_turn_penalty_factor(raw) is None


@pytest.mark.parametrize(("raw", "expected"), [("0", 0.0), ("140", 140.0), (" 500.5 ", 500.5)])
def test_turn_penalty_factor_accepts_non_negative_numbers(raw: str, expected: float) -> None:
    assert trace_turn_penalty_factor(raw) == expected


@pytest.mark.parametrize("raw", ["", "abc", "nan", "inf", "-1", "500m"])
def test_turn_penalty_factor_rejects_bad_values(raw: str) -> None:
    with pytest.raises(ValueError, match="TRACE_TURN_PENALTY_FACTOR"):
        trace_turn_penalty_factor(raw)


def test_sigma_z_unset_keeps_valhalla_default() -> None:
    assert meili_sigma_z(None) is None


@pytest.mark.parametrize(("raw", "expected"), [("4.07", 4.07), (" 15 ", 15.0)])
def test_sigma_z_accepts_positive_numbers(raw: str, expected: float) -> None:
    assert meili_sigma_z(raw) == expected


@pytest.mark.parametrize("raw", ["", "none", "abc", "nan", "inf", "0", "-3"])
def test_sigma_z_rejects_bad_values(raw: str) -> None:
    with pytest.raises(ValueError, match="MEILI_SIGMA_Z"):
        meili_sigma_z(raw)


def _keywords(factory) -> dict:
    assert isinstance(factory, functools.partial) and factory.func == Router.from_build
    return factory.keywords


def test_router_factory_without_env_uses_defaults() -> None:
    assert _keywords(router_factory_from_env({})) == {"sigma_z": None, "turn_penalty_factor": FAST_TURN_PENALTY_FACTOR}


def test_router_factory_passes_env_values() -> None:
    env = {"TRACE_TURN_PENALTY_FACTOR": "none", "MEILI_SIGMA_Z": "15"}
    assert _keywords(router_factory_from_env(env)) == {"sigma_z": 15.0, "turn_penalty_factor": None}


def test_router_factory_rejects_bad_env() -> None:
    with pytest.raises(ValueError, match="MEILI_SIGMA_Z"):
        router_factory_from_env({"MEILI_SIGMA_Z": "fifteen"})


def test_production_import_fails_loudly_on_bad_env(tmp_path: Path) -> None:
    # 模組底部建 app 時就讀環境變數：設錯要讓服務起不來，不能悄悄用預設值
    env = {**os.environ, "DATA_DIR": str(tmp_path), "TRACE_TURN_PENALTY_FACTOR": "lots"}
    done = subprocess.run([sys.executable, "-c", "import engine.app"], capture_output=True, text=True, timeout=60, env=env)
    assert done.returncode != 0
    assert "TRACE_TURN_PENALTY_FACTOR" in done.stderr
