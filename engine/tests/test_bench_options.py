"""兩個 bench 共用的吸附參數旗標：--turn-penalty-factor（數字或 none）與 --sigma-z。"""

import argparse

import pytest

from engine.bench.options import (
    add_match_options,
    add_router_options,
    describe_match_options,
    describe_router_options,
    match_options,
    router_options,
)
from engine.routing.router import FAST_TURN_PENALTY_FACTOR


def parse(*argv: str) -> dict:
    parser = argparse.ArgumentParser()
    add_router_options(parser)
    return router_options(parser.parse_args(list(argv)))


def test_defaults_are_the_router_defaults() -> None:
    assert parse() == {"turn_penalty_factor": FAST_TURN_PENALTY_FACTOR, "sigma_z": None}


@pytest.mark.parametrize("raw", ["none", "None", " NONE "])
def test_none_means_do_not_send_turn_penalty_factor(raw: str) -> None:
    assert parse("--turn-penalty-factor", raw)["turn_penalty_factor"] is None


def test_numbers() -> None:
    assert parse("--turn-penalty-factor", "0", "--sigma-z", "8.5") == {"turn_penalty_factor": 0.0, "sigma_z": 8.5}
    assert parse("--turn-penalty-factor", "140")["turn_penalty_factor"] == 140.0


@pytest.mark.parametrize(
    "argv",
    [
        ["--turn-penalty-factor", "abc"],
        ["--turn-penalty-factor", "-1"],
        ["--turn-penalty-factor", "nan"],
        ["--turn-penalty-factor", "inf"],
        ["--sigma-z", "none"],
        ["--sigma-z", "0"],
        ["--sigma-z", "-3"],
        ["--sigma-z", "inf"],
    ],
)
def test_bad_values_are_argparse_errors(argv: list[str], capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as usage:
        parse(*argv)
    assert usage.value.code == 2
    assert argv[0] in capsys.readouterr().err


def test_description_names_the_values_actually_used() -> None:
    assert describe_router_options({"turn_penalty_factor": 500.0, "sigma_z": None}) == (
        "吸附參數：turn_penalty_factor 500、sigma_z 設定檔預設"
    )
    assert describe_router_options({"turn_penalty_factor": None, "sigma_z": 8.5}) == (
        "吸附參數：turn_penalty_factor none（不帶，由 meili 設定決定）、sigma_z 8.5"
    )


def test_no_prune_flag() -> None:
    parser = argparse.ArgumentParser()
    add_match_options(parser)
    assert match_options(parser.parse_args([])) == {}
    assert match_options(parser.parse_args(["--no-prune"])) == {"prune": False}
    assert "關" in describe_match_options({"prune": False}) and "開" in describe_match_options({})


def test_quality_flag_only_when_asked() -> None:
    parser = argparse.ArgumentParser()
    add_match_options(parser, quality=True)
    assert match_options(parser.parse_args([])) == {}
    assert match_options(parser.parse_args(["--quality", "fine", "--no-prune"])) == {"prune": False, "quality": "fine"}
    assert "quality fine" in describe_match_options({"quality": "fine"})
    with pytest.raises(SystemExit):
        parser.parse_args(["--quality", "best"])
