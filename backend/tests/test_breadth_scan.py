"""Breadth momentum scanner core logic (2026-07-24).

_breadth_scan_movers screens a market-wide universe and admits FRESH momentum
movers to an evaluation set. Verifies: default-off; admits a genuine early mover;
rejects a glitched (split-unadjusted) series; rejects an already-parabolic name;
drops the whole pool in bear/crash (so the bypassing reserved-buy lane can't
knife-catch in a downtrend).

gna.py isn't import-safe under pytest -> AST-extract the function with stubs for
its module deps and a fake ticker_universe, and hand it synthetic closes.
"""
import ast
import os
import sys

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

_ns = {
    "_log": lambda *a, **k: None,
    "get_breadth_universe": lambda **k: list(_UNIVERSE),
    # bars are pre-seeded into strategy_cache["_overlay_bars_raw"] as close-lists;
    # the loader is a no-op and _point_in_time_closes just returns them.
    "_ensure_overlay_bars_cached": lambda *a, **k: None,
    "_point_in_time_closes": lambda bars, ds: list(bars) if isinstance(bars, list) else [],
}
_src = open(os.path.join(_backend, "strategies", "graph_nexus_analysis.py")).read()
for _node in ast.parse(_src).body:
    if isinstance(_node, ast.FunctionDef) and _node.name == "_breadth_scan_movers":
        # The extracted function imports this dependency inside its try block.
        # Remove only that import and inject the test-local callable above;
        # mutating sys.modules at collection time contaminates unrelated tests.
        for _inner in ast.walk(_node):
            if isinstance(_inner, ast.Try):
                _inner.body = [
                    _statement
                    for _statement in _inner.body
                    if not (
                        isinstance(_statement, ast.ImportFrom)
                        and _statement.module == "ticker_universe"
                    )
                ]
        exec(compile(ast.Module(body=[_node], type_ignores=[]), "gna.py", "exec"), _ns)
breadth = _ns["_breadth_scan_movers"]

_UNIVERSE = ["MOVER", "GLITCH", "PARA", "FLAT"]
# 21+ closes each. MOVER: +15% over 20d, clean. GLITCH: a 3x split jump.
# PARA: +120% over 20d (blown off). FLAT: no move.
_SERIES = {
    "MOVER": [100.0] * 6 + [100, 101, 102, 103, 104, 105, 106, 108, 110, 112, 113, 114, 115, 115.1, 115.2],
    "GLITCH": [30.0] * 10 + [30, 30, 90, 90, 91, 92, 93, 94, 95, 96, 97],   # 30->90 split-signature jump
    "PARA": [50.0] * 6 + [50, 55, 60, 66, 72, 80, 88, 96, 104, 110, 112, 113, 114, 115, 116],  # ~+130%
    "FLAT": [100.0] * 21,
}
CFG_ON = {"breadth_scan_enabled": True, "breadth_scan_r20_min_pct": 12.0,
          "breadth_scan_r5_min_pct": 7.0, "breadth_scan_admit_per_bar": 5,
          "breadth_scan_batch_per_bar": 10}


def _cache(regime="bull"):
    return {"_market_regime": regime,
            "_overlay_bars_raw": {k: list(v) for k, v in _SERIES.items()}}


def test_default_off_admits_nothing():
    sc = _cache()
    assert breadth({}, sc, "2026-04-02", "k", "s") == []
    assert not sc.get("_breadth_scan_admitted")


def test_admits_clean_early_mover():
    sc = _cache()
    out = breadth(CFG_ON, sc, "2026-04-02", "k", "s")
    assert "MOVER" in out, out


def test_rejects_glitch_split_series():
    sc = _cache()
    out = breadth(CFG_ON, sc, "2026-04-02", "k", "s")
    assert "GLITCH" not in out, "split-signature series must be glitch-filtered"


def test_rejects_parabolic_blownoff():
    sc = _cache()
    out = breadth(CFG_ON, sc, "2026-04-02", "k", "s")
    assert "PARA" not in out, "already-parabolic name must be capped out"


def test_rejects_flat():
    sc = _cache()
    assert "FLAT" not in breadth(CFG_ON, sc, "2026-04-02", "k", "s")


def test_bear_drops_pool():
    sc = _cache("bull")
    breadth(CFG_ON, sc, "2026-04-02", "k", "s")
    assert sc["_breadth_scan_admitted"], "pool built in bull"
    sc["_market_regime"] = "bear"
    out = breadth(CFG_ON, sc, "2026-04-06", "k", "s")
    assert out == [] and not sc["_breadth_scan_admitted"], "bear must clear the pool"


def test_off_regime_holds_but_no_growth():
    # chop is in the default regimes -> grows; a regime NOT in the list (e.g. crash)
    # with the bear-block off would hold (not clear). Here verify crash clears.
    sc = _cache("crash")
    assert breadth(CFG_ON, sc, "2026-04-02", "k", "s") == []


def test_bear_EVICTS_from_watchlist_not_just_pool():
    """The critical bug-sweep fix: admitted names get merged into the never-evicting
    _momentum_watchlist, from which two buy lanes (reserved + momentum_breakout_add)
    can buy in bear bypassing the RS gate. Bear/crash must EVICT them, not just
    clear the pool."""
    sc = _cache("bull")
    sc["_momentum_watchlist"] = {}
    breadth(CFG_ON, sc, "2026-04-02", "k", "s")
    # simulate _build_momentum_watchlist merging the admitted names
    for s in list(sc["_breadth_scan_admitted"]):
        sc["_momentum_watchlist"][s] = {"first_seen_bar": 1}
    assert sc["_momentum_watchlist"], "precondition: admitted names merged into watchlist"
    sc["_market_regime"] = "bear"
    breadth(CFG_ON, sc, "2026-04-06", "k", "s")
    assert not sc["_momentum_watchlist"], "bear must EVICT breadth names from the watchlist"
    assert not sc["_breadth_scan_admitted"]


def test_disable_rolls_back_watchlist_and_pool():
    """Kill-switch: disabling must evict + clear (not keep feeding the stale pool)."""
    sc = _cache("bull")
    breadth(CFG_ON, sc, "2026-04-02", "k", "s")
    for s in list(sc["_breadth_scan_admitted"]):
        sc.setdefault("_momentum_watchlist", {})[s] = {"first_seen_bar": 1}
    assert breadth({}, sc, "2026-04-06", "k", "s") == []   # disabled
    assert not sc["_breadth_scan_admitted"]
    assert not sc.get("_momentum_watchlist")


def test_wide_glitch_scan_catches_r60_window_split():
    """A split-signature jump in the r60 window (bars -60..-22) — clean r20 — must
    still be glitch-filtered (the r60-hole fix)."""
    # 62 closes: a 3x jump ~40 bars back, then clean recent action (clean r20).
    series = [30.0] * 20 + [90.0] * 42
    sc = {"_market_regime": "bull", "_overlay_bars_raw": {"SPLIT60": list(series)}}
    global _UNIVERSE
    _save = _UNIVERSE
    try:
        _UNIVERSE = ["SPLIT60"]
        out = breadth({**CFG_ON, "breadth_scan_r20_min_pct": 0.0, "breadth_scan_r5_min_pct": 0.0},
                      sc, "2026-04-02", "k", "s")
        assert "SPLIT60" not in out, "split in the r60 window must be caught"
    finally:
        _UNIVERSE = _save
