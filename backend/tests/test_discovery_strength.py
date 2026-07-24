"""Discovery-strengthening fixes (2026-07-24).

Fix B: _overlay_bars_compute_range must extend the prefetch start to cover
`overlay_bars_min_history_bars` trading bars when set (so 60-day momentum returns
aren't zeroed) — default 0 preserves the old ~55-bar behavior byte-identically.

gna.py isn't import-safe under pytest, so we AST-extract the pure function with
datetime/timedelta supplied — same pattern as test_recovery_capture.py.
"""
import ast
import os
import sys
from datetime import datetime, timedelta

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

_src = open(os.path.join(_backend, "strategies", "graph_nexus_analysis.py")).read()
_tree = ast.parse(_src)
_ns = {"datetime": datetime, "timedelta": timedelta}
for _node in _tree.body:
    if isinstance(_node, ast.FunctionDef) and _node.name == "_overlay_bars_compute_range":
        exec(compile(ast.Module(body=[_node], type_ignores=[]), "gna.py", "exec"), _ns)
compute_range = _ns["_overlay_bars_compute_range"]

_DATE = "2026-04-02"


def _days_back(cfg):
    start, _end = compute_range(cfg, _DATE)
    return (datetime.strptime(_DATE, "%Y-%m-%d") - datetime.strptime(start, "%Y-%m-%d")).days


def test_default_is_short_range_unchanged():
    """No key -> old ~78-calendar-day (~55 trading bar) start (byte-identical)."""
    d = _days_back({})
    assert 70 <= d <= 90, d  # overlay_lookback(30)+18+30 = 78


def test_min_history_bars_extends_start():
    """overlay_bars_min_history_bars=252 -> start reaches ~1.5*252+30 = ~408 days."""
    d = _days_back({"overlay_bars_min_history_bars": 252})
    assert d >= 252 * 1.5, d           # covers 252 trading bars
    assert d >= 380, d


def test_min_history_bars_never_shrinks_range():
    """A tiny value must not pull the start IN from the default (only ever extend)."""
    assert _days_back({"overlay_bars_min_history_bars": 5}) == _days_back({})


def test_min_history_bars_zero_is_identity():
    assert _days_back({"overlay_bars_min_history_bars": 0}) == _days_back({})
