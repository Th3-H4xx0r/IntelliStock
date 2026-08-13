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
    i = _src.index('breakout_diagnostics_enabled')
    line = _src[_src.rindex("\n", 0, i) + 1: _src.index("\n", i)]
    assert 'config.get("breakout_diagnostics_enabled", False)' in line
    nxt = _src[_src.index("\n", i) + 1: _src.index("\n", _src.index("\n", i) + 1)]
    assert "_log(" in nxt and "fresh_score" not in nxt


def test_reason_only_consumed_when_boost_positive():
    assert "if _bk_boost > 0:" in _src, (
        "naming the skip reasons is only safe while the reason is used on the "
        "positive branch")
