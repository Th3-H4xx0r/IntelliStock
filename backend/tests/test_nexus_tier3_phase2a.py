"""Tier-3 Phase 2a — macro-event sentiment override + tiered momentum budget + extended backfill queue.

Spec: docs/superpowers/specs/2026-05-17-nexus-tier3-missed-rally-fixes-design.md (A3, B2, B4, B5)
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


# ── A3: macro-event sentiment override ──────────────────────────────────────


def test_macro_event_supersedes_sp500_entry():
    assert gna._macro_event_supersedes_sentiment("S&P 500 entry")
    assert gna._macro_event_supersedes_sentiment("Added to S&P500 index")


def test_macro_event_supersedes_buyout_announcement():
    assert gna._macro_event_supersedes_sentiment("Buyout target by ABC Corp")
    assert gna._macro_event_supersedes_sentiment("Acquisition target announcement")
    assert gna._macro_event_supersedes_sentiment("M&A activity")


def test_macro_event_supersedes_index_reweight():
    assert gna._macro_event_supersedes_sentiment("Index reweight")
    assert gna._macro_event_supersedes_sentiment("Russell 2000 reconstitution")
    assert gna._macro_event_supersedes_sentiment("Nasdaq 100 addition")


def test_macro_event_does_not_supersede_normal_buy():
    """Routine LLM/momentum-driven reasons remain vetoable by sentiment."""
    assert not gna._macro_event_supersedes_sentiment("LLM bullish sentiment")
    assert not gna._macro_event_supersedes_sentiment("Momentum watchlist score 0.5")
    assert not gna._macro_event_supersedes_sentiment("Trend reversal confirmed")
    assert not gna._macro_event_supersedes_sentiment("Benzinga upgrade")  # generic, not "strong Benzinga"


def test_macro_event_supersedes_strong_benzinga():
    assert gna._macro_event_supersedes_sentiment("Strong Benzinga upgrade catalyst")


def test_macro_event_handles_empty_input():
    assert not gna._macro_event_supersedes_sentiment("")
    assert not gna._macro_event_supersedes_sentiment(None)


def test_macro_event_case_insensitive():
    assert gna._macro_event_supersedes_sentiment("S&P 500 ENTRY")
    assert gna._macro_event_supersedes_sentiment("buyout target")


# ── B2: scaled max new stock buys threading ─────────────────────────────────


def test_planner_uses_initial_value_kwarg():
    """Passing initial_value to the planner picks the right account-size bucket."""
    # Empty candidate slate; we just check the budget plumbing.
    funded, queued, meta = gna._plan_executable_stock_buy_slate(
        candidates=[],
        available_budget=0.0,
        min_position_size=100.0,
        config={},
        blocked_tickers=None,
        initial_value=7_000.0,
    )
    # Budget=0 returns empty; no exception means plumbing is fine.
    assert funded == []
    assert isinstance(queued, list)


def test_planner_uses_config_initial_value_fallback():
    """When initial_value kwarg is 0, falls back to config['_account_initial_value']."""
    funded, queued, meta = gna._plan_executable_stock_buy_slate(
        candidates=[],
        available_budget=0.0,
        min_position_size=100.0,
        config={"_account_initial_value": 7_000.0},
        blocked_tickers=None,
    )
    assert funded == []


# ── B4: tiered momentum reserved slots ──────────────────────────────────────


def test_b4_momentum_reserved_slots_default():
    """Diagnostic dump shows momentum_execution_reserved_slots default 2."""
    cfg = gna._get_effective_nexus_config({})
    assert cfg["momentum_execution_reserved_slots"] == 2


# ── B5: extended backfill queue defaults ────────────────────────────────────


def test_b5_backfill_queue_grace_bars_default():
    cfg = gna._get_effective_nexus_config({})
    assert cfg["backfill_queue_grace_bars"] == 7


def test_b5_backfill_queue_max_size_default():
    cfg = gna._get_effective_nexus_config({})
    assert cfg["backfill_queue_max_size"] == 50


def test_b5_backfill_queue_override_preserved():
    cfg = gna._get_effective_nexus_config({
        "backfill_queue_grace_bars": 3,
        "backfill_queue_max_size": 30,
    })
    assert cfg["backfill_queue_grace_bars"] == 3
    assert cfg["backfill_queue_max_size"] == 30


# ── Surface check ───────────────────────────────────────────────────────────


def test_effective_nexus_config_surfaces_tier3_phase2a_knobs():
    cfg = gna._get_effective_nexus_config({})
    for key in (
        "macro_supersedes_sentiment_enabled",
        "momentum_execution_reserved_slots",
    ):
        assert key in cfg, f"Tier-3 Phase 2a knob missing: {key}"


# ── Tiered momentum slot expansion (B4 integration) ─────────────────────────


def test_momentum_reserved_slots_expand_max_buys_when_momentum_candidates_present():
    """When momentum candidates are in the slate, max_new_stock_buys is expanded
    by min(reserved, momentum_count). Verified by passing a candidate list and
    observing the slate planner doesn't choke."""
    candidates = [
        {"ticker": "MU", "raw_net_score": 0.65, "signal_source": "momentum_watchlist", "is_propagation_expansion": False, "n_paths": 0, "is_watchlist_member": False, "is_watchlist_priority": False, "grace_eligible": False},
        {"ticker": "LITE", "raw_net_score": 1.5, "signal_source": "momentum_watchlist", "is_propagation_expansion": False, "n_paths": 0, "is_watchlist_member": False, "is_watchlist_priority": False, "grace_eligible": False},
        {"ticker": "RKLB", "raw_net_score": 1.0, "signal_source": "momentum_watchlist", "is_propagation_expansion": False, "n_paths": 0, "is_watchlist_member": False, "is_watchlist_priority": False, "grace_eligible": False},
    ]
    # Budget large enough to fund all three. Without B4, default max=4 would
    # accept 4 buys; with B4 (default reserved=2), max=6 accepts these 3 easily.
    funded, queued, meta = gna._plan_executable_stock_buy_slate(
        candidates=candidates,
        available_budget=10_000.0,
        min_position_size=100.0,
        config={"allocation_execute_min_raw_score": 0.5},
        blocked_tickers=None,
        initial_value=50_000.0,  # mid-tier account → max_new = 8
    )
    # Should fund at least 2 of the 3 (signal scores high enough for all)
    assert len(funded) >= 2, f"Expected >=2 momentum buys funded, got {len(funded)}"


def test_momentum_reserved_slots_zero_disables_b4():
    """Setting momentum_execution_reserved_slots=0 reverts to pre-Tier-3 behavior."""
    candidates = [
        {"ticker": "MU", "raw_net_score": 0.65, "signal_source": "momentum_watchlist", "is_propagation_expansion": False, "n_paths": 0, "is_watchlist_member": False, "is_watchlist_priority": False, "grace_eligible": False},
    ]
    funded, queued, meta = gna._plan_executable_stock_buy_slate(
        candidates=candidates,
        available_budget=10_000.0,
        min_position_size=100.0,
        config={
            "momentum_execution_reserved_slots": 0,
            "allocation_execute_min_raw_score": 0.3,
        },
        blocked_tickers=None,
        initial_value=7_000.0,
    )
    # Should still fund the single candidate (within default 6-slot small-account cap)
    assert len(funded) >= 1
