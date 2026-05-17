"""Tier-3 Phase 2b — post_sell_watch re-entry pipeline + regime-gated floors.

Spec: docs/superpowers/specs/2026-05-17-nexus-tier3-missed-rally-fixes-design.md (A4, A2)
"""
from __future__ import annotations

import os
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND = os.path.abspath(os.path.join(_HERE, ".."))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from strategies import graph_nexus_analysis as gna  # noqa: E402


# ── A4: post-sell re-entry eligibility ──────────────────────────────────────


def test_reentry_eligible_recovery_threshold():
    """Price 6% above exit price + score above floor → eligible."""
    eligible, reason = gna._is_post_sell_reentry_eligible(
        current_price=210.0,
        exit_price=200.0,
        fresh_raw_score=0.5,
        config={},
    )
    assert eligible
    assert "recovery_threshold" in reason


def test_reentry_blocked_insufficient_score():
    """Even with strong recovery, score below 0.40 blocks re-entry."""
    eligible, reason = gna._is_post_sell_reentry_eligible(
        current_price=300.0,
        exit_price=200.0,
        fresh_raw_score=0.30,
        config={},
    )
    assert not eligible
    assert "raw_score" in reason


def test_reentry_eligible_resistance_break():
    """10-day high + 0.5% buffer triggers when recovery threshold isn't hit."""
    # Price 4% above exit (below 5% threshold) but breaks 10d high.
    eligible, reason = gna._is_post_sell_reentry_eligible(
        current_price=208.0,
        exit_price=200.0,
        fresh_raw_score=0.5,
        config={},
        recent_high_after_exit=205.0,  # 208 > 205 × 1.005 = 206.025
    )
    assert eligible
    assert "resistance_break" in reason


def test_reentry_blocked_no_trigger():
    """Within window but no trigger → not eligible."""
    eligible, reason = gna._is_post_sell_reentry_eligible(
        current_price=202.0,  # below 5% recovery + below 10d_high
        exit_price=200.0,
        fresh_raw_score=0.5,
        config={},
        recent_high_after_exit=208.0,
    )
    assert not eligible


def test_reentry_missing_prices():
    """Defensive: zero/negative prices → not eligible."""
    eligible, reason = gna._is_post_sell_reentry_eligible(
        current_price=0.0,
        exit_price=200.0,
        fresh_raw_score=1.0,
        config={},
    )
    assert not eligible


def test_reentry_custom_recovery_threshold():
    """Operator can tune the recovery threshold."""
    eligible, _ = gna._is_post_sell_reentry_eligible(
        current_price=220.0,
        exit_price=200.0,
        fresh_raw_score=0.5,
        config={"post_sell_recovery_threshold_pct": 0.15},  # need 15% recovery
    )
    assert not eligible  # 10% recovery < 15% threshold


def test_reentry_custom_min_score():
    """Operator can raise the score gate."""
    eligible, _ = gna._is_post_sell_reentry_eligible(
        current_price=300.0,
        exit_price=200.0,
        fresh_raw_score=0.5,
        config={"post_sell_reentry_min_raw_score": 1.0},
    )
    assert not eligible


# ── A4: helper smoke tests (no DB) ──────────────────────────────────────────


def test_mark_sold_helpers_dont_raise_without_conn():
    """All A4 DB helpers must be safe when conn is None (fail-open)."""
    # Should silently no-op.
    gna._mark_discovered_stock_sold(None, "main", "AAPL", reason="test", date_key="2026-05-17")
    gna._mark_discovered_stock_sold(
        None, "main", "SNDK", reason="Circuit breaker", date_key="2026-05-17",
        forced_exit=True, exit_price=195.77, entry_conviction_tier="HIGH",
    )
    gna._mark_discovered_stock_re_entered(None, "main", "SNDK", date_key="2026-05-17")
    gna._mark_discovered_stock_forgotten(None, "main", "SNDK")


def test_get_post_sell_watch_candidates_no_conn():
    """Returns empty list when no DB connection."""
    out = gna._get_post_sell_watch_candidates(None, "main", "2026-05-17")
    assert out == []


def test_get_post_sell_watch_candidates_no_instance():
    out = gna._get_post_sell_watch_candidates(None, "", "2026-05-17")
    assert out == []


# ── A2: regime-gated floor (integration via _get_conviction_aware_floor) ────


def test_a2_regime_bull_widens_high_tier_floor():
    """HIGH-tier base floor -25%; bull regime adds +5pp → -20% effective."""
    floor, _, _ = gna._get_conviction_aware_floor("HIGH", {}, regime="bull")
    assert floor == -20.0


def test_a2_regime_bear_tightens_mid_tier_floor():
    """MID-tier base floor -20%; bear regime adds -5pp → -25% effective."""
    floor, _, _ = gna._get_conviction_aware_floor("MID", {}, regime="bear")
    assert floor == -25.0


def test_a2_regime_crash_emergency_signals():
    """Crash regime returns (0.0, 0.0, _) sentinel → caller treats as immediate sell."""
    floor, vol_mult, _ = gna._get_conviction_aware_floor("HIGH", {}, regime="crash")
    assert floor == 0.0
    assert vol_mult == 0.0


def test_a2_regime_none_disables_adjustment():
    """regime=None keeps base floor; regime gating disabled at call site."""
    floor, _, _ = gna._get_conviction_aware_floor("LOW", {}, regime=None)
    assert floor == -15.0  # base, no adjustment


def test_a2_custom_regime_adjustment_pp():
    """Operator can tune the regime delta knobs."""
    floor, _, _ = gna._get_conviction_aware_floor(
        "LOW",
        {"circuit_breaker_regime_adjustment_bull_pp": 10.0},
        regime="bull",
    )
    assert floor == -5.0  # -15 + 10


# ── Effective config surface ────────────────────────────────────────────────


def test_effective_nexus_config_surfaces_phase2b_knobs():
    cfg = gna._get_effective_nexus_config({})
    for key in (
        "post_sell_watch_enabled",
        "post_sell_watch_window_days",
        "post_sell_recovery_threshold_pct",
        "post_sell_resistance_break_pct",
        "post_sell_resistance_lookback_days",
        "post_sell_reentry_min_raw_score",
        "post_sell_reentry_size_fraction",
    ):
        assert key in cfg


def test_effective_nexus_config_post_sell_watch_defaults():
    cfg = gna._get_effective_nexus_config({})
    assert cfg["post_sell_watch_enabled"] is True
    assert cfg["post_sell_watch_window_days"] == 60
    assert cfg["post_sell_recovery_threshold_pct"] == 0.05
    assert cfg["post_sell_resistance_break_pct"] == 0.005
    assert cfg["post_sell_reentry_min_raw_score"] == 0.40
    assert cfg["post_sell_reentry_size_fraction"] == 0.50
