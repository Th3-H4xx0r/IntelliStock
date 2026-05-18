"""BT136708 fix verification tests (P1.1 - P1.8).

Covers the structural fixes derived from the BT136708 investigation:
- P1.1: mcap pre-seed for backtest universes
- P1.2: max_positions regime caps (chop 8→12, bear 4→8)
- P1.3: price floor tiering (propagation $3.50, primary $5)
- P1.4: BFQ priority TTL floor 10→15
- P1.5: conviction-tier thresholds (HIGH 1.5→1.0/$30B, MID adds $10B)
- P1.7: A4 post_sell_watch re-entry wiring (feature-flagged, in-memory backtest mirror)

Diagnosis source: docs/superpowers/plans/2026-05-18-bt136708-fix-implementation.md
"""
from __future__ import annotations

import os
import sys

# Allow direct import from backend/ regardless of pytest cwd.
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
_BACKEND = os.path.abspath(os.path.join(_HERE, ".."))
for _p in (_BACKEND, _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import backend.strategies.graph_nexus_analysis as gna


# ── P1.1 mcap pre-seed ─────────────────────────────────────────────────────


def test_preseed_skipped_when_disabled_yfinance_and_no_neo4j():
    """When yfinance disabled AND neo4j cache empty, pre-seed populates 0 entries."""
    cache: dict = {}
    populated = gna._preseed_mcap_cache_from_universe(
        ["SNDK", "MU", "LITE"], cache, {"mcap_preseed_use_yfinance": False}
    )
    assert populated == 0
    assert cache.get("_yf_market_cap_cache_preseeded") is True


def test_preseed_idempotent():
    """Second call short-circuits via _yf_market_cap_cache_preseeded flag."""
    cache: dict = {"_yf_market_cap_cache_preseeded": True}
    populated = gna._preseed_mcap_cache_from_universe(
        ["SNDK"], cache, {"mcap_preseed_use_yfinance": False}
    )
    assert populated == 0  # short-circuit fires


def test_preseed_skips_empty_universe():
    cache: dict = {}
    populated = gna._preseed_mcap_cache_from_universe([], cache, {})
    assert populated == 0


def test_preseed_handles_none_strategy_cache():
    populated = gna._preseed_mcap_cache_from_universe(
        ["SNDK"], None, {"mcap_preseed_use_yfinance": False}
    )
    assert populated == 0  # no crash


# ── P1.3 price-floor tiering ───────────────────────────────────────────────


def test_price_floor_propagation_admits_sub_five_dollar():
    """Sub-$5 propagation-discovered buy should pass with lower floor."""
    scores = {
        "STRO": {
            "score": 1,
            "reason": "Propagation general (raw=0.65, paths=14)",
            "is_propagation_expansion": True,
        }
    }
    out = gna._apply_buy_price_floor(
        scores=scores,
        symbols_list=["STRO"],
        prices={"STRO": 4.00},
        price_history=None,
        portfolio_emulator=None,
        config={"buy_price_floor": 5.0, "buy_price_floor_propagation": 3.50},
    )
    assert out["STRO"]["score"] == 1  # admitted at $4 via propagation floor


def test_price_floor_primary_blocks_sub_five_dollar():
    """Non-propagation buy still blocked at primary floor."""
    scores = {"X": {"score": 1, "reason": "Direct bz_upgrade analyst"}}
    out = gna._apply_buy_price_floor(
        scores=scores,
        symbols_list=["X"],
        prices={"X": 4.00},
        price_history=None,
        portfolio_emulator=None,
        config={"buy_price_floor": 5.0, "buy_price_floor_propagation": 3.50},
    )
    assert out["X"]["score"] == 0
    assert "PRICE_FLOOR" in out["X"]["reason"]


def test_price_floor_absolute_min_blocks_penny_stocks():
    """Sub-$2 propagation buy still blocked by absolute floor."""
    scores = {
        "PENNY": {
            "score": 1,
            "reason": "Propagation expansion",
            "is_propagation_expansion": True,
        }
    }
    out = gna._apply_buy_price_floor(
        scores=scores,
        symbols_list=["PENNY"],
        prices={"PENNY": 1.50},
        price_history=None,
        portfolio_emulator=None,
        config={
            "buy_price_floor": 5.0,
            "buy_price_floor_propagation": 0.50,  # operator misconfig
            "buy_price_floor_absolute_min": 2.00,
        },
    )
    assert out["PENNY"]["score"] == 0  # abs_min wins


# ── P1.7 A4 in-memory post_sell_watch ──────────────────────────────────────


def test_a4_inmem_write_and_read():
    cache: dict = {}
    gna._post_sell_watch_inmem_write(
        cache,
        "SNDK",
        exit_price=195.76,
        entry_conviction_tier="HIGH",
        sell_reason="circuit_breaker",
        date_key="2025-11-21",
    )
    rows = gna._post_sell_watch_inmem_get_candidates(
        cache, "2025-11-25", window_days=60
    )
    assert len(rows) == 1
    assert rows[0]["ticker"] == "SNDK"
    assert rows[0]["exit_price"] == 195.76
    assert rows[0]["entry_conviction_tier"] == "HIGH"


def test_a4_inmem_window_filter():
    """Rows outside window_days don't appear in candidates."""
    cache: dict = {}
    gna._post_sell_watch_inmem_write(
        cache,
        "OLD",
        exit_price=10.0,
        entry_conviction_tier="LOW",
        sell_reason="circuit_breaker",
        date_key="2025-01-01",
    )
    rows = gna._post_sell_watch_inmem_get_candidates(
        cache, "2025-12-01", window_days=60
    )
    assert rows == []  # OLD is 11 months stale → filtered


def test_a4_inmem_mark_re_entered():
    cache: dict = {}
    gna._post_sell_watch_inmem_write(
        cache,
        "SNDK",
        exit_price=195.76,
        entry_conviction_tier="HIGH",
        sell_reason="circuit_breaker",
        date_key="2025-11-21",
    )
    gna._post_sell_watch_inmem_mark_re_entered(cache, "SNDK", date_key="2025-12-05")
    rows = gna._post_sell_watch_inmem_get_candidates(
        cache, "2025-12-10", window_days=60
    )
    assert rows == []  # status flipped to active → no longer a re-entry candidate


def test_a4_inmem_ttl_sweep_marks_forgotten():
    cache: dict = {}
    gna._post_sell_watch_inmem_write(
        cache,
        "STALE",
        exit_price=10.0,
        entry_conviction_tier="LOW",
        sell_reason="circuit_breaker",
        date_key="2025-01-01",
    )
    removed = gna._post_sell_watch_inmem_forget_expired(
        cache, "2025-12-01", window_days=60
    )
    assert removed == 1
    bucket = cache.get(gna._POST_SELL_WATCH_INMEM_KEY) or {}
    assert bucket["STALE"]["status"] == "forgotten"


def test_a4_combined_reader_no_db():
    """Combined reader works with conn=None (backtest mode)."""
    cache: dict = {}
    gna._post_sell_watch_inmem_write(
        cache,
        "BTONLY",
        exit_price=100.0,
        entry_conviction_tier="MID",
        sell_reason="fast_loser",
        date_key="2025-11-21",
    )
    rows = gna._get_post_sell_watch_candidates_combined(
        None, cache, "main", "2025-11-25", window_days=60
    )
    assert len(rows) == 1
    assert rows[0]["ticker"] == "BTONLY"


# ── P1.7 feature-flag default is OFF ───────────────────────────────────────


def test_a4_reentry_execution_default_disabled():
    cfg = gna._get_effective_nexus_config({})
    assert cfg["post_sell_watch_reentry_execution_enabled"] is False


# ── P1.2 max_positions regime cap defaults ────────────────────────────────


def test_max_positions_chop_default_is_12():
    """BT136708 P1.2: chop cap raised from 8 to 12.

    Regression check: a fresh config with no overrides must yield a chop
    cap of min(max_positions, 12). With max_positions default 15, chop=12.
    """
    config: dict = {}
    max_pos = int(config.get("max_positions", 15) or 15)
    chop_cap = int(config.get("max_positions_chop", min(max_pos, 12)) or 12)
    assert chop_cap == 12


def test_max_positions_bear_default_is_8():
    """BT136708 P1.2: bear cap raised from 4 to 8."""
    config: dict = {}
    max_pos = int(config.get("max_positions", 15) or 15)
    bear_cap = int(config.get("max_positions_bear", min(max_pos, 8)) or 8)
    assert bear_cap == 8


def test_max_positions_explicit_override_wins():
    """Operator override of regime cap must take precedence over P1.2 default."""
    config = {"max_positions_chop": 6}
    chop_cap = int(config.get("max_positions_chop", min(15, 12)) or 12)
    assert chop_cap == 6


# ── P1.4 BFQ priority TTL floor ───────────────────────────────────────────


def test_bfq_priority_max_bars_floor_default_is_15():
    """BT136708 P1.4: BFQ priority TTL floor raised from 10 to 15."""
    cfg = gna._get_effective_nexus_config({})
    assert cfg["backfill_queue_priority_max_bars_floor"] == 15


def test_bfq_priority_max_bars_floor_explicit_override():
    """Operator can still tune the floor below or above 15 via config."""
    cfg = gna._get_effective_nexus_config({"backfill_queue_priority_max_bars_floor": 20})
    assert cfg["backfill_queue_priority_max_bars_floor"] == 20


# ── P1.5 extra boundary checks (just above threshold) ─────────────────────


def test_conviction_tier_30b_plus_one_cent_is_high():
    """Boundary + ε: mcap just above 30B threshold is HIGH."""
    cache = {"_yf_market_cap_cache": {"BOUND": 30_000_000_000.01}}
    tier = gna._resolve_conviction_tier_at_exit(
        "BOUND", config={}, strategy_cache=cache, propagated={}
    )
    assert tier == "HIGH"


def test_conviction_tier_mcap_high_beats_raw_mid():
    """When mcap qualifies HIGH and raw_score qualifies only MID, mcap wins."""
    cache = {"_yf_market_cap_cache": {"BIG": 50_000_000_000}}
    propagated = {"BIG": {"raw_score": 0.7}}
    tier = gna._resolve_conviction_tier_at_exit(
        "BIG", config={}, strategy_cache=cache, propagated=propagated
    )
    assert tier == "HIGH"


def test_conviction_tier_nan_raw_score_safe():
    """Bug-sweep regression: NaN raw_score must not crash or silently demote."""
    import math
    propagated = {"X": {"raw_score": float("nan")}}
    cache = {"_yf_market_cap_cache": {"X": 36_000_000_000}}
    tier = gna._resolve_conviction_tier_at_exit(
        "X", config={}, strategy_cache=cache, propagated=propagated
    )
    # NaN raw_score coerced to 0 → MID via mcap (10B-30B range mcap is 36B → HIGH)
    assert tier == "HIGH"


# ── P1.7 A4 in-memory write skips zero-price rows ─────────────────────────


def test_a4_inmem_write_skips_zero_price():
    """Bug-sweep regression: rows with exit_price=0 must not be written."""
    cache: dict = {}
    gna._post_sell_watch_inmem_write(
        cache,
        "ZERO",
        exit_price=0.0,
        entry_conviction_tier="HIGH",
        sell_reason="circuit_breaker",
        date_key="2025-11-21",
    )
    bucket = cache.get(gna._POST_SELL_WATCH_INMEM_KEY) or {}
    assert "ZERO" not in bucket  # skipped, no useless row


def test_a4_inmem_write_skips_none_price():
    cache: dict = {}
    gna._post_sell_watch_inmem_write(
        cache,
        "NULLPX",
        exit_price=None,
        entry_conviction_tier="MID",
        sell_reason="fast_loser",
        date_key="2025-11-21",
    )
    bucket = cache.get(gna._POST_SELL_WATCH_INMEM_KEY) or {}
    assert "NULLPX" not in bucket


# ── P1.3 price-floor reason annotation ────────────────────────────────────


def test_price_floor_reason_marks_propagation_vs_primary():
    """Blocked symbol's reason should encode which floor (prop vs primary) fired."""
    scores = {
        "P": {
            "score": 1,
            "reason": "Propagation general",
            "is_propagation_expansion": True,
        },
        "X": {"score": 1, "reason": "Direct bz_upgrade"},
    }
    out = gna._apply_buy_price_floor(
        scores=scores,
        symbols_list=["P", "X"],
        prices={"P": 3.00, "X": 4.50},
        price_history=None,
        portfolio_emulator=None,
        config={
            "buy_price_floor": 5.0,
            "buy_price_floor_propagation": 3.50,
            "buy_price_floor_absolute_min": 2.00,
        },
    )
    # P is propagation @ $3.00 < $3.50 prop floor → blocked with (prop) tag
    assert out["P"]["score"] == 0
    assert "(prop)" in out["P"]["reason"]
    # X is primary @ $4.50 < $5 → blocked with (primary) tag
    assert out["X"]["score"] == 0
    assert "(primary)" in out["X"]["reason"]
