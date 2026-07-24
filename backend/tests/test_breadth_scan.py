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
import types

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

# Fake ticker_universe so the function's `from ticker_universe import ...` resolves.
_fake_tu = types.ModuleType("ticker_universe")
_fake_tu.get_breadth_universe = lambda **k: list(_UNIVERSE)
sys.modules["ticker_universe"] = _fake_tu

_ns = {
    "_log": lambda *a, **k: None,
    # bars are pre-seeded into strategy_cache["_overlay_bars_raw"] as close-lists;
    # the loader is a no-op and _point_in_time_closes just returns them.
    "_ensure_overlay_bars_cached": lambda *a, **k: None,
    "_point_in_time_closes": lambda bars, ds: list(bars) if isinstance(bars, list) else [],
}
_src = open(os.path.join(_backend, "strategies", "graph_nexus_analysis.py")).read()
for _node in ast.parse(_src).body:
    if isinstance(_node, ast.FunctionDef) and _node.name == "_breadth_scan_movers":
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
