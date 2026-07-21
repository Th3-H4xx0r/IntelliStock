"""Bull-alpha candidate #3 (2026-07-21): bull-only CONVERT loss threshold for
the V28.8.1 loser-displacement gate. Lets a starved high-conviction queue name
(e.g. INTC +97% in bt 148462) displace a shallow held loser in CONFIRMED bull,
while bear/chop keep the base threshold so the bear window is untouched.
"""
import os
import sys

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

import strategies.graph_nexus_analysis as g


def test_default_is_noop_all_regimes():
    # No config keys -> base default -2.0 in every regime (byte-identical).
    for regime in ("bull", "bear", "chop", "crash", "", None):
        assert g._convert_min_loss_threshold({}, regime) == -2.0


def test_bull_override_applies_only_in_bull():
    cfg = {"v32_convert_min_loss_pct": -2.0, "v32_convert_min_loss_pct_bull": -1.0}
    assert g._convert_min_loss_threshold(cfg, "bull") == -1.0, "bull uses the bull override"
    for regime in ("bear", "chop", "crash", "unknown", None):
        assert g._convert_min_loss_threshold(cfg, regime) == -2.0, \
            f"{regime!r} must keep the base threshold (bear-safety)"


def test_base_override_respected_but_not_widened_in_bear():
    # An operator widening the BASE affects all regimes (existing behavior);
    # the bull key is independent and does not leak into bear.
    cfg = {"v32_convert_min_loss_pct": -4.0}
    assert g._convert_min_loss_threshold(cfg, "bear") == -4.0
    assert g._convert_min_loss_threshold(cfg, "bull") == -4.0  # bull falls back to base when no bull key
    cfg2 = {"v32_convert_min_loss_pct": -4.0, "v32_convert_min_loss_pct_bull": -1.0}
    assert g._convert_min_loss_threshold(cfg2, "bear") == -4.0
    assert g._convert_min_loss_threshold(cfg2, "bull") == -1.0


def test_malformed_values_fall_back():
    assert g._convert_min_loss_threshold({"v32_convert_min_loss_pct": "oops"}, "bear") == -2.0
    assert g._convert_min_loss_threshold(
        {"v32_convert_min_loss_pct": -2.0, "v32_convert_min_loss_pct_bull": "nan-ish"}, "bull") == -2.0


def test_intc_usl_scenario():
    # USL at -1.7% is NOT convertible at the base -2.0, but IS in bull at -1.0.
    usl_pnl = -1.7
    base = g._convert_min_loss_threshold({"v32_convert_min_loss_pct": -2.0}, "bull")
    assert not (usl_pnl <= base), "at base -2.0, USL(-1.7%) is not convertible (the observed skip)"
    bull = g._convert_min_loss_threshold(
        {"v32_convert_min_loss_pct": -2.0, "v32_convert_min_loss_pct_bull": -1.0}, "bull")
    assert usl_pnl <= bull, "at bull -1.0, USL(-1.7%) becomes convertible -> INTC funded"
