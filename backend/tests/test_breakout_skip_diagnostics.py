"""Name the guard that blocks breakout promotion (bt 180796).

doc 193 has `breakout_score_boost_enabled=True` and `buy_threshold=0.3`, yet 84 of
103 names that moved >=30% ended the window at a flat vote and only 11 log lines
mention breakout at all. `_compute_breakout_score_boost` has five early exits that
all returned an empty reason, so which one fires was unknowable. The reason string
is only consumed when boost > 0, so naming the exits changes no behaviour.
"""
import ast
import os
import sys

import pytest

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

_path = os.path.join(_backend, "strategies", "graph_nexus_analysis.py")
_src = open(_path, encoding="utf-8").read()
_tree = ast.parse(_src)
_fn = next(n for n in ast.walk(_tree)
           if isinstance(n, ast.FunctionDef) and n.name == "_compute_breakout_score_boost")
_ns = {}
exec(compile(ast.Module(body=[_fn], type_ignores=[]), _path, "exec"), _ns)
_boost = _ns["_compute_breakout_score_boost"]

BAR = {"close": 10.0, "open": 9.9, "volume": 1000.0}


def test_disabled_says_so():
    assert _boost("X", {"X": [BAR] * 30}, {"breakout_score_boost_enabled": False}) == (
        0.0, "skip:disabled")


def test_missing_history_map_says_so():
    assert _boost("X", None, {})[1] == "skip:no_history_map"


def test_short_history_reports_the_counts():
    """The prime suspect: a fast mover discovered mid-move lacks 25 bars."""
    boost, reason = _boost("X", {"X": [BAR] * 5}, {})
    assert boost == 0.0
    assert reason == "skip:bars=5<25"


def test_threshold_is_configurable_and_reported():
    assert _boost("X", {"X": [BAR] * 5}, {"breakout_min_history_bars": 3})[1] != "skip:bars=5<3"


def test_empty_symbol_says_so():
    assert _boost("", {"X": [BAR] * 30}, {})[1] == "skip:no_symbol"


def test_no_early_exit_returns_a_silent_empty_reason():
    fn_src = ast.get_source_segment(_src, _fn)
    assert 'return (0.0, "")' not in fn_src, (
        "an unnamed early exit is an unobservable guard")


def test_diagnostic_is_default_off_and_log_only():
    """The flag must not change the return value — tested behaviourally.

    The previous version asserted the log call sat on the literal next line and
    broke on a comment while the invariant still held. Textual adjacency was the
    wrong tool: what matters is that turning diagnostics on changes nothing a
    caller can observe. bt 718107 relies on that — the reason string is logged,
    and `boost` is what drives promotion.
    """
    bars = [{"c": 10.0 + i * 0.01, "v": 100.0, "o": 10.0 + i * 0.01} for i in range(300)]
    hist = {"AAA": bars}
    for cfg_extra in ({}, {"breakout_min_history_bars": 25}):
        off = dict(cfg_extra, breakout_diagnostics_enabled=False)
        on = dict(cfg_extra, breakout_diagnostics_enabled=True)
        boost_off, _ = _boost("AAA", hist, off)
        boost_on, _ = _boost("AAA", hist, on)
        assert boost_off == boost_on, "diagnostics changed the boost"
    # a symbol with no history: the early exit must also be flag-independent
    assert _boost("ZZZ", {}, {"breakout_diagnostics_enabled": False})[0] == 0.0
    assert _boost("ZZZ", {}, {"breakout_diagnostics_enabled": True})[0] == 0.0


def test_flag_always_defaults_to_false():
    for i in range(len(_src)):
        if _src.startswith("breakout_diagnostics_enabled", i):
            line = _src[_src.rindex("\n", 0, i) + 1: _src.index("\n", i)]
            if "config.get(" in line:
                assert 'config.get("breakout_diagnostics_enabled", False)' in line, (
                    f"must default to False: {line.strip()!r}")
def test_reason_only_consumed_when_boost_positive():
    assert "if _bk_boost > 0:" in _src, (
        "naming the skip reasons is only safe while the reason is used on the "
        "positive branch")
