"""Distinguish cadence from latency as the reason movers are not promoted.

bt 778288: movers come within a median 2.6% of the promotion test, 18 of 45 within
2%, several at the -1.0% boundary — and are evaluated on a median 43% of days.
So a mover may be missing its qualifying moment rather than never having one.
`_compute_breakout_score_boost` cannot tell, because it only sees a symbol when
it is called. This audit walks the cache every tick regardless.
"""
import ast
import os
import sys

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

_path = os.path.join(_backend, "strategies", "graph_nexus_analysis.py")
_src = open(_path, encoding="utf-8").read()
_tree = ast.parse(_src)

LOGS = []
_ns = {"_log": lambda msg, *a, **k: LOGS.append(msg),
       "_visible_overlay_bars": lambda bars, cache, date_key, config: list(bars)}
for _n in _tree.body:
    if isinstance(_n, ast.FunctionDef) and _n.name == "_breakout_opportunity_audit":
        exec(compile(ast.Module(body=[_n], type_ignores=[]), _path, "exec"), _ns)
audit = _ns["_breakout_opportunity_audit"]

ON = {"breakout_opportunity_audit_enabled": True}


def _bars(n, last):
    return [{"c": 10.0} for _ in range(n - 1)] + [{"c": last}]


def setup_function(_):
    LOGS.clear()


def test_default_off_emits_nothing():
    audit({}, {"_overlay_bars_raw": {"A": _bars(30, 10.0)}}, "d", {})
    assert LOGS == []


def test_reports_a_symbol_at_a_25_bar_high():
    audit({}, {"_overlay_bars_raw": {"AAOI": _bars(30, 10.0)}}, "2026-01-05", ON)
    assert any("AAOI" in m and "1 qualify" in m for m in LOGS)


def test_reports_none_explicitly_rather_than_staying_silent():
    """Silence would be indistinguishable from the audit not running."""
    audit({}, {"_overlay_bars_raw": {"A": _bars(30, 5.0)}}, "2026-01-05", ON)
    assert any("none of 1 cached symbols qualify" in m for m in LOGS)


def test_flags_symbols_absent_from_the_history_map():
    """A qualifying symbol the scorer could not see is the cadence signal."""
    audit({}, {"_overlay_bars_raw": {"AAOI": _bars(30, 10.0)}}, "d", ON)
    assert any("nomap" in m for m in LOGS)
    LOGS.clear()
    audit({"AAOI": 1}, {"_overlay_bars_raw": {"AAOI": _bars(30, 10.0)}}, "d", ON)
    assert not any("nomap" in m for m in LOGS)


def test_requires_25_bars_like_the_scorer_does():
    audit({}, {"_overlay_bars_raw": {"A": _bars(20, 10.0)}}, "d", ON)
    assert any("none of 1" in m for m in LOGS)


def test_uses_the_same_1pct_band_as_the_promotion_test():
    """0.99 * high: 9.95 on a high of 10 qualifies, 9.80 does not."""
    audit({}, {"_overlay_bars_raw": {"A": _bars(30, 9.95)}}, "d", ON)
    assert any("1 qualify" in m for m in LOGS)
    LOGS.clear()
    audit({}, {"_overlay_bars_raw": {"A": _bars(30, 9.80)}}, "d", ON)
    assert any("none of" in m for m in LOGS)


def test_malformed_cache_is_survivable():
    audit({}, {"_overlay_bars_raw": "nonsense"}, "d", ON)
    audit({}, None, "d", ON)
    assert not any("ERROR" in m for m in LOGS)
