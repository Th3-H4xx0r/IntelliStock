"""Tests for the Tier-2 P&L maximization interventions (2026-05-15 spec).

Covers the new helpers added to graph_nexus_analysis.py:
- _realized_vol_20d
- _spy_20d_return (smoke test only — depends on strategy_cache shape)
- _nexus_regime_classify
- _recent_closes_for_symbol

The actual decision-loop integration is covered by the existing
test_nexus_strategy_bugsweep.py + dispatch tests; these are unit tests
on the leaf helpers so future regressions on the numeric thresholds get
caught at the smallest surface.
"""

import math

from backend.strategies.graph_nexus_analysis import (
    _realized_vol_20d,
    _nexus_regime_classify,
    _recent_closes_for_symbol,
    _spy_20d_return,
)


# ── _realized_vol_20d ────────────────────────────────────────────────


def test_realized_vol_empty_returns_zero():
    assert _realized_vol_20d([]) == 0.0


def test_realized_vol_none_returns_zero():
    assert _realized_vol_20d(None) == 0.0  # type: ignore[arg-type]


def test_realized_vol_too_few_bars_returns_zero():
    assert _realized_vol_20d([100.0, 101.0]) == 0.0


def test_realized_vol_zero_prices_filtered():
    # All non-positive prices filtered → too few valid bars → 0
    assert _realized_vol_20d([0.0, -1.0, 0.0]) == 0.0


def test_realized_vol_constant_prices_is_zero():
    # log(1.0/1.0) = 0; variance = 0; vol = 0
    assert _realized_vol_20d([100.0] * 10) == 0.0


def test_realized_vol_volatile_is_positive_and_bounded():
    import random
    random.seed(42)
    prices = [100.0]
    for _ in range(20):
        prices.append(prices[-1] * (1 + random.uniform(-0.05, 0.05)))
    v = _realized_vol_20d(prices)
    assert v > 0.05  # at least 5% annualized
    assert v < 10.0  # sanity upper bound


def test_realized_vol_higher_moves_higher_vol():
    """Bigger daily swings should produce higher vol than smaller swings."""
    small = [100.0 + i * 0.1 for i in range(20)]  # 0.1% daily drift
    large = [100.0 + i * 5.0 for i in range(20)]  # 5% daily drift
    v_small = _realized_vol_20d(small)
    v_large = _realized_vol_20d(large)
    assert v_large > v_small


# ── _nexus_regime_classify ───────────────────────────────────────────


def test_regime_crash_via_vix():
    assert _nexus_regime_classify(0.05, "bull", 45.0) == "crash"


def test_regime_crash_via_intraday_drop():
    assert _nexus_regime_classify(0.05, "bull", 25.0, spy_intraday_drop_pct=-0.10) == "crash"


def test_regime_bear_by_v31():
    assert _nexus_regime_classify(0.01, "bear", 25.0) == "bear"


def test_regime_bear_by_spy_drop():
    assert _nexus_regime_classify(-0.10, "bull", 25.0) == "bear"


def test_regime_bull():
    assert _nexus_regime_classify(0.03, "bull", 20.0) == "bull"


def test_regime_chop_default():
    assert _nexus_regime_classify(0.01, "neutral", 20.0) == "chop"


def test_regime_chop_when_spy_20d_just_above_zero_but_v31_neutral():
    # SPY barely positive (below the 2% bull threshold) → chop, not bull
    assert _nexus_regime_classify(0.005, "neutral", 20.0) == "chop"


def test_regime_crash_takes_precedence_over_bear():
    # VIX > 40 wins even when V31 says bear
    assert _nexus_regime_classify(-0.10, "bear", 50.0) == "crash"


def test_regime_handles_none_spy():
    # No SPY data with neutral v31 — defaults to chop, never crashes
    assert _nexus_regime_classify(None, "neutral", None) == "chop"


def test_regime_backcompat_none_spy_with_bull_v31_returns_bull():
    # Snapshot-loader backcompat: when SPY data is missing (cold start)
    # but V31 says bull, return bull instead of chop so live runs don't
    # silently halve max_positions on the first bar.
    assert _nexus_regime_classify(None, "bull", None) == "bull"


def test_regime_backcompat_does_not_override_bear_when_spy_none():
    # Even with V31=bear and SPY=None, we still respect bear
    assert _nexus_regime_classify(None, "bear", None) == "bear"


# ── _recent_closes_for_symbol ────────────────────────────────────────


def test_recent_closes_empty_when_no_data():
    assert _recent_closes_for_symbol("AMD", None) == []


def test_recent_closes_dict_format_with_close_key():
    ph = {"AMD": [{"c": 337.0}, {"c": 354.0}, {"c": 449.0}]}
    assert _recent_closes_for_symbol("AMD", ph) == [337.0, 354.0, 449.0]


def test_recent_closes_dict_format_with_close_word():
    ph = {"AMD": [{"close": 100.0}, {"close": 110.0}]}
    assert _recent_closes_for_symbol("AMD", ph) == [100.0, 110.0]


def test_recent_closes_filters_zero_and_negative():
    ph = {"AMD": [{"c": 100.0}, {"c": 0}, {"c": -5}, {"c": 110.0}]}
    assert _recent_closes_for_symbol("AMD", ph) == [100.0, 110.0]


def test_recent_closes_unknown_symbol_returns_empty():
    ph = {"AMD": [{"c": 100.0}]}
    assert _recent_closes_for_symbol("UNKNOWN", ph) == []


# ── _spy_20d_return ──────────────────────────────────────────────────


def test_spy_20d_no_cache_returns_none():
    assert _spy_20d_return(None, "2026-05-12") is None


def test_spy_20d_empty_cache_returns_none():
    assert _spy_20d_return({}, "2026-05-12") is None


def test_spy_20d_too_few_bars_returns_none():
    # Need at least 21 bars to compute 20-day return
    cache = {"_overlay_bars_raw": {"SPY": [{"t": "2026-05-01", "c": 700.0}] * 5}}
    assert _spy_20d_return(cache, "2026-05-12") is None


def test_spy_20d_returns_correct_value_with_enough_bars():
    # 21 SPY bars; first=700, last=735 → return = (735-700)/700 = 0.05
    bars = [{"t": f"2026-04-{i:02d}", "c": 700.0 + (i * 1.75)} for i in range(1, 22)]
    cache = {"_overlay_bars_raw": {"SPY": bars}}
    result = _spy_20d_return(cache, "2026-05-12")
    assert result is not None
    assert math.isclose(result, 0.05, abs_tol=0.001)


def test_spy_20d_falls_back_to_qqq_when_spy_missing():
    bars = [{"t": f"2026-04-{i:02d}", "c": 600.0 + i} for i in range(1, 22)]
    cache = {"_overlay_bars_raw": {"QQQ": bars}}
    assert _spy_20d_return(cache, "2026-05-12") is not None


def test_spy_20d_respects_date_cutoff():
    # Bars after date_key must NOT be included
    bars = (
        [{"t": f"2026-04-{i:02d}", "c": 700.0 + i} for i in range(1, 22)]
        + [{"t": "2026-12-31", "c": 99999.0}]  # far future — should be excluded
    )
    cache = {"_overlay_bars_raw": {"SPY": bars}}
    result = _spy_20d_return(cache, "2026-05-12")
    assert result is not None
    assert result < 1.0  # would be enormous if the future bar leaked in
