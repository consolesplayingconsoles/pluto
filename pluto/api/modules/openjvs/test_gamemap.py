#!/usr/bin/env python3
"""Tests for gamemap.py, from the stock-style maps in nodes/local/pi/openjvs/games.

    python3 test_gamemap.py     # plain asserts, no pytest
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gamemap  # noqa: E402

FILES = {
    "generic": "# Generic\nCONTROLLER_BUTTON_START CONTROLLER_1 BUTTON_START PLAYER_1\n"
               "CONTROLLER_BUTTON_TEST CONTROLLER_1 BUTTON_TEST SYSTEM\n",
    "generic-shooting": "INCLUDE generic\n\nCONTROLLER_ANALOGUE_X CONTROLLER_1 ANALOGUE_1\n",
    "hotd2": "INCLUDE generic-shooting\nCONTROLLER_BUTTON_D CONTROLLER_1 COIN PLAYER_1  # coin\n",
    "loop-a": "INCLUDE loop-b\n",
    "loop-b": "INCLUDE loop-a\n",
}


def read(name):
    return FILES[name]


def test_default_game():
    assert gamemap.default_game("# x\nDEFAULT_GAME virtua-tennis\nEMULATE sega-type-1\n") == "virtua-tennis"
    assert gamemap.default_game("EMULATE sega-type-1\n") == ""


def test_includes_land_in_place():
    rows = gamemap.resolve("hotd2", read)
    assert [(r["input"], r["output"], r["player"], r["file"]) for r in rows] == [
        ("CONTROLLER_BUTTON_START", "BUTTON_START", "PLAYER_1", "generic"),
        ("CONTROLLER_BUTTON_TEST", "BUTTON_TEST", "SYSTEM", "generic"),
        ("CONTROLLER_ANALOGUE_X", "ANALOGUE_1", "", "generic-shooting"),
        ("CONTROLLER_BUTTON_D", "COIN", "PLAYER_1", "hotd2"),
    ]


def test_loop_and_missing_include_are_rows_not_errors():
    assert "loop" in gamemap.resolve("loop-a", read)[0]["output"]
    FILES["broken"] = "INCLUDE nowhere\n"
    assert "unreadable" in gamemap.resolve("broken", read)[0]["output"]


def test_repo_virtua_tennis_map():
    games = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "..",
                         "nodes", "local", "pi", "openjvs", "games")

    def disk(name):
        with open(os.path.join(games, name)) as f:
            return f.read()
    rows = gamemap.resolve("virtua-tennis", disk)
    assert ("CONTROLLER_BUTTON_D", "COIN") in [(r["input"], r["output"]) for r in rows]


if __name__ == "__main__":
    n = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            n += 1
    print("%d tests passed" % n)
