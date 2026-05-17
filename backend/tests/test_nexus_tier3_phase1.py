"""Tier-3 Phase 1 — conviction-aware floor + scaled cash reserve + relaxed rotation.

Spec: docs/superpowers/specs/2026-05-17-nexus-tier3-missed-rally-fixes-design.md
Plan: docs/superpowers/plans/2026-05-17-nexus-tier3-missed-rally-fixes-implementation.md
"""
from __future__ import annotations

import os
import sys

import pytest

# Allow direct import from backend/.
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
_BACKEND = os.path.abspath(os.path.join(_HERE, ".."))
for _p in (_BACKEND, _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from strategies import graph_nexus_analysis as gna  # noqa: E402


# ── A1: conviction tier resolver ────────────────────────────────────────────


def test_conviction_tier_high_via_mcap():
    """Mega-cap (mcap >= 50B) → HIGH regardless of raw_score."""
    cache = {"_yf_market_cap_cache": {"SNDK": 75_000_000_000}}
    tier = gna._resolve_conviction_tier_at_exit(
        "SNDK", config={}, strategy_cache=cache, propagated={}
    )
    assert tier == "HIGH"


def test_conviction_tier_high_via_raw_score():
    """raw_score >= 1.5 → HIGH even on small-cap."""
    cache = {"_yf_market_cap_cache": {"LITE": 5_000_000_000}}
    propagated = {"LITE": {"raw_score": 1.5}}
    tier = gna._resolve_conviction_tier_at_exit(
        "LITE", config={}, strategy_cache=cache, propagated=propagated
    )
    assert tier == "HIGH"


def test_conviction_tier_mid_via_raw_score():
    """raw_score in [0.5, 1.5) → MID."""
    propagated = {"FOO": {"raw_score": 0.8}}
    tier = gna._resolve_conviction_tier_at_exit(
        "FOO", config={}, strategy_cache={}, propagated=propagated
    )
    assert tier == "MID"


def test_conviction_tier_low_default():
    """Unknown mcap + raw_score < 0.5 → LOW (matches pre-Tier-3 behavior)."""
    tier = gna._resolve_conviction_tier_at_exit(
        "BARK", config={}, strategy_cache={}, propagated={}
    )
    assert tier == "LOW"


def test_conviction_tier_low_with_zero_mcap():
    """Zero mcap in cache → treated as None (LOW unless raw_score qualifies)."""
    cache = {"_yf_market_cap_cache": {"BAR": 0}}
    tier = gna._resolve_conviction_tier_at_exit(
        "BAR", config={}, strategy_cache=cache, propagated={"BAR": {"raw_score": 0.3}}
    )
    assert tier == "LOW"


def test_conviction_tier_handles_none_inputs():
    """Defensive: bad inputs return LOW, never raise."""
    assert gna._resolve_conviction_tier_at_exit("FOO", config={}, strategy_cache=None, propagated=None) == "LOW"
    assert gna._resolve_conviction_tier_at_exit("", config={}, strategy_cache=None, propagated=None) == "LOW"


# ── A1: conviction-aware floor mapping ──────────────────────────────────────


def test_conviction_floor_high_disables_vol_scaling():
    """HIGH tier returns vol_mult=0 so absolute floor dominates."""
    floor, vol_mult, low_vol = gna._get_conviction_aware_floor("HIGH", config={})
    assert floor == -25.0
    assert vol_mult == 0.0
    assert low_vol == 0.08


def test_conviction_floor_mid_uses_legacy_vol_mult():
    """MID returns the legacy vol_mult (so vol-scaling still applies)."""
    floor, vol_mult, _ = gna._get_conviction_aware_floor("MID", config={})
    assert floor == -20.0
    assert vol_mult == 2.0


def test_conviction_floor_low_matches_pretier3():
    """LOW returns the original -15% floor."""
    floor, vol_mult, _ = gna._get_conviction_aware_floor("LOW", config={})
    assert floor == -15.0
    assert vol_mult == 2.0


def test_conviction_floor_config_override():
    """Custom thresholds via config keys."""
    cfg = {
        "circuit_breaker_floor_high_conviction_pct": -30.0,
        "circuit_breaker_floor_mid_conviction_pct": -22.0,
        "circuit_breaker_low_vol_disable_threshold_pct": 0.10,
    }
    assert gna._get_conviction_aware_floor("HIGH", cfg)[0] == -30.0
    assert gna._get_conviction_aware_floor("MID", cfg)[0] == -22.0
    assert gna._get_conviction_aware_floor("HIGH", cfg)[2] == 0.10


# ── A2: regime adjustment ───────────────────────────────────────────────────


def test_regime_bull_widens_floor():
    """Bull regime adds +5pp (less negative) to the base floor."""
    floor, _, _ = gna._get_conviction_aware_floor("LOW", config={}, regime="bull")
    assert floor == -10.0  # -15 + 5


def test_regime_bear_tightens_floor():
    """Bear regime subtracts 5pp (more negative)."""
    floor, _, _ = gna._get_conviction_aware_floor("LOW", config={}, regime="bear")
    assert floor == -20.0  # -15 - 5


def test_regime_chop_no_adjustment():
    """Chop regime returns base floor unchanged."""
    floor, _, _ = gna._get_conviction_aware_floor("LOW", config={}, regime="chop")
    assert floor == -15.0


def test_regime_crash_returns_emergency_sentinel():
    """Crash regime returns 0.0 floor sentinel (caller treats as emergency-exit)."""
    floor, vol_mult, _ = gna._get_conviction_aware_floor("HIGH", config={}, regime="crash")
    assert floor == 0.0
    assert vol_mult == 0.0


def test_regime_unknown_no_adjustment():
    """Unknown regime string returns base floor unchanged."""
    floor, _, _ = gna._get_conviction_aware_floor("LOW", config={}, regime="weird")
    assert floor == -15.0


# ── B1: scaled cash reserve floor ───────────────────────────────────────────


def test_cash_reserve_small_account():
    """< $50K → 5% floor."""
    assert gna._get_scaled_cash_reserve_floor_pct({}, 7_000.0) == 0.05
    assert gna._get_scaled_cash_reserve_floor_pct({}, 49_999.0) == 0.05


def test_cash_reserve_mid_account():
    """$50K to $200K → 7.5% floor."""
    assert gna._get_scaled_cash_reserve_floor_pct({}, 50_000.0) == 0.075
    assert gna._get_scaled_cash_reserve_floor_pct({}, 150_000.0) == 0.075
    assert gna._get_scaled_cash_reserve_floor_pct({}, 199_999.0) == 0.075


def test_cash_reserve_large_account():
    """>= $200K → 10% floor."""
    assert gna._get_scaled_cash_reserve_floor_pct({}, 200_000.0) == 0.10
    assert gna._get_scaled_cash_reserve_floor_pct({}, 1_000_000.0) == 0.10


def test_cash_reserve_explicit_override():
    """Explicit cash_reserve_floor_pct config wins (back-compat)."""
    assert gna._get_scaled_cash_reserve_floor_pct({"cash_reserve_floor_pct": 0.20}, 7_000.0) == 0.20


def test_cash_reserve_handles_bad_initial_value():
    """Bad input → returns small-account default (no exception)."""
    assert gna._get_scaled_cash_reserve_floor_pct({}, None) == 0.05  # treated as 0
    assert gna._get_scaled_cash_reserve_floor_pct({}, "bad") == 0.05


# ── B2: scaled max new stock buys ───────────────────────────────────────────


def test_max_new_stock_buys_small_account():
    """< $50K → 6 buys per cycle."""
    assert gna._get_scaled_max_new_stock_buys({}, 7_000.0) == 6


def test_max_new_stock_buys_mid_account():
    """$50K to $500K → 8 buys per cycle."""
    assert gna._get_scaled_max_new_stock_buys({}, 100_000.0) == 8


def test_max_new_stock_buys_large_account():
    """>= $500K → 12 buys per cycle."""
    assert gna._get_scaled_max_new_stock_buys({}, 1_000_000.0) == 12


def test_max_new_stock_buys_explicit_override():
    """Explicit allocation_max_new_stock_buys wins (back-compat)."""
    assert gna._get_scaled_max_new_stock_buys({"allocation_max_new_stock_buys": 4}, 7_000.0) == 4


def test_max_new_stock_buys_minimum_1():
    """Never returns 0 even if config has a bad value."""
    assert gna._get_scaled_max_new_stock_buys({"allocation_max_new_stock_buys": 0}, 7_000.0) == 1


# ── B3: relaxed rotation thresholds via _get_effective_nexus_config ─────────


def test_effective_nexus_config_relaxed_rotation_defaults():
    """Default `_get_effective_nexus_config` should reflect Tier-3 B3 defaults."""
    cfg = gna._get_effective_nexus_config({})
    assert cfg["rotation_winner_lock_min_hold_days"] == 3
    assert cfg["rotation_winner_lock_min_pnl_pct"] == 2.0
    assert cfg["rotation_profitable_min_delta"] == 1.00
    assert cfg["rotation_break_glass_delta"] == 1.50


def test_effective_nexus_config_explicit_override_preserved():
    """Operator override for rotation thresholds still applies."""
    cfg = gna._get_effective_nexus_config({
        "rotation_winner_lock_min_hold_days": 5,
        "rotation_winner_lock_min_pnl_pct": 3.0,
        "rotation_profitable_min_delta": 1.5,
        "rotation_break_glass_delta": 2.25,
    })
    assert cfg["rotation_winner_lock_min_hold_days"] == 5
    assert cfg["rotation_winner_lock_min_pnl_pct"] == 3.0
    assert cfg["rotation_profitable_min_delta"] == 1.5
    assert cfg["rotation_break_glass_delta"] == 2.25


# ── Surface check: all new Tier-3 knobs visible ─────────────────────────────


def test_effective_nexus_config_surfaces_tier3_a1_knobs():
    cfg = gna._get_effective_nexus_config({})
    for key in (
        "circuit_breaker_floor_high_conviction_pct",
        "circuit_breaker_floor_mid_conviction_pct",
        "circuit_breaker_floor_low_conviction_pct",
        "circuit_breaker_high_conviction_mcap_threshold_usd",
        "circuit_breaker_high_conviction_raw_score_threshold",
        "circuit_breaker_mid_conviction_raw_score_threshold",
        "circuit_breaker_low_vol_disable_threshold_pct",
        "circuit_breaker_regime_gating_enabled",
        "circuit_breaker_regime_adjustment_bull_pp",
        "circuit_breaker_regime_adjustment_bear_pp",
        "cash_reserve_floor_small_pct",
        "cash_reserve_floor_mid_pct",
        "cash_reserve_floor_large_pct",
        "cash_reserve_floor_threshold_small_usd",
        "cash_reserve_floor_threshold_mid_usd",
        "allocation_max_new_stock_buys_small",
        "allocation_max_new_stock_buys_mid",
        "allocation_max_new_stock_buys_large",
    ):
        assert key in cfg, f"Tier-3 knob missing from diagnostic dump: {key}"


# ── _resolve_position_market_cap helper ─────────────────────────────────────


def test_position_market_cap_from_cache():
    cache = {"_yf_market_cap_cache": {"AAA": 123e9}}
    assert gna._resolve_position_market_cap("AAA", cache) == 123e9


def test_position_market_cap_missing_cache():
    assert gna._resolve_position_market_cap("AAA", None) is None
    assert gna._resolve_position_market_cap("AAA", {}) is None
    assert gna._resolve_position_market_cap("AAA", {"_yf_market_cap_cache": {}}) is None


def test_position_market_cap_zero_returns_none():
    """Zero mcap (yfinance miss sentinel) returns None, not 0.0."""
    cache = {"_yf_market_cap_cache": {"X": 0}}
    assert gna._resolve_position_market_cap("X", cache) is None


# ── Bug-sweep additions (2026-05-17) ────────────────────────────────────────


def test_conviction_tier_mcap_boundary_exactly_50b():
    """Boundary: mcap == 50B exactly should map to HIGH (>=, not >)."""
    cache = {"_yf_market_cap_cache": {"BOUND": 50_000_000_000}}
    tier = gna._resolve_conviction_tier_at_exit(
        "BOUND", config={}, strategy_cache=cache, propagated={}
    )
    assert tier == "HIGH"


def test_conviction_tier_mcap_just_below_50b():
    """Boundary: mcap just under 50B should NOT map to HIGH via mcap path."""
    cache = {"_yf_market_cap_cache": {"BOUND": 49_999_999_999}}
    tier = gna._resolve_conviction_tier_at_exit(
        "BOUND", config={}, strategy_cache=cache, propagated={}
    )
    # No raw_score either → LOW
    assert tier == "LOW"


def test_max_new_stock_buys_bad_string_config_falls_back():
    """Bad string for allocation_max_new_stock_buys falls back without crash."""
    result = gna._get_scaled_max_new_stock_buys(
        {"allocation_max_new_stock_buys": "not-a-number"}, 7_000.0
    )
    # Should fall through explicit-override try/except and use scaled default.
    assert isinstance(result, int)
    assert result >= 1


def test_cash_reserve_bad_string_threshold_falls_back():
    """Bad threshold values in config fall back without crash."""
    result = gna._get_scaled_cash_reserve_floor_pct(
        {"cash_reserve_floor_threshold_small_usd": "bad"}, 7_000.0
    )
    assert isinstance(result, float)
    assert 0.0 <= result <= 1.0


def test_chop_regime_adjustment_knob_surfaced():
    """Bug-sweep: chop adjustment knob is exposed for operator tuning."""
    cfg = gna._get_effective_nexus_config({})
    assert "circuit_breaker_regime_adjustment_chop_pp" in cfg
    assert cfg["circuit_breaker_regime_adjustment_chop_pp"] == 0.0
