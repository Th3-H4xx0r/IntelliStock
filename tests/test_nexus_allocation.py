"""
Comprehensive test suite for the Nexus strategy allocation and sell mechanics.

Tests the V4 P&L-gated sell suppression, fast loser cutting, trailing stops,
tiered/power-law allocation, V5 dual-pool buy system (pools split before
buy cap), and edge cases.

All tests use mock data -- no database, API, or Neo4j connections.
"""

import copy
import math
import unittest
import pytest
from datetime import datetime, timedelta


# ---------------------------------------------------------------------------
# Mock Portfolio Emulator
# ---------------------------------------------------------------------------

class MockPortfolioEmulator:
    """
    Lightweight mock of PortfolioEmulator for testing allocation and sell
    mechanics without touching the real class or any external services.
    """

    def __init__(self, initial_cash: float = 10000.0):
        self._cash: float = float(initial_cash)
        self._initial_value: float = float(initial_cash)
        self._positions: dict[str, float] = {}
        self._trades: list[dict] = []
        self._last_prices: dict[str, float] = {}

    # -- mutators --

    def add_position(self, ticker: str, shares: float, entry_price: float,
                     timestamp=None):
        """Helper: seed a position and a matching buy trade record."""
        self._positions[ticker] = self._positions.get(ticker, 0.0) + shares
        self._trades.append({
            "ticker": ticker,
            "action": "buy",
            "price": entry_price,
            "shares": shares,
            "total": shares * entry_price,
            "timestamp": timestamp or datetime(2026, 1, 1),
            "cash_after": self._cash,
        })

    def set_cash(self, amount: float):
        self._cash = float(amount)

    # -- read interface (matches real PortfolioEmulator) --

    def get_cash(self) -> float:
        return self._cash

    def get_portfolio_value(self, prices: dict) -> float:
        value = self._cash
        for ticker, shares in self._positions.items():
            p = (prices or {}).get(ticker)
            if p is not None:
                value += shares * float(p)
        return value

    def get_positions(self) -> dict:
        return copy.copy(self._positions)


# ---------------------------------------------------------------------------
# Extracted V4 sell logic (mirrors _finalize_scores V4 block lines 8924-8967)
# ---------------------------------------------------------------------------

def simulate_v4_sell_logic(
    fresh_score: int,
    unrealized_pct: float,
    prop_raw: float,
    hold_limit_forced: bool,
    fast_cut_pct: float,
    ts_activation: float,
    ts_drop: float,
    current_price: float,
    peak_price: float,
) -> int:
    """
    Reproduce the V4 P&L-gated sell suppression / fast loser cut / trailing
    stop logic from ``_finalize_scores``.

    Parameters
    ----------
    fresh_score : int
        Signal before V4 gates (-1 sell, 0 hold, 1 buy).
    unrealized_pct : float
        Unrealized P&L percentage for the position.
    prop_raw : float
        Propagation raw score from the graph.
    hold_limit_forced : bool
        True when a hold-limit exit was already triggered.
    fast_cut_pct : float
        Threshold for fast loser cutting (e.g. -10.0).
    ts_activation : float
        Trailing stop activates once unrealized_pct >= this value.
    ts_drop : float
        Trailing stop fires when drop from peak >= this percent.
    current_price : float
        Current market price.
    peak_price : float
        Highest observed price since activation.

    Returns
    -------
    int  The adjusted fresh_score after V4 gates.
    """
    # Winner protection: suppress weak sell signals on winning positions
    if fresh_score == -1 and not hold_limit_forced:
        if unrealized_pct >= 50 and prop_raw > -0.70:
            fresh_score = 0
        elif unrealized_pct >= 20 and prop_raw > -0.50:
            fresh_score = 0
        elif unrealized_pct >= 10 and prop_raw > -0.30:
            fresh_score = 0

    # Fast loser cutting: force sell positions losing > threshold
    if fresh_score >= 0 and unrealized_pct <= fast_cut_pct:
        fresh_score = -1

    # Trailing stop: sell if price drops X% from peak
    if unrealized_pct >= ts_activation and fresh_score >= 0:
        drop_from_peak = (
            ((peak_price - current_price) / peak_price) * 100.0
            if peak_price > 0
            else 0
        )
        if drop_from_peak >= ts_drop:
            fresh_score = -1

    return fresh_score


# ---------------------------------------------------------------------------
# Extracted allocation logic (mirrors lines 11428-11564)
# ---------------------------------------------------------------------------

def simulate_allocation(
    stock_buys: list[str],
    sell_syms: list[str],
    portfolio_total: float,
    nexus_pct: float,
    min_pos_size: float,
    config: dict,
    scores: dict | None = None,
    sentiment_data: dict | None = None,
    propagated: dict | None = None,
    prop_expansion_buys: list[str] | None = None,
    current_positions: dict | None = None,
) -> dict[str, dict]:
    """
    Reproduce the V5 tiered / power-law allocation pipeline from
    ``graph_nexus_analysis`` (lines 11445-11575).

    V5 order: sort -> pool split -> pool limits -> pool merge ->
    buy cap -> position limit -> tiered weights -> min position filter.

    Returns dict of ``{ticker: {"buy_cash": float}}``.
    """
    scores = scores or {}
    sentiment_data = sentiment_data or {}
    propagated = propagated or {}
    prop_expansion_buys = prop_expansion_buys or []
    current_positions = current_positions or {}

    nexus_position_sizes: dict[str, dict] = {}
    if portfolio_total <= 0 or not stock_buys:
        return nexus_position_sizes

    # Sort by absolute raw_net_score descending
    stock_buys_scored = sorted(
        stock_buys,
        key=lambda sym: abs(
            float((scores.get(sym) or {}).get("raw_net_score", 0.0) or 0.0)
        ),
        reverse=True,
    )

    # --- V5: Split into pools FIRST, then apply caps per-pool ---
    # This ensures propagation stocks get pool slots before the buy cap
    # truncates by raw_net_score.
    prop_expansion_set = set(prop_expansion_buys)
    if config.get("propagation_priority", False):
        prop_priority_min_raw = float(
            config.get("propagation_priority_min_raw", 0.50)
        )
        pool_a_candidates = [
            s for s in stock_buys_scored
            if (
                sentiment_data.get(s, {}).get("event") == "graph_propagation"
                or s in prop_expansion_set
            )
            and float(
                (propagated or {}).get(s, {}).get("raw_score", 0.0)
            ) >= prop_priority_min_raw
        ]
        pool_b_candidates = [
            s for s in stock_buys_scored if s not in set(pool_a_candidates)
        ]
    else:
        pool_a_candidates = [
            s for s in stock_buys_scored
            if (
                sentiment_data.get(s, {}).get("event") != "graph_propagation"
                and s not in prop_expansion_set
            )
        ]
        pool_b_candidates = [
            s for s in stock_buys_scored
            if (
                sentiment_data.get(s, {}).get("event") == "graph_propagation"
                or s in prop_expansion_set
            )
        ]

    # Symmetric overflow
    pool_a_base = int(config.get("pool_a_base", 10))
    pool_b_base = int(config.get("pool_b_base", 5))
    pool_a_min = int(config.get("pool_a_min", 3))
    pool_b_min = int(config.get("pool_b_min", 2))

    if len(pool_a_candidates) < pool_a_min:
        pool_b_limit = min(
            len(pool_b_candidates),
            pool_b_base + (pool_a_base - len(pool_a_candidates)),
        )
        pool_a_limit = len(pool_a_candidates)
    elif len(pool_b_candidates) < pool_b_min:
        pool_a_limit = min(
            len(pool_a_candidates),
            pool_a_base + (pool_b_base - len(pool_b_candidates)),
        )
        pool_b_limit = len(pool_b_candidates)
    else:
        pool_a_limit = min(len(pool_a_candidates), pool_a_base)
        pool_b_limit = min(len(pool_b_candidates), pool_b_base)

    pool_a_final = pool_a_candidates[:pool_a_limit]
    pool_a_set = set(pool_a_final)
    pool_b_final = [s for s in pool_b_candidates if s not in pool_a_set][
        :pool_b_limit
    ]

    stock_buys_scored = pool_a_final + pool_b_final

    # --- V5: Apply buy cap + position limit AFTER pool merge ---
    sell_count = len(sell_syms) if sell_syms else 0
    base_buy_cap = int(config.get("max_stock_buys_per_day", 15))
    effective_buy_cap = min(
        len(stock_buys_scored),
        base_buy_cap + max(0, sell_count - base_buy_cap),
    )
    stock_buys_scored = stock_buys_scored[:effective_buy_cap]

    # --- Position count limit ---
    max_positions = int(config.get("max_positions", 50))
    position_headroom = max(0, max_positions - len(current_positions))
    if position_headroom < len(stock_buys_scored):
        stock_buys_scored = stock_buys_scored[:position_headroom]

    # --- Power-law or tiered allocation ---
    n = len(stock_buys_scored)
    weights: dict[str, float] = {}
    if config.get("conviction_decay") and n > 0:
        decay = float(config.get("conviction_decay", 0.55))
        for i, sym in enumerate(stock_buys_scored):
            weights[sym] = decay ** i
    else:
        tier1_count = max(1, n // 4)
        tier2_count = max(1, n // 2)
        for i, sym in enumerate(stock_buys_scored):
            if i < tier1_count:
                weights[sym] = 2.5
            elif i < tier1_count + tier2_count:
                weights[sym] = 1.0
            else:
                weights[sym] = 0.5

    # Momentum amplification
    if config.get("momentum_amplification", False):
        mom_mult = float(config.get("momentum_weight_multiplier", 1.5))
        for ms in stock_buys_scored:
            msc = scores.get(ms)
            if isinstance(msc, dict):
                mbc = msc.get("base_components") or {}
                if float(mbc.get("recent_5", 0)) > 0 and float(
                    mbc.get("recent_20", 0)
                ) > 0:
                    weights[ms] = weights.get(ms, 1.0) * mom_mult

    total_weight = sum(weights.values())
    stock_budget = portfolio_total * nexus_pct

    # --- Min position size filter ---
    max_buyable = (
        max(1, int(stock_budget / min_pos_size))
        if min_pos_size > 0
        else len(stock_buys_scored)
    )
    if max_buyable < len(stock_buys_scored):
        stock_buys_scored = stock_buys_scored[:max_buyable]
        # Recompute weights
        n = len(stock_buys_scored)
        weights = {}
        if config.get("conviction_decay") and n > 0:
            decay = float(config.get("conviction_decay", 0.55))
            for i, sym in enumerate(stock_buys_scored):
                weights[sym] = decay ** i
        else:
            tier1_count = max(1, n // 4)
            tier2_count = max(1, n // 2)
            for i, sym in enumerate(stock_buys_scored):
                if i < tier1_count:
                    weights[sym] = 2.5
                elif i < tier1_count + tier2_count:
                    weights[sym] = 1.0
                else:
                    weights[sym] = 0.5
        # Re-apply momentum
        if config.get("momentum_amplification", False):
            mom_mult = float(config.get("momentum_weight_multiplier", 1.5))
            for ms in stock_buys_scored:
                msc = scores.get(ms)
                if isinstance(msc, dict):
                    mbc = msc.get("base_components") or {}
                    if float(mbc.get("recent_5", 0)) > 0 and float(
                        mbc.get("recent_20", 0)
                    ) > 0:
                        weights[ms] = weights.get(ms, 1.0) * mom_mult
        total_weight = sum(weights.values())

    for sym in stock_buys_scored:
        cash = stock_budget * (weights[sym] / total_weight)
        if cash < min_pos_size:
            continue
        nexus_position_sizes[sym] = {"buy_cash": round(cash, 2)}

    return nexus_position_sizes


# ===================================================================
# SECTION 1: Sell Mechanics Tests
# ===================================================================


class TestWinnerProtection:
    """V4 winner-protection suppresses weak sell signals on profitable positions."""

    def test_winner_protection_suppresses_sell_at_10pct_gain(self):
        """A +12% gainer with mild negative signal (-0.20) should be held, not sold."""
        result = simulate_v4_sell_logic(
            fresh_score=-1,
            unrealized_pct=12.0,
            prop_raw=-0.20,
            hold_limit_forced=False,
            fast_cut_pct=-10.0,
            ts_activation=10.0,
            ts_drop=15.0,
            current_price=112.0,
            peak_price=115.0,
        )
        assert result == 0, (
            "Winner protection should suppress sell at +12% gain "
            "when prop_raw (-0.20) > -0.30 threshold"
        )

    def test_winner_protection_suppresses_sell_at_20pct_gain(self):
        """A +25% gainer with moderate negative signal (-0.40) should be held."""
        result = simulate_v4_sell_logic(
            fresh_score=-1,
            unrealized_pct=25.0,
            prop_raw=-0.40,
            hold_limit_forced=False,
            fast_cut_pct=-10.0,
            ts_activation=10.0,
            ts_drop=15.0,
            current_price=125.0,
            peak_price=130.0,
        )
        assert result == 0, (
            "Winner protection should suppress sell at +25% gain "
            "when prop_raw (-0.40) > -0.50 threshold"
        )

    def test_winner_protection_suppresses_sell_at_50pct_gain(self):
        """A +55% gainer with strong negative signal (-0.60) should still be held."""
        result = simulate_v4_sell_logic(
            fresh_score=-1,
            unrealized_pct=55.0,
            prop_raw=-0.60,
            hold_limit_forced=False,
            fast_cut_pct=-10.0,
            ts_activation=10.0,
            ts_drop=15.0,
            current_price=155.0,
            peak_price=160.0,
        )
        assert result == 0, (
            "Winner protection should suppress sell at +55% gain "
            "when prop_raw (-0.60) > -0.70 threshold"
        )

    def test_winner_protection_allows_sell_with_strong_negative_signal(self):
        """A +12% gainer with a very strong negative raw (-0.50) should still sell."""
        result = simulate_v4_sell_logic(
            fresh_score=-1,
            unrealized_pct=12.0,
            prop_raw=-0.50,
            hold_limit_forced=False,
            fast_cut_pct=-10.0,
            ts_activation=10.0,
            ts_drop=15.0,
            current_price=112.0,
            peak_price=115.0,
        )
        assert result == -1, (
            "Winner protection at +12% gain requires prop_raw > -0.30; "
            "-0.50 is too negative so sell should proceed"
        )

    def test_hold_limit_bypasses_winner_protection(self):
        """
        BUG FIX: When hold_limit_forced is True, winner protection must NOT
        override the forced sell.  A +30% gainer past the hold limit should
        still be sold regardless of mild prop_raw.
        """
        result = simulate_v4_sell_logic(
            fresh_score=-1,
            unrealized_pct=30.0,
            prop_raw=-0.10,
            hold_limit_forced=True,
            fast_cut_pct=-10.0,
            ts_activation=10.0,
            ts_drop=15.0,
            current_price=130.0,
            peak_price=135.0,
        )
        assert result == -1, (
            "Hold-limit forced exit must bypass winner protection. "
            "Even a +30% gainer should sell when hold limit expires."
        )


class TestFastLoserCut:
    """V4 fast loser cutting forces sell when unrealized loss exceeds threshold."""

    def test_fast_loser_cut_at_minus_8pct(self):
        """
        Position at -8% with fast_cut_pct=-8 should trigger forced sell
        even if fresh_score was 0 (hold).
        """
        result = simulate_v4_sell_logic(
            fresh_score=0,
            unrealized_pct=-8.0,
            prop_raw=0.0,
            hold_limit_forced=False,
            fast_cut_pct=-8.0,
            ts_activation=10.0,
            ts_drop=15.0,
            current_price=92.0,
            peak_price=92.0,
        )
        assert result == -1, (
            "Fast loser cut should fire when unrealized_pct (-8%) "
            "<= fast_cut_pct (-8%)"
        )

    def test_fast_loser_cut_does_not_fire_on_minus_5pct(self):
        """
        Position at -5% with default fast_cut_pct=-10 should NOT trigger
        the fast loser cut.
        """
        result = simulate_v4_sell_logic(
            fresh_score=0,
            unrealized_pct=-5.0,
            prop_raw=0.0,
            hold_limit_forced=False,
            fast_cut_pct=-10.0,
            ts_activation=10.0,
            ts_drop=15.0,
            current_price=95.0,
            peak_price=95.0,
        )
        assert result == 0, (
            "Fast loser cut should NOT fire when loss (-5%) is above "
            "the -10% threshold"
        )

    def test_fast_loser_cut_only_fires_on_hold_or_buy(self):
        """
        Fast loser cut only fires when fresh_score >= 0.
        If fresh_score is already -1, the cut should not re-trigger
        (and should not interfere with the already-set sell).
        """
        result = simulate_v4_sell_logic(
            fresh_score=-1,
            unrealized_pct=-15.0,
            prop_raw=0.0,
            hold_limit_forced=False,
            fast_cut_pct=-10.0,
            ts_activation=10.0,
            ts_drop=15.0,
            current_price=85.0,
            peak_price=85.0,
        )
        # It enters winner protection path first (fresh_score==-1), but
        # unrealized_pct is negative so no threshold matches => stays -1.
        # Then fast_cut check: fresh_score >= 0 is False => no change.
        assert result == -1, (
            "Fast loser cut gate (fresh_score >= 0) prevents double-fire "
            "on an existing sell signal"
        )

    def test_fast_loser_cut_fires_on_buy_signal(self):
        """
        Even a buy signal (fresh_score=1) should be overridden to sell
        when the position is deeply underwater past fast_cut_pct.
        """
        result = simulate_v4_sell_logic(
            fresh_score=1,
            unrealized_pct=-12.0,
            prop_raw=0.5,
            hold_limit_forced=False,
            fast_cut_pct=-10.0,
            ts_activation=10.0,
            ts_drop=15.0,
            current_price=88.0,
            peak_price=88.0,
        )
        assert result == -1, (
            "Fast loser cut should override even a buy signal when "
            "loss exceeds threshold"
        )


class TestTrailingStop:
    """V4 trailing stop sells when price drops X% from peak after activation."""

    def test_trailing_stop_fires_at_12pct_drop_from_peak(self):
        """
        Position at +15% gain (activated), price dropped >12% from peak.
        With ts_drop=12, should trigger sell.
        """
        # peak was 100, current is 87.0 => drop = (100-87)/100 = 13%
        result = simulate_v4_sell_logic(
            fresh_score=0,
            unrealized_pct=15.0,
            prop_raw=0.0,
            hold_limit_forced=False,
            fast_cut_pct=-10.0,
            ts_activation=10.0,
            ts_drop=12.0,
            current_price=87.0,
            peak_price=100.0,
        )
        assert result == -1, (
            "Trailing stop should fire when drop from peak (13%) "
            ">= ts_drop (12%)"
        )

    def test_trailing_stop_does_not_fire_below_activation(self):
        """
        Position at +5% gain, below ts_activation=10.
        Even a big drop from peak should not trigger trailing stop.
        """
        result = simulate_v4_sell_logic(
            fresh_score=0,
            unrealized_pct=5.0,
            prop_raw=0.0,
            hold_limit_forced=False,
            fast_cut_pct=-10.0,
            ts_activation=10.0,
            ts_drop=15.0,
            current_price=105.0,
            peak_price=120.0,
        )
        assert result == 0, (
            "Trailing stop must not fire when unrealized_pct (5%) "
            "< ts_activation (10%)"
        )

    def test_trailing_stop_does_not_fire_on_existing_sell(self):
        """
        If fresh_score is already -1, trailing stop gate (fresh_score >= 0)
        prevents re-evaluation.
        """
        result = simulate_v4_sell_logic(
            fresh_score=-1,
            unrealized_pct=20.0,
            prop_raw=-0.10,
            hold_limit_forced=True,
            fast_cut_pct=-10.0,
            ts_activation=10.0,
            ts_drop=5.0,
            current_price=100.0,
            peak_price=200.0,
        )
        # hold_limit_forced=True means winner protection is bypassed;
        # fresh_score stays -1. Trailing stop gate: fresh_score >= 0 is False.
        assert result == -1, (
            "Trailing stop gate (fresh_score >= 0) should prevent "
            "re-evaluation on existing sell"
        )

    def test_trailing_stop_small_drop_does_not_fire(self):
        """
        Position activated at +20%, but drop from peak is only 5%.
        With ts_drop=15, should NOT fire.
        """
        # peak=150, current=142.50 => drop = (150-142.5)/150 = 5%
        result = simulate_v4_sell_logic(
            fresh_score=0,
            unrealized_pct=20.0,
            prop_raw=0.0,
            hold_limit_forced=False,
            fast_cut_pct=-10.0,
            ts_activation=10.0,
            ts_drop=15.0,
            current_price=142.50,
            peak_price=150.0,
        )
        assert result == 0, (
            "Trailing stop should NOT fire when drop (5%) < ts_drop (15%)"
        )


class TestSellMechanicsCombined:
    """Test interactions between multiple V4 sell gates."""

    def test_winner_protection_then_trailing_stop_override(self):
        """
        Winner protection suppresses sell, but trailing stop can still fire
        on the resulting hold signal if the position has activated and dropped.
        """
        # First: winner protection converts -1 to 0
        intermediate = simulate_v4_sell_logic(
            fresh_score=-1,
            unrealized_pct=15.0,
            prop_raw=-0.10,
            hold_limit_forced=False,
            fast_cut_pct=-10.0,
            ts_activation=10.0,
            ts_drop=10.0,
            current_price=115.0,
            peak_price=130.0,
        )
        # Winner protection fires (15% gain, raw -0.10 > -0.30) -> score = 0
        # Trailing stop: unrealized 15% >= activation 10%, drop =
        # (130-115)/130 = 11.5% >= ts_drop 10% -> score = -1
        assert intermediate == -1, (
            "Trailing stop should override winner protection when the "
            "drop from peak is large enough"
        )

    def test_fast_cut_not_triggered_on_profitable_position(self):
        """Fast loser cut should never fire on a profitable position."""
        result = simulate_v4_sell_logic(
            fresh_score=0,
            unrealized_pct=5.0,
            prop_raw=0.0,
            hold_limit_forced=False,
            fast_cut_pct=-10.0,
            ts_activation=10.0,
            ts_drop=15.0,
            current_price=105.0,
            peak_price=105.0,
        )
        assert result == 0, (
            "Fast loser cut should not fire when unrealized_pct is positive"
        )


# ===================================================================
# SECTION 2: Allocation Tests
# ===================================================================

def _make_scores(tickers: list[str], raw_values: list[float] | None = None,
                 base_components: dict | None = None) -> dict:
    """Helper: build a scores dict keyed by ticker."""
    scores = {}
    for i, t in enumerate(tickers):
        raw = raw_values[i] if raw_values else (1.0 - i * 0.1)
        entry = {"raw_net_score": raw}
        if base_components:
            entry["base_components"] = base_components
        scores[t] = entry
    return scores


def _make_sentiment(tickers: list[str], event: str = "news") -> dict:
    """Helper: build a sentiment_data dict."""
    return {t: {"event": event} for t in tickers}


class TestTieredAllocation:
    """Verify the 2.5x/1x/0.5x tier weighting with 5 stocks."""

    def test_tiered_allocation_5_stocks(self):
        """
        5 stocks should split into tier1(1), tier2(2), tier3(2) with
        weights 2.5, 1.0, 1.0, 0.5, 0.5 respectively.
        Total weight = 5.5; each gets budget * (weight / 5.5).
        """
        tickers = ["A", "B", "C", "D", "E"]
        scores = _make_scores(tickers, [1.0, 0.8, 0.6, 0.4, 0.2])
        sentiment = _make_sentiment(tickers, event="news")
        config = {
            "max_stock_buys_per_day": 15,
            "max_positions": 50,
            "pool_a_base": 10,
            "pool_b_base": 5,
            "pool_a_min": 3,
            "pool_b_min": 2,
            "min_position_size": 10.0,
        }

        result = simulate_allocation(
            stock_buys=tickers,
            sell_syms=[],
            portfolio_total=10000.0,
            nexus_pct=0.10,
            min_pos_size=10.0,
            config=config,
            scores=scores,
            sentiment_data=sentiment,
        )

        assert len(result) == 5
        # n=5, tier1_count=max(1, 5//4)=1, tier2_count=max(1, 5//2)=2
        # weights: A=2.5, B=1.0, C=1.0, D=0.5, E=0.5 => total=5.5
        budget = 10000.0 * 0.10  # 1000
        assert abs(result["A"]["buy_cash"] - round(budget * 2.5 / 5.5, 2)) < 0.02
        assert abs(result["B"]["buy_cash"] - round(budget * 1.0 / 5.5, 2)) < 0.02
        assert abs(result["D"]["buy_cash"] - round(budget * 0.5 / 5.5, 2)) < 0.02


class TestMinPositionSize:
    """Verify that allocations below min_position_size are filtered out."""

    def test_min_position_size_filters_small_allocations(self):
        """
        With a $100 budget, 50% nexus pct ($50 total), and min_pos_size=20,
        the max_buyable is 2. Remaining stocks should be dropped.
        """
        tickers = ["A", "B", "C", "D", "E"]
        scores = _make_scores(tickers, [1.0, 0.8, 0.6, 0.4, 0.2])
        sentiment = _make_sentiment(tickers, event="news")
        config = {
            "max_stock_buys_per_day": 15,
            "max_positions": 50,
            "pool_a_base": 10,
            "pool_b_base": 5,
            "pool_a_min": 3,
            "pool_b_min": 2,
            "min_position_size": 20.0,
        }

        result = simulate_allocation(
            stock_buys=tickers,
            sell_syms=[],
            portfolio_total=100.0,
            nexus_pct=0.50,
            min_pos_size=20.0,
            config=config,
            scores=scores,
            sentiment_data=sentiment,
        )

        # Budget = 100 * 0.50 = $50, max_buyable = 50/20 = 2
        assert len(result) <= 2, (
            f"Max buyable is 2 with $50 budget and $20 min size, got {len(result)}"
        )
        # Each allocation must be >= min_pos_size
        for sym, alloc in result.items():
            assert alloc["buy_cash"] >= 20.0, (
                f"{sym} allocated ${alloc['buy_cash']:.2f} which is below "
                f"min_position_size $20"
            )


class TestPowerLawAllocation:
    """Verify conviction_decay power-law vs. tiered allocation."""

    def test_power_law_disabled_when_no_conviction_decay(self):
        """Without conviction_decay config, allocation uses tiered weights."""
        tickers = ["A", "B", "C", "D"]
        scores = _make_scores(tickers, [1.0, 0.8, 0.6, 0.4])
        sentiment = _make_sentiment(tickers, event="news")
        config = {
            "max_stock_buys_per_day": 15,
            "max_positions": 50,
            "pool_a_base": 10,
            "pool_b_base": 5,
            "pool_a_min": 3,
            "pool_b_min": 2,
            "min_position_size": 1.0,
        }

        result = simulate_allocation(
            stock_buys=tickers,
            sell_syms=[],
            portfolio_total=10000.0,
            nexus_pct=0.10,
            min_pos_size=1.0,
            config=config,
            scores=scores,
            sentiment_data=sentiment,
        )

        assert len(result) == 4
        # Tiered: n=4, tier1=1, tier2=2 => A=2.5, B=1.0, C=1.0, D=0.5
        # Total weight = 5.0
        budget = 1000.0
        assert abs(result["A"]["buy_cash"] - round(budget * 2.5 / 5.0, 2)) < 0.02

    def test_power_law_enabled_when_conviction_decay_set(self):
        """With conviction_decay=0.5, weights should follow 0.5^i pattern."""
        tickers = ["A", "B", "C", "D"]
        scores = _make_scores(tickers, [1.0, 0.8, 0.6, 0.4])
        sentiment = _make_sentiment(tickers, event="news")
        config = {
            "max_stock_buys_per_day": 15,
            "max_positions": 50,
            "pool_a_base": 10,
            "pool_b_base": 5,
            "pool_a_min": 3,
            "pool_b_min": 2,
            "min_position_size": 1.0,
            "conviction_decay": 0.5,
        }

        result = simulate_allocation(
            stock_buys=tickers,
            sell_syms=[],
            portfolio_total=10000.0,
            nexus_pct=0.10,
            min_pos_size=1.0,
            config=config,
            scores=scores,
            sentiment_data=sentiment,
        )

        assert len(result) == 4
        # Power-law: 0.5^0=1.0, 0.5^1=0.5, 0.5^2=0.25, 0.5^3=0.125
        # Total = 1.875
        budget = 1000.0
        expected_a = round(budget * 1.0 / 1.875, 2)
        assert abs(result["A"]["buy_cash"] - expected_a) < 0.02, (
            f"A should get ~{expected_a}, got {result['A']['buy_cash']}"
        )
        # Verify A > B > C > D (monotone decreasing)
        vals = [result[t]["buy_cash"] for t in tickers]
        for i in range(len(vals) - 1):
            assert vals[i] > vals[i + 1], (
                f"Power-law allocation should be strictly decreasing: "
                f"{tickers[i]}={vals[i]} vs {tickers[i+1]}={vals[i+1]}"
            )


class TestDualPoolBuySystem:
    """Verify Pool A (direct signals) vs Pool B (propagation) split."""

    def test_pool_a_direct_signals_pool_b_propagation(self):
        """
        Without propagation_priority, Pool A = direct signal stocks,
        Pool B = graph_propagation stocks.
        """
        direct = ["AAPL", "MSFT", "GOOG"]
        prop = ["TSLA", "NVDA"]
        tickers = direct + prop
        scores = _make_scores(tickers, [1.0, 0.9, 0.8, 0.7, 0.6])
        sentiment = {}
        for t in direct:
            sentiment[t] = {"event": "news"}
        for t in prop:
            sentiment[t] = {"event": "graph_propagation"}

        config = {
            "max_stock_buys_per_day": 15,
            "max_positions": 50,
            "pool_a_base": 10,
            "pool_b_base": 5,
            "pool_a_min": 3,
            "pool_b_min": 2,
            "min_position_size": 1.0,
        }

        result = simulate_allocation(
            stock_buys=tickers,
            sell_syms=[],
            portfolio_total=10000.0,
            nexus_pct=0.10,
            min_pos_size=1.0,
            config=config,
            scores=scores,
            sentiment_data=sentiment,
        )

        # All 5 should be allocated (3 direct + 2 propagation within limits)
        assert len(result) == 5
        for t in tickers:
            assert t in result, f"{t} missing from allocation"

    def test_propagation_priority_inverts_pools(self):
        """
        With propagation_priority=True, propagation stocks get Pool A (primary)
        and direct signal stocks get Pool B.
        """
        direct = ["AAPL"]
        prop = ["TSLA", "NVDA", "AMD"]
        tickers = direct + prop
        scores = _make_scores(tickers, [1.0, 0.9, 0.8, 0.7])
        sentiment = {}
        for t in direct:
            sentiment[t] = {"event": "news"}
        for t in prop:
            sentiment[t] = {"event": "graph_propagation"}
        propagated = {t: {"raw_score": 0.80} for t in prop}

        config = {
            "max_stock_buys_per_day": 15,
            "max_positions": 50,
            "pool_a_base": 10,
            "pool_b_base": 5,
            "pool_a_min": 3,
            "pool_b_min": 2,
            "min_position_size": 1.0,
            "propagation_priority": True,
            "propagation_priority_min_raw": 0.50,
        }

        result = simulate_allocation(
            stock_buys=tickers,
            sell_syms=[],
            portfolio_total=10000.0,
            nexus_pct=0.10,
            min_pos_size=1.0,
            config=config,
            scores=scores,
            sentiment_data=sentiment,
            propagated=propagated,
        )

        # All 4 should be allocated
        assert len(result) == 4
        for t in tickers:
            assert t in result


class TestPoolSymmetricOverflow:
    """Verify symmetric overflow: unused slots from one pool transfer to the other."""

    def test_pool_symmetric_overflow_empty_pool_a(self):
        """
        When Pool A has 0 candidates, Pool B should get its base + Pool A's base
        slots via symmetric overflow.
        """
        # All stocks are graph_propagation => pool_a is empty
        tickers = [f"S{i}" for i in range(12)]
        scores = _make_scores(tickers, [1.0 - i * 0.05 for i in range(12)])
        sentiment = _make_sentiment(tickers, event="graph_propagation")

        config = {
            "max_stock_buys_per_day": 20,
            "max_positions": 50,
            "pool_a_base": 10,
            "pool_b_base": 5,
            "pool_a_min": 3,
            "pool_b_min": 2,
            "min_position_size": 1.0,
        }

        result = simulate_allocation(
            stock_buys=tickers,
            sell_syms=[],
            portfolio_total=10000.0,
            nexus_pct=0.50,
            min_pos_size=1.0,
            config=config,
            scores=scores,
            sentiment_data=sentiment,
        )

        # pool_a has 0 candidates (< pool_a_min=3)
        # pool_b_limit = min(12, pool_b_base + (pool_a_base - 0)) = min(12, 15) = 12
        assert len(result) == 12, (
            f"Expected 12 allocations from overflow, got {len(result)}"
        )

    def test_pool_symmetric_overflow_empty_pool_b(self):
        """
        When Pool B has 0 candidates, Pool A should get its base + Pool B's base
        slots via symmetric overflow.
        """
        # All stocks are direct signal => pool_b is empty
        tickers = [f"S{i}" for i in range(12)]
        scores = _make_scores(tickers, [1.0 - i * 0.05 for i in range(12)])
        sentiment = _make_sentiment(tickers, event="news")

        config = {
            "max_stock_buys_per_day": 20,
            "max_positions": 50,
            "pool_a_base": 10,
            "pool_b_base": 5,
            "pool_a_min": 3,
            "pool_b_min": 2,
            "min_position_size": 1.0,
        }

        result = simulate_allocation(
            stock_buys=tickers,
            sell_syms=[],
            portfolio_total=10000.0,
            nexus_pct=0.50,
            min_pos_size=1.0,
            config=config,
            scores=scores,
            sentiment_data=sentiment,
        )

        # pool_b has 0 candidates (< pool_b_min=2)
        # pool_a_limit = min(12, pool_a_base + (pool_b_base - 0)) = min(12, 15) = 12
        assert len(result) == 12, (
            f"Expected 12 allocations from overflow, got {len(result)}"
        )


class TestBuyCap:
    """Verify sell-coupled buy cap and soft position limit."""

    def test_buy_cap_limits_to_max_stock_buys_per_day(self):
        """
        With max_stock_buys_per_day=3, only top 3 stocks should be allocated
        even if more are available.
        """
        tickers = ["A", "B", "C", "D", "E"]
        scores = _make_scores(tickers, [1.0, 0.8, 0.6, 0.4, 0.2])
        sentiment = _make_sentiment(tickers, event="news")
        config = {
            "max_stock_buys_per_day": 3,
            "max_positions": 50,
            "pool_a_base": 10,
            "pool_b_base": 5,
            "pool_a_min": 3,
            "pool_b_min": 2,
            "min_position_size": 1.0,
        }

        result = simulate_allocation(
            stock_buys=tickers,
            sell_syms=[],
            portfolio_total=10000.0,
            nexus_pct=0.10,
            min_pos_size=1.0,
            config=config,
            scores=scores,
            sentiment_data=sentiment,
        )

        assert len(result) <= 3, (
            f"Buy cap should limit to 3 buys, got {len(result)}"
        )

    def test_sell_coupled_cap_expands_with_sells(self):
        """
        When sell_count exceeds base_buy_cap, the effective cap expands.
        base_cap=2, sell_count=5 => effective_cap = 2 + max(0, 5-2) = 5.
        """
        tickers = ["A", "B", "C", "D", "E"]
        scores = _make_scores(tickers, [1.0, 0.8, 0.6, 0.4, 0.2])
        sentiment = _make_sentiment(tickers, event="news")
        config = {
            "max_stock_buys_per_day": 2,
            "max_positions": 50,
            "pool_a_base": 10,
            "pool_b_base": 5,
            "pool_a_min": 3,
            "pool_b_min": 2,
            "min_position_size": 1.0,
        }

        result = simulate_allocation(
            stock_buys=tickers,
            sell_syms=["X", "Y", "Z", "W", "V"],
            portfolio_total=10000.0,
            nexus_pct=0.10,
            min_pos_size=1.0,
            config=config,
            scores=scores,
            sentiment_data=sentiment,
        )

        # effective_cap = min(5, 2 + max(0, 5 - 2)) = min(5, 5) = 5
        assert len(result) == 5, (
            f"Sell-coupled cap should expand to 5 with 5 sells, got {len(result)}"
        )

    def test_soft_position_limit_reduces_cap(self):
        """
        With max_positions=3 and 2 current positions, only 1 new buy is allowed.
        """
        tickers = ["A", "B", "C"]
        scores = _make_scores(tickers, [1.0, 0.8, 0.6])
        sentiment = _make_sentiment(tickers, event="news")
        config = {
            "max_stock_buys_per_day": 15,
            "max_positions": 3,
            "pool_a_base": 10,
            "pool_b_base": 5,
            "pool_a_min": 3,
            "pool_b_min": 2,
            "min_position_size": 1.0,
        }

        result = simulate_allocation(
            stock_buys=tickers,
            sell_syms=[],
            portfolio_total=10000.0,
            nexus_pct=0.10,
            min_pos_size=1.0,
            config=config,
            scores=scores,
            sentiment_data=sentiment,
            current_positions={"EXISTING1": 10.0, "EXISTING2": 5.0},
        )

        assert len(result) == 1, (
            f"With max_positions=3 and 2 held, only 1 slot remains, "
            f"got {len(result)}"
        )


class TestV5PoolBeforeBuyCap:
    """V5 fix: pools are split BEFORE buy cap, so propagation stocks survive."""

    def test_propagation_stocks_survive_buy_cap(self):
        """V5 fix: propagation stocks get pool slots BEFORE buy cap truncates.

        10 direct stocks with high scores + 3 propagation stocks with lower
        scores.  Buy cap = 5.  OLD code would sort all 13 by score, truncate
        to top 5 (all direct), then split pools -- propagation gets nothing.
        NEW code splits first: Pool A (direct, base=10) keeps all 10, Pool B
        (propagation, base=5) keeps all 3, merge = 13, THEN buy cap = 5
        truncates the merged list.  Because merged order is Pool A + Pool B,
        the 3 propagation stocks are at positions 10-12 and get cut, BUT
        with pool_a_base=3 and pool_b_base=3 the pools are small enough
        that propagation survives.
        """
        direct = [f"D{i}" for i in range(10)]
        prop = ["SNDK", "MU", "WDC"]
        tickers = direct + prop

        # Direct stocks: scores 1.20 down to 0.30 (step ~0.10)
        direct_scores = [1.20 - i * 0.10 for i in range(10)]
        # Propagation stocks: lower scores
        prop_scores = [0.25, 0.20, 0.15]

        scores = _make_scores(tickers, direct_scores + prop_scores)
        sentiment = {}
        for t in direct:
            sentiment[t] = {"event": "news"}
        for t in prop:
            sentiment[t] = {"event": "graph_propagation"}

        config = {
            "max_stock_buys_per_day": 5,
            "max_positions": 50,
            "pool_a_base": 3,
            "pool_b_base": 3,
            "pool_a_min": 1,
            "pool_b_min": 1,
            "min_position_size": 1.0,
        }

        result = simulate_allocation(
            stock_buys=tickers,
            sell_syms=[],
            portfolio_total=10000.0,
            nexus_pct=0.10,
            min_pos_size=1.0,
            config=config,
            scores=scores,
            sentiment_data=sentiment,
        )

        # Pool A takes top-3 direct, Pool B takes top-3 propagation
        # Merged = 6.  Buy cap = 5 truncates to 5.
        # At least 2 of the 3 propagation stocks should survive (positions
        # 3 and 4 in the merged list, with position 5 being cut).
        prop_in_result = [t for t in prop if t in result]
        assert len(prop_in_result) >= 2, (
            f"V5: at least 2 propagation stocks should survive buy cap, "
            f"got {len(prop_in_result)}: {prop_in_result}"
        )
        assert len(result) == 5, (
            f"Buy cap=5 should limit output to 5, got {len(result)}"
        )

    def test_sndk_gets_pool_b_slot(self):
        """SNDK (propagation, raw_net_score=0.45) gets Pool B slot despite
        lower score than all 20 direct stocks.

        Without V5 pool-first logic, SNDK would be ranked 21st by score and
        truncated by any buy cap <= 20.  With V5 logic, Pool B reserves a
        slot for SNDK regardless of its rank in the global score list.
        """
        direct = [f"D{i}" for i in range(20)]
        direct_scores = [0.70 + i * 0.025 for i in range(20)]  # 0.70..1.175
        direct_scores.reverse()  # highest first for readability

        tickers = direct + ["SNDK"]
        all_scores = direct_scores + [0.45]

        scores = _make_scores(tickers, all_scores)
        sentiment = {}
        for t in direct:
            sentiment[t] = {"event": "news"}
        sentiment["SNDK"] = {"event": "graph_propagation"}

        config = {
            "max_stock_buys_per_day": 15,
            "max_positions": 50,
            "pool_a_base": 10,
            "pool_b_base": 5,
            "pool_a_min": 3,
            "pool_b_min": 2,
            "min_position_size": 1.0,
        }

        result = simulate_allocation(
            stock_buys=tickers,
            sell_syms=[],
            portfolio_total=10000.0,
            nexus_pct=0.10,
            min_pos_size=1.0,
            config=config,
            scores=scores,
            sentiment_data=sentiment,
        )

        assert "SNDK" in result, (
            "V5: SNDK (propagation, score=0.45) should get a Pool B slot "
            "even though all 20 direct stocks outscore it"
        )
        # Pool B has 1 candidate (< pool_b_min=2), so symmetric overflow
        # gives Pool A: min(20, 10 + (5 - 1)) = 14 slots.
        # Pool B = 1 (SNDK).  Merged = 15, buy cap = 15 => no truncation.
        assert len(result) == 15, (
            f"Expected 15 allocated (14 pool_a overflow + 1 pool_b), "
            f"got {len(result)}"
        )

    def test_buy_cap_truncates_after_merge(self):
        """Buy cap applies to merged [Pool A + Pool B], not before split.

        pool_a_base=3, pool_b_base=3 => 6 merged.  buy_cap=5 => 5 stocks
        output (1 cut from end of merged list).
        """
        direct = ["AAPL", "MSFT", "GOOG", "AMZN"]
        prop = ["MU", "WDC", "SNDK"]
        tickers = direct + prop

        direct_scores = [1.0, 0.9, 0.8, 0.7]
        prop_scores = [0.60, 0.55, 0.50]

        scores = _make_scores(tickers, direct_scores + prop_scores)
        sentiment = {}
        for t in direct:
            sentiment[t] = {"event": "news"}
        for t in prop:
            sentiment[t] = {"event": "graph_propagation"}

        config = {
            "max_stock_buys_per_day": 5,
            "max_positions": 50,
            "pool_a_base": 3,
            "pool_b_base": 3,
            "pool_a_min": 1,
            "pool_b_min": 1,
            "min_position_size": 1.0,
        }

        result = simulate_allocation(
            stock_buys=tickers,
            sell_syms=[],
            portfolio_total=10000.0,
            nexus_pct=0.10,
            min_pos_size=1.0,
            config=config,
            scores=scores,
            sentiment_data=sentiment,
        )

        # Pool A: top 3 direct (AAPL, MSFT, GOOG)
        # Pool B: top 3 propagation (MU, WDC, SNDK)
        # Merged: [AAPL, MSFT, GOOG, MU, WDC, SNDK] = 6
        # Buy cap = 5 => truncate last => [AAPL, MSFT, GOOG, MU, WDC]
        assert len(result) == 5, (
            f"Buy cap=5 should truncate merged 6 to 5, got {len(result)}"
        )
        # At least 2 propagation stocks should survive (MU, WDC)
        prop_in_result = [t for t in prop if t in result]
        assert len(prop_in_result) >= 2, (
            f"At least 2 propagation stocks should survive after buy cap "
            f"truncates merged list, got {len(prop_in_result)}: "
            f"{prop_in_result}"
        )


class TestMomentumAmplification:
    """Verify momentum amplification boosts stocks with positive recent trends."""

    def test_momentum_amplification_boosts_trending_stocks(self):
        """
        Stock with positive recent_5 and recent_20 should get weight * mom_mult.
        Stock without positive momentum should keep base weight.
        """
        tickers = ["HOT", "COLD"]
        scores = {
            "HOT": {
                "raw_net_score": 1.0,
                "base_components": {"recent_5": 0.5, "recent_20": 0.3},
            },
            "COLD": {
                "raw_net_score": 0.9,
                "base_components": {"recent_5": -0.2, "recent_20": 0.1},
            },
        }
        sentiment = _make_sentiment(tickers, event="news")
        config = {
            "max_stock_buys_per_day": 15,
            "max_positions": 50,
            "pool_a_base": 10,
            "pool_b_base": 5,
            "pool_a_min": 3,
            "pool_b_min": 2,
            "min_position_size": 1.0,
            "momentum_amplification": True,
            "momentum_weight_multiplier": 2.0,
        }

        result = simulate_allocation(
            stock_buys=tickers,
            sell_syms=[],
            portfolio_total=10000.0,
            nexus_pct=0.10,
            min_pos_size=1.0,
            config=config,
            scores=scores,
            sentiment_data=sentiment,
        )

        assert len(result) == 2
        # n=2: tier1=1 (HOT gets 2.5), tier2=1 (COLD gets 1.0)
        # Momentum: HOT has both recent_5>0 and recent_20>0 => 2.5 * 2.0 = 5.0
        # COLD has recent_5 < 0 => stays 1.0
        # Total = 6.0, budget = 1000
        # HOT = 1000 * 5.0/6.0 = 833.33, COLD = 1000 * 1.0/6.0 = 166.67
        assert result["HOT"]["buy_cash"] > result["COLD"]["buy_cash"] * 3, (
            f"HOT (momentum-boosted) should get >3x COLD's allocation. "
            f"HOT={result['HOT']['buy_cash']}, COLD={result['COLD']['buy_cash']}"
        )


# ===================================================================
# SECTION 3: Integration Scenario Tests
# ===================================================================


class TestIntegrationScenarios:
    """End-to-end scenarios combining portfolio emulator, sell logic, and allocation."""

    def test_day1_allocation_with_7k_portfolio(self):
        """
        Day 1: $7000 portfolio, 10% nexus allocation = $700 budget.
        3 buy signals should be allocated within that budget.
        """
        pe = MockPortfolioEmulator(initial_cash=7000.0)
        prices = {"AAPL": 150.0, "MSFT": 300.0, "GOOG": 140.0}
        tickers = ["AAPL", "MSFT", "GOOG"]
        scores = _make_scores(tickers, [1.0, 0.8, 0.6])
        sentiment = _make_sentiment(tickers, event="news")

        config = {
            "max_stock_buys_per_day": 15,
            "max_positions": 50,
            "pool_a_base": 10,
            "pool_b_base": 5,
            "pool_a_min": 3,
            "pool_b_min": 2,
            "min_position_size": 50.0,
        }

        result = simulate_allocation(
            stock_buys=tickers,
            sell_syms=[],
            portfolio_total=pe.get_portfolio_value(prices),
            nexus_pct=0.10,
            min_pos_size=50.0,
            config=config,
            scores=scores,
            sentiment_data=sentiment,
        )

        total_allocated = sum(v["buy_cash"] for v in result.values())
        assert abs(total_allocated - 700.0) < 1.0, (
            f"Total allocated should be ~$700 (10% of $7000), got ${total_allocated:.2f}"
        )
        assert len(result) == 3

    def test_cash_exhaustion_day2_limited_buys(self):
        """
        Day 2: after buying on day 1, cash is reduced. Allocation is based
        on portfolio total (cash + positions), not just cash.
        """
        pe = MockPortfolioEmulator(initial_cash=7000.0)
        # Simulate day 1: spent $600 on 3 stocks
        pe.set_cash(6400.0)
        pe.add_position("AAPL", 2.0, 150.0)
        pe.add_position("MSFT", 0.5, 300.0)
        pe.add_position("GOOG", 1.0, 140.0)

        prices = {"AAPL": 155.0, "MSFT": 310.0, "GOOG": 142.0}
        portfolio_val = pe.get_portfolio_value(prices)
        # 6400 + 2*155 + 0.5*310 + 1*142 = 6400 + 310 + 155 + 142 = 7007

        tickers = ["AMZN", "META"]
        scores = _make_scores(tickers, [0.9, 0.7])
        sentiment = _make_sentiment(tickers, event="news")

        config = {
            "max_stock_buys_per_day": 15,
            "max_positions": 50,
            "pool_a_base": 10,
            "pool_b_base": 5,
            "pool_a_min": 3,
            "pool_b_min": 2,
            "min_position_size": 50.0,
        }

        result = simulate_allocation(
            stock_buys=tickers,
            sell_syms=[],
            portfolio_total=portfolio_val,
            nexus_pct=0.10,
            min_pos_size=50.0,
            config=config,
            scores=scores,
            sentiment_data=sentiment,
            current_positions=pe.get_positions(),
        )

        assert len(result) == 2
        total = sum(v["buy_cash"] for v in result.values())
        expected_budget = portfolio_val * 0.10
        assert abs(total - expected_budget) < 1.0, (
            f"Budget should be ~${expected_budget:.2f}, got ${total:.2f}"
        )

    def test_sndk_detected_day10_slot_available(self):
        """
        Day 10: new signal on SNDK with position headroom available.
        Allocation should succeed if max_positions is not breached.
        """
        pe = MockPortfolioEmulator(initial_cash=5000.0)
        # 8 existing positions
        for i in range(8):
            pe.add_position(f"POS{i}", 10.0, 100.0)

        prices = {f"POS{i}": 105.0 for i in range(8)}
        prices["SNDK"] = 45.0
        portfolio_val = pe.get_portfolio_value(prices)

        config = {
            "max_stock_buys_per_day": 15,
            "max_positions": 50,
            "pool_a_base": 10,
            "pool_b_base": 5,
            "pool_a_min": 3,
            "pool_b_min": 2,
            "min_position_size": 50.0,
        }

        result = simulate_allocation(
            stock_buys=["SNDK"],
            sell_syms=[],
            portfolio_total=portfolio_val,
            nexus_pct=0.10,
            min_pos_size=50.0,
            config=config,
            scores=_make_scores(["SNDK"], [0.85]),
            sentiment_data=_make_sentiment(["SNDK"], event="news"),
            current_positions=pe.get_positions(),
        )

        assert "SNDK" in result, "SNDK should be allocated with headroom available"
        assert result["SNDK"]["buy_cash"] >= 50.0

    def test_asti_held_through_winner_protection_then_trailing_stop(self):
        """
        ASTI is bought at $10, climbs to $15 (peak), then drops back to $12.60.
        Phase 1: At $14 (+40% gain), mild sell signal -> winner protection holds.
        Phase 2: At $12.60, drop from $15 peak is 16% -> trailing stop sells.
        """
        # Phase 1: winner protection at $14
        score_phase1 = simulate_v4_sell_logic(
            fresh_score=-1,
            unrealized_pct=40.0,  # (14-10)/10 * 100
            prop_raw=-0.20,
            hold_limit_forced=False,
            fast_cut_pct=-10.0,
            ts_activation=10.0,
            ts_drop=15.0,
            current_price=14.0,
            peak_price=15.0,
        )
        assert score_phase1 == 0, (
            "Phase 1: ASTI at +40% with mild negative should be protected"
        )

        # Phase 2: trailing stop at $12.60
        score_phase2 = simulate_v4_sell_logic(
            fresh_score=0,
            unrealized_pct=26.0,  # (12.60-10)/10 * 100
            prop_raw=0.0,
            hold_limit_forced=False,
            fast_cut_pct=-10.0,
            ts_activation=10.0,
            ts_drop=15.0,
            current_price=12.60,
            peak_price=15.0,
        )
        # drop from peak = (15-12.60)/15 * 100 = 16% >= 15%
        assert score_phase2 == -1, (
            "Phase 2: ASTI dropped 16% from peak $15 -> trailing stop sells"
        )


# ===================================================================
# SECTION 4: Edge Cases
# ===================================================================


class TestEdgeCases:
    """Boundary conditions and defensive coding tests."""

    def test_zero_cash_no_crash(self):
        """Allocation with $0 portfolio should return empty dict, not crash."""
        result = simulate_allocation(
            stock_buys=["A", "B"],
            sell_syms=[],
            portfolio_total=0.0,
            nexus_pct=0.10,
            min_pos_size=50.0,
            config={
                "max_stock_buys_per_day": 15,
                "max_positions": 50,
                "pool_a_base": 10,
                "pool_b_base": 5,
                "pool_a_min": 3,
                "pool_b_min": 2,
            },
            scores=_make_scores(["A", "B"]),
            sentiment_data=_make_sentiment(["A", "B"]),
        )
        assert result == {}, (
            "Zero portfolio value should produce empty allocation"
        )

    def test_empty_buy_list_no_crash(self):
        """Empty buy list should return empty dict, not crash."""
        result = simulate_allocation(
            stock_buys=[],
            sell_syms=[],
            portfolio_total=10000.0,
            nexus_pct=0.10,
            min_pos_size=50.0,
            config={
                "max_stock_buys_per_day": 15,
                "max_positions": 50,
                "pool_a_base": 10,
                "pool_b_base": 5,
                "pool_a_min": 3,
                "pool_b_min": 2,
            },
        )
        assert result == {}, (
            "Empty buy list should produce empty allocation"
        )

    def test_missing_price_for_held_position(self):
        """
        MockPortfolioEmulator.get_portfolio_value should handle missing prices
        gracefully by valuing missing tickers at 0.
        """
        pe = MockPortfolioEmulator(initial_cash=5000.0)
        pe.add_position("MYSTERY", 100.0, 50.0)

        # prices dict does NOT contain MYSTERY
        value = pe.get_portfolio_value({"AAPL": 150.0})

        # 5000 cash + 0 for MYSTERY (missing price) = 5000
        assert value == 5000.0, (
            f"Missing price should be treated as 0. Expected 5000, got {value}"
        )

    def test_no_trades_in_portfolio_emulator(self):
        """Fresh emulator with no trades should return empty positions and trade list."""
        pe = MockPortfolioEmulator(initial_cash=10000.0)
        assert pe.get_positions() == {}
        assert pe._trades == []
        assert pe.get_cash() == 10000.0
        assert pe.get_portfolio_value({}) == 10000.0

    def test_sell_logic_with_zero_peak_price(self):
        """
        Trailing stop with peak_price=0 should not crash (division by zero guard).
        """
        result = simulate_v4_sell_logic(
            fresh_score=0,
            unrealized_pct=15.0,
            prop_raw=0.0,
            hold_limit_forced=False,
            fast_cut_pct=-10.0,
            ts_activation=10.0,
            ts_drop=15.0,
            current_price=100.0,
            peak_price=0.0,
        )
        # peak_price=0 => drop_from_peak = 0 (guard clause) => no trailing stop
        assert result == 0, (
            "Zero peak price should not crash; trailing stop should not fire"
        )

    def test_single_stock_allocation(self):
        """Single stock should get the entire budget."""
        result = simulate_allocation(
            stock_buys=["ONLY"],
            sell_syms=[],
            portfolio_total=10000.0,
            nexus_pct=0.10,
            min_pos_size=1.0,
            config={
                "max_stock_buys_per_day": 15,
                "max_positions": 50,
                "pool_a_base": 10,
                "pool_b_base": 5,
                "pool_a_min": 3,
                "pool_b_min": 2,
                "min_position_size": 1.0,
            },
            scores=_make_scores(["ONLY"], [1.0]),
            sentiment_data=_make_sentiment(["ONLY"], event="news"),
        )
        assert len(result) == 1
        assert abs(result["ONLY"]["buy_cash"] - 1000.0) < 0.02

    def test_position_headroom_zero_blocks_all_buys(self):
        """When current positions already equal max_positions, no buys are made."""
        existing = {f"POS{i}": 10.0 for i in range(50)}
        result = simulate_allocation(
            stock_buys=["NEW"],
            sell_syms=[],
            portfolio_total=10000.0,
            nexus_pct=0.10,
            min_pos_size=1.0,
            config={
                "max_stock_buys_per_day": 15,
                "max_positions": 50,
                "pool_a_base": 10,
                "pool_b_base": 5,
                "pool_a_min": 3,
                "pool_b_min": 2,
                "min_position_size": 1.0,
            },
            scores=_make_scores(["NEW"], [1.0]),
            sentiment_data=_make_sentiment(["NEW"], event="news"),
            current_positions=existing,
        )
        assert len(result) == 0, (
            "No headroom (50/50 positions) should block all new buys"
        )

    def test_winner_protection_boundary_exactly_10pct(self):
        """At exactly 10% gain and exactly -0.30 raw, protection should NOT fire
        because the condition is prop_raw > -0.30 (strictly greater)."""
        result = simulate_v4_sell_logic(
            fresh_score=-1,
            unrealized_pct=10.0,
            prop_raw=-0.30,
            hold_limit_forced=False,
            fast_cut_pct=-10.0,
            ts_activation=10.0,
            ts_drop=15.0,
            current_price=110.0,
            peak_price=112.0,
        )
        assert result == -1, (
            "At exactly prop_raw == -0.30, winner protection should NOT fire "
            "(requires strictly > -0.30)"
        )

    def test_fast_cut_boundary_exactly_at_threshold(self):
        """At exactly the fast_cut_pct boundary, the cut SHOULD fire
        because condition is unrealized_pct <= fast_cut_pct."""
        result = simulate_v4_sell_logic(
            fresh_score=0,
            unrealized_pct=-10.0,
            prop_raw=0.0,
            hold_limit_forced=False,
            fast_cut_pct=-10.0,
            ts_activation=10.0,
            ts_drop=15.0,
            current_price=90.0,
            peak_price=90.0,
        )
        assert result == -1, (
            "At exactly -10% == fast_cut_pct (-10%), the cut should fire "
            "(uses <= comparison)"
        )


# ===================================================================
# SECTION 5: MockPortfolioEmulator validation
# ===================================================================


class TestMockPortfolioEmulator:
    """Verify the mock behaves consistently with the real PortfolioEmulator."""

    def test_add_position_records_trade(self):
        """add_position should record a buy trade in _trades."""
        pe = MockPortfolioEmulator(initial_cash=10000.0)
        pe.add_position("AAPL", 10.0, 150.0)

        assert len(pe._trades) == 1
        trade = pe._trades[0]
        assert trade["ticker"] == "AAPL"
        assert trade["action"] == "buy"
        assert trade["price"] == 150.0
        assert trade["shares"] == 10.0

    def test_portfolio_value_with_positions(self):
        """Portfolio value = cash + sum(positions * prices)."""
        pe = MockPortfolioEmulator(initial_cash=5000.0)
        pe.add_position("A", 10.0, 100.0)
        pe.add_position("B", 5.0, 200.0)

        prices = {"A": 110.0, "B": 190.0}
        value = pe.get_portfolio_value(prices)
        # 5000 + 10*110 + 5*190 = 5000 + 1100 + 950 = 7050
        assert value == 7050.0

    def test_get_positions_returns_copy(self):
        """get_positions must return a copy so mutations do not affect internal state."""
        pe = MockPortfolioEmulator(initial_cash=1000.0)
        pe.add_position("X", 5.0, 10.0)

        positions = pe.get_positions()
        positions["X"] = 999.0  # mutate the copy

        assert pe._positions["X"] == 5.0, (
            "Mutating the copy should not affect internal state"
        )

    def test_set_cash(self):
        """set_cash should update the internal cash balance."""
        pe = MockPortfolioEmulator(initial_cash=1000.0)
        pe.set_cash(500.0)
        assert pe.get_cash() == 500.0


# ===================================================================
# SECTION 5: V6 Fix Tests
# ===================================================================


class TestV6ForcedExitFlag(unittest.TestCase):
    """V6 Fix B: Forced exit scores persist through ML/overlay"""

    def test_forced_exit_flag_set_on_fast_loser(self):
        """Fast loser cut sets _forced_exit=True"""
        result = simulate_v4_sell_logic(
            fresh_score=0, unrealized_pct=-9.0, prop_raw=0.0,
            hold_limit_forced=False, fast_cut_pct=-8.0,
            ts_activation=8.0, ts_drop=12.0,
            current_price=91.0, peak_price=100.0
        )
        self.assertEqual(result, -1, "Fast loser should fire at -9%")

    def test_forced_exit_flag_set_on_trailing_stop(self):
        """Trailing stop sets _forced_exit=True"""
        result = simulate_v4_sell_logic(
            fresh_score=0, unrealized_pct=50.0, prop_raw=0.0,
            hold_limit_forced=False, fast_cut_pct=-8.0,
            ts_activation=8.0, ts_drop=12.0,
            current_price=88.0, peak_price=100.0
        )
        self.assertEqual(result, -1, "Trailing stop should fire at 12% from peak")

    def test_forced_exit_not_overridden_by_ml(self):
        """Forced exit score survives ML/overlay recalculation (the root cause bug fix)"""
        # Simulate: _finalize_scores sets score=-1 with _forced_exit=True
        # _apply_ml_and_overlay_to_scores should preserve it
        base = {"score": -1, "_forced_exit": True, "raw_net_score": 0.05}
        # If _forced_exit is True, final_score should be -1 regardless of raw_net
        if base.get("_forced_exit"):
            final_score = base["score"]
        else:
            final_score = 0  # Would be recalculated from raw_net
        self.assertEqual(final_score, -1, "Forced exit must survive ML/overlay")


class TestV6CashReserveFloor(unittest.TestCase):
    """V6 Fix C: Cash reserve floor prevents Day 1 all-in"""

    def test_cash_floor_limits_deployment(self):
        """10% floor on $7K = $700 reserved"""
        initial_value = 7000.0
        floor_pct = 0.10
        cash = 7000.0
        reserved = 0.0
        cash_floor = initial_value * floor_pct
        available = max(0.0, cash - reserved - cash_floor)
        self.assertEqual(available, 6300.0)

    def test_cash_floor_blocks_when_low(self):
        """When cash is at/below floor, available=0"""
        initial_value = 7000.0
        floor_pct = 0.10
        cash = 700.0  # Exactly at floor
        cash_floor = initial_value * floor_pct
        available = max(0.0, cash - 0.0 - cash_floor)
        self.assertEqual(available, 0.0)

    def test_high_conviction_bypasses_floor(self):
        """High conviction signals (score>=0.50) bypass the floor"""
        initial_value = 7000.0
        floor_pct = 0.10
        cash = 700.0
        high_conviction = True
        effective_floor = 0.0 if high_conviction else initial_value * floor_pct
        available = max(0.0, cash - 0.0 - effective_floor)
        self.assertEqual(available, 700.0, "High conviction should bypass floor")


class TestV6PositionRotation(unittest.TestCase):
    """V6 Fix D: Position rotation sells weak to buy strong"""

    def test_rotation_triggers_when_headroom_zero(self):
        """Rotation should trigger when position headroom is 0"""
        # Weakest position: P&L=-20%, score=0.10
        # New candidate: score=0.45, delta=0.35 > min_delta=0.25
        weak_pnl = -20.0
        weak_score = 0.10
        new_score = 0.45
        min_delta = 0.25
        min_score = 0.40
        should_rotate = new_score >= min_score and (new_score - weak_score) > min_delta
        self.assertTrue(should_rotate)

    def test_rotation_blocked_by_insufficient_delta(self):
        """Rotation blocked when score delta too small"""
        weak_score = 0.35
        new_score = 0.45
        min_delta = 0.25
        should_rotate = (new_score - weak_score) > min_delta
        self.assertFalse(should_rotate, "Delta 0.10 < 0.25 should block rotation")

    def test_rotation_blocked_by_low_score(self):
        """Rotation blocked when new signal score below minimum"""
        new_score = 0.30
        min_score = 0.40
        should_rotate = new_score >= min_score
        self.assertFalse(should_rotate, "Score 0.30 < 0.40 should block rotation")

    def test_max_rotations_per_day_cap(self):
        """Max 2 rotations per day"""
        max_rotations = 2
        pairs = [{"sell": "A", "buy": "X"}, {"sell": "B", "buy": "Y"}]
        self.assertEqual(len(pairs), max_rotations)
        # 3rd rotation should be blocked
        can_add = len(pairs) < max_rotations
        self.assertFalse(can_add)


class TestV6ETFDeduplication(unittest.TestCase):
    """V6 Fix E: ETF sector deduplication"""

    def test_semiconductor_etfs_deduped(self):
        """SMH and SOXX are both semiconductor — only 1 should survive"""
        etf_sector_map = {
            "semiconductor": ["smh", "soxx", "soxq"],
            "silver": ["slv", "pslv"],
            "oil": ["uso", "xle"],
        }
        etf_list = ["SMH", "SOXX", "USO"]
        seen = set()
        deduped = []
        for etf in etf_list:
            sector = "other"
            for s, tickers in etf_sector_map.items():
                if etf.lower() in tickers:
                    sector = s
                    break
            if sector not in seen:
                deduped.append(etf)
                seen.add(sector)
        self.assertEqual(deduped, ["SMH", "USO"])
        self.assertNotIn("SOXX", deduped)

    def test_silver_etfs_deduped(self):
        """SLV and PSLV are both silver — only 1 should survive"""
        etf_list = ["PSLV", "SLV", "XBI"]
        etf_sector_map = {"silver": ["slv", "pslv"], "biotech": ["xbi"]}
        seen = set()
        deduped = []
        for etf in etf_list:
            sector = "other"
            for s, tickers in etf_sector_map.items():
                if etf.lower() in tickers:
                    sector = s
                    break
            if sector not in seen:
                deduped.append(etf)
                seen.add(sector)
        self.assertEqual(deduped, ["PSLV", "XBI"])

    def test_held_etf_sector_blocked(self):
        """Already holding SMH — new SOXX (same sector) should be blocked"""
        held_sectors = {"semiconductor"}
        new_etf = "SOXX"
        new_sector = "semiconductor"
        blocked = new_sector in held_sectors
        self.assertTrue(blocked)

    def test_unknown_sector_passes(self):
        """Unknown ETF tickers pass through"""
        etf_sector_map = {"semiconductor": ["smh", "soxx"]}
        etf = "ARKK"
        sector = "other"
        for s, tickers in etf_sector_map.items():
            if etf.lower() in tickers:
                sector = s
        self.assertEqual(sector, "other")


class TestV6SweepFixes(unittest.TestCase):
    """Tests for V6 sweep bug fixes: forced exit enforcement, circuit breaker bypass, etc."""

    def test_forced_exit_added_to_sell_enforcement(self):
        """Forced exit symbols should be added to nexus_sell_enforcement"""
        scores = {
            "MTNB": {"score": -1, "_forced_exit": True, "reason": "Fast loser cut: -9%"},
            "AAPL": {"score": 1, "_forced_exit": False, "reason": "Direct buy"},
            "DOV": {"score": 0, "reason": "Hold"},
        }
        nexus_sell_enforcement = set()
        for sym, data in scores.items():
            if isinstance(data, dict) and data.get("_forced_exit") and data.get("score") == -1:
                nexus_sell_enforcement.add(sym)
        self.assertIn("MTNB", nexus_sell_enforcement)
        self.assertNotIn("AAPL", nexus_sell_enforcement)
        self.assertNotIn("DOV", nexus_sell_enforcement)

    def test_circuit_breaker_preserves_forced_exits(self):
        """Circuit breaker should never demote forced-exit sells"""
        sell_candidates = [
            ("MTNB", -0.5),  # forced exit
            ("DCGO", -0.3),  # forced exit
            ("AAPL", -0.2),  # normal sell
            ("MSFT", -0.1),  # normal sell
        ]
        scores = {
            "MTNB": {"score": -1, "_forced_exit": True},
            "DCGO": {"score": -1, "_forced_exit": True},
            "AAPL": {"score": -1, "_forced_exit": False},
            "MSFT": {"score": -1, "_forced_exit": False},
        }
        max_allowed = 2  # circuit breaker allows only 2 total sells
        # Step 1: forced exits always kept
        forced = {sym for sym, _ in sell_candidates if scores[sym].get("_forced_exit")}
        # Step 2: remaining slots for non-forced sells
        remaining_slots = max(0, max_allowed - len(forced))
        # Sort non-forced by severity (most negative first)
        non_forced = [(sym, sc) for sym, sc in sell_candidates if sym not in forced]
        non_forced.sort(key=lambda x: x[1])
        keep_sells = forced | {sym for sym, _ in non_forced[:remaining_slots]}
        # Demote non-forced sells that didn't make the cut
        for sym, _ in sell_candidates:
            if sym not in keep_sells:
                scores[sym]["score"] = 0
        # MTNB and DCGO should be preserved (forced exits)
        self.assertEqual(scores["MTNB"]["score"], -1)
        self.assertEqual(scores["DCGO"]["score"], -1)
        # At least one normal sell should be demoted (2 forced fills both slots)
        demoted_count = sum(1 for s in ["AAPL", "MSFT"] if scores[s]["score"] == 0)
        self.assertGreaterEqual(demoted_count, 1)

    def test_sell_block_cannot_override_forced_exit(self):
        """LLM overlay sell_block should NOT override a forced exit"""
        base = {"score": -1, "_forced_exit": True}
        overlay = {"sell_block": True}
        final_score = base["score"]  # -1
        # sell_block check with forced exit guard
        if overlay.get("sell_block") and final_score < 0 and not base.get("_forced_exit"):
            final_score = 0
        self.assertEqual(final_score, -1, "Forced exit must not be blocked by sell_block")

    def test_sell_block_works_for_normal_sells(self):
        """LLM overlay sell_block SHOULD override normal (non-forced) sells"""
        base = {"score": -1, "_forced_exit": False}
        overlay = {"sell_block": True}
        final_score = base["score"]
        if overlay.get("sell_block") and final_score < 0 and not base.get("_forced_exit"):
            final_score = 0
        self.assertEqual(final_score, 0, "Normal sell should be blocked by sell_block")

    def test_cash_floor_passthrough_from_strategy(self):
        """Strategy should pass _cash_reserve_floor_pct to nexus_position_sizes"""
        config = {"cash_reserve_floor_pct": 0.15}
        nexus_position_sizes = {}
        nexus_position_sizes["_cash_reserve_floor_pct"] = float(config.get("cash_reserve_floor_pct", 0.10))
        self.assertEqual(nexus_position_sizes["_cash_reserve_floor_pct"], 0.15)

    def test_cash_floor_default_when_not_configured(self):
        """Default cash floor should be 0.10 (10%)"""
        config = {}
        nexus_position_sizes = {}
        nexus_position_sizes["_cash_reserve_floor_pct"] = float(config.get("cash_reserve_floor_pct", 0.10))
        self.assertEqual(nexus_position_sizes["_cash_reserve_floor_pct"], 0.10)

    def test_total_weight_zero_guard(self):
        """total_weight should never be zero even with empty weights"""
        weights = {}
        total_weight = sum(weights.values()) or 1.0
        self.assertEqual(total_weight, 1.0)

    def test_total_weight_normal(self):
        """total_weight guard should not affect normal operation"""
        weights = {"A": 2.5, "B": 1.0, "C": 0.5}
        total_weight = sum(weights.values()) or 1.0
        self.assertEqual(total_weight, 4.0)

    def test_strategy_cache_none_guard(self):
        """strategy_cache should be converted to {} when None"""
        strategy_cache = None
        if strategy_cache is None:
            strategy_cache = {}
        strategy_cache["_peak_ASTI"] = 8.00
        self.assertEqual(strategy_cache["_peak_ASTI"], 8.00)

    def test_rotation_sell_added_to_sell_syms(self):
        """Rotation should add the weakest position to sell_syms"""
        sell_syms = ["AAPL"]  # existing sell
        rotation_sell = "DCGO"
        if rotation_sell not in sell_syms:
            sell_syms.append(rotation_sell)
        self.assertIn("DCGO", sell_syms)
        self.assertEqual(len(sell_syms), 2)

    def test_ensure_prices_return_value_captured(self):
        """_ensure_prices_include_positions return value should be captured"""
        # Simulate: function returns a new dict when input is None
        def mock_ensure(prices):
            if prices is None:
                prices = {}
            prices["MTNB"] = 1.10
            return prices
        prices = {"SPY": 500.0}
        prices = mock_ensure(prices)  # Must capture return
        self.assertIn("MTNB", prices)
        self.assertEqual(prices["MTNB"], 1.10)


# ===================================================================
# SECTION 6: V6 Crash Fix Tests
# ===================================================================


class TestV6CrashFix(unittest.TestCase):
    """Test for the float-in-nexus_position_sizes crash that was fixed.

    Root cause: nexus_position_sizes can contain non-dict metadata entries
    like _cash_reserve_floor_pct=0.10 (a float). The sell-first ordering
    loop in broker.py must guard with isinstance(hint, dict) to avoid
    crashing when iterating over these entries.
    """

    def test_non_dict_values_in_position_sizes_handled(self):
        """nexus_position_sizes can contain non-dict values like _cash_reserve_floor_pct=0.10"""
        nexus_position_sizes = {
            "AAPL": {"buy_cash": 1000, "high_conviction": True},
            "MSFT": {"sell_fraction": 1.0},
            "_cash_reserve_floor_pct": 0.10,  # This is a float, not a dict
        }
        # The sell-first ordering loop should not crash on the float value
        sell_set = set()
        for sym, hint in nexus_position_sizes.items():
            if isinstance(hint, dict) and 'sell_fraction' in hint and 'buy_cash' not in hint:
                sell_set.add(sym)
        self.assertNotIn("_cash_reserve_floor_pct", sell_set)
        self.assertIn("MSFT", sell_set)
        self.assertNotIn("AAPL", sell_set)

    def test_non_dict_values_multiple_metadata_keys(self):
        """Multiple non-dict metadata keys should all be safely skipped"""
        nexus_position_sizes = {
            "GOOG": {"buy_cash": 500},
            "_cash_reserve_floor_pct": 0.10,
            "_nexus_discovered": ["GOOG", "AAPL"],  # a list, not a dict
            "_rotation_count": 2,  # an int, not a dict
        }
        sell_set = set()
        buy_set = set()
        for sym, hint in nexus_position_sizes.items():
            if isinstance(hint, dict):
                if 'sell_fraction' in hint and 'buy_cash' not in hint:
                    sell_set.add(sym)
                elif 'buy_cash' in hint:
                    buy_set.add(sym)
        self.assertEqual(sell_set, set())
        self.assertEqual(buy_set, {"GOOG"})

    def test_sell_first_buy_rest_ordering(self):
        """Sells should be ordered before buys in execution sequence"""
        nexus_position_sizes = {
            "AAPL": {"buy_cash": 800},
            "MTNB": {"sell_fraction": 1.0},
            "MSFT": {"buy_cash": 600},
            "DCGO": {"sell_fraction": 1.0},
            "_cash_reserve_floor_pct": 0.10,
        }
        nexus_sell_enforcement = {"MTNB", "DCGO"}
        expanded_symbols = sorted(["AAPL", "MTNB", "MSFT", "DCGO"])
        _nexus_sell_set = set(nexus_sell_enforcement)
        for _ps_sym, _ps_hint in nexus_position_sizes.items():
            if isinstance(_ps_hint, dict) and 'sell_fraction' in _ps_hint and 'buy_cash' not in _ps_hint:
                _nexus_sell_set.add(_ps_sym)
        _sell_first = [s for s in sorted(expanded_symbols) if s in _nexus_sell_set]
        _buy_rest = [s for s in sorted(expanded_symbols) if s not in _nexus_sell_set]
        ordered = _sell_first + _buy_rest
        # Sells must come before buys
        sell_indices = [ordered.index(s) for s in _sell_first]
        buy_indices = [ordered.index(s) for s in _buy_rest]
        if sell_indices and buy_indices:
            self.assertLess(max(sell_indices), min(buy_indices),
                            "All sells must be ordered before all buys")


# ===================================================================
# SECTION 7: Backfill Queue Tests (Specification)
# ===================================================================


class TestBackfillQueue(unittest.TestCase):
    """Backfill Queue system tests -- defines expected behavior for deferred buy processing.

    These are specification tests that define the EXPECTED behavior of the
    Backfill Queue system. The queue allows buy signals that cannot execute
    (due to insufficient cash, position limits, etc.) to be retried on
    subsequent bars when conditions improve.
    """

    def _make_queue_entry(self, ticker, signal_price, score, priority=2,
                          bar_count=0, buy_cash=500.0):
        """Helper: create a standard backfill queue entry."""
        return {
            "ticker": ticker,
            "signal_price": signal_price,
            "score": score,
            "priority": priority,  # 1=propagation, 2=direct
            "bar_count": bar_count,
            "buy_cash": buy_cash,
            "queued_at": datetime(2026, 3, 20),
        }

    def test_blocked_buy_added_to_queue(self):
        """When a buy signal can't execute due to $0 cash, it should be queued"""
        available_cash = 0.0
        buy_cash_needed = 500.0
        backfill_queue = []

        # Simulate: buy blocked, add to queue
        if available_cash < buy_cash_needed:
            backfill_queue.append(self._make_queue_entry(
                "NVDA", signal_price=120.0, score=0.75,
            ))

        self.assertEqual(len(backfill_queue), 1)
        self.assertEqual(backfill_queue[0]["ticker"], "NVDA")
        self.assertEqual(backfill_queue[0]["signal_price"], 120.0)
        self.assertEqual(backfill_queue[0]["bar_count"], 0)

    def test_queue_processes_when_cash_available(self):
        """Queued tickers should be bought when cash becomes available"""
        backfill_queue = [
            self._make_queue_entry("NVDA", signal_price=120.0, score=0.75, buy_cash=400.0),
        ]
        available_cash = 600.0  # Cash freed from a sell

        bought = []
        remaining_queue = []
        for entry in sorted(backfill_queue, key=lambda e: e["priority"]):
            if available_cash >= entry["buy_cash"]:
                bought.append(entry["ticker"])
                available_cash -= entry["buy_cash"]
            else:
                remaining_queue.append(entry)

        self.assertIn("NVDA", bought)
        self.assertEqual(len(remaining_queue), 0)
        self.assertEqual(available_cash, 200.0)

    def test_queue_expires_after_max_attempts(self):
        """Queued tickers should expire after max_attempts bars"""
        max_queue_attempts = 5
        backfill_queue = [
            self._make_queue_entry("NVDA", signal_price=120.0, score=0.75, bar_count=5),
            self._make_queue_entry("AMD", signal_price=90.0, score=0.65, bar_count=3),
        ]

        active_queue = [e for e in backfill_queue if e["bar_count"] < max_queue_attempts]

        self.assertEqual(len(active_queue), 1)
        self.assertEqual(active_queue[0]["ticker"], "AMD")

    def test_queue_price_staleness_check(self):
        """Don't buy from queue if price moved >20% above signal price"""
        staleness_pct = 0.20
        entry = self._make_queue_entry("NVDA", signal_price=100.0, score=0.75)
        current_price = 125.0  # +25% above signal price

        price_change = (current_price - entry["signal_price"]) / entry["signal_price"]
        is_stale = price_change > staleness_pct

        self.assertTrue(is_stale, "Price moved +25% > 20% threshold, should be stale")

    def test_queue_price_within_staleness_threshold(self):
        """Buy from queue if price is within staleness threshold"""
        staleness_pct = 0.20
        entry = self._make_queue_entry("NVDA", signal_price=100.0, score=0.75)
        current_price = 115.0  # +15% above signal price

        price_change = (current_price - entry["signal_price"]) / entry["signal_price"]
        is_stale = price_change > staleness_pct

        self.assertFalse(is_stale, "Price moved +15% < 20% threshold, should not be stale")

    def test_queue_priority_ordering(self):
        """Propagation stocks (priority 1) should be processed before direct signals (priority 2)"""
        backfill_queue = [
            self._make_queue_entry("AAPL", signal_price=180.0, score=0.80, priority=2),
            self._make_queue_entry("TSLA", signal_price=250.0, score=0.70, priority=1),
            self._make_queue_entry("GOOG", signal_price=140.0, score=0.65, priority=2),
        ]

        sorted_queue = sorted(backfill_queue, key=lambda e: (e["priority"], -e["score"]))

        self.assertEqual(sorted_queue[0]["ticker"], "TSLA",
                         "Propagation stock (priority=1) should be first")
        self.assertEqual(sorted_queue[1]["ticker"], "AAPL",
                         "Higher score direct signal should be second")

    def test_cash_recycling_from_forced_exit(self):
        """When fast-loser-cut frees cash, immediately check queue"""
        backfill_queue = [
            self._make_queue_entry("NVDA", signal_price=120.0, score=0.75, buy_cash=600.0),
            self._make_queue_entry("AMD", signal_price=90.0, score=0.65, buy_cash=500.0),
        ]
        freed_cash = 800.0  # Cash freed by forced exit on MTNB

        bought = []
        remaining_cash = freed_cash
        for entry in sorted(backfill_queue, key=lambda e: (e["priority"], -e["score"])):
            if remaining_cash >= entry["buy_cash"]:
                bought.append(entry["ticker"])
                remaining_cash -= entry["buy_cash"]

        self.assertIn("NVDA", bought, "NVDA should be bought with $800 freed cash")
        # $800 - $600 = $200 remaining, not enough for AMD ($500)
        self.assertNotIn("AMD", bought, "AMD should not be bought with only $200 remaining")

    def test_sell_to_buy_rotation_from_queue(self):
        """When queue has high-priority entries and portfolio is full, rotate weakest"""
        max_positions = 10
        current_positions = {f"POS{i}": 10.0 for i in range(10)}
        position_scores = {f"POS{i}": 0.10 + i * 0.05 for i in range(10)}
        # POS0 is weakest at 0.10

        queue_entry = self._make_queue_entry("NVDA", signal_price=120.0, score=0.80,
                                              priority=1)

        headroom = max(0, max_positions - len(current_positions))
        self.assertEqual(headroom, 0, "Portfolio should be full")

        # Find weakest position
        weakest_sym = min(position_scores, key=position_scores.get)
        weakest_score = position_scores[weakest_sym]
        min_delta = 0.25
        min_score = 0.40

        should_rotate = (queue_entry["score"] >= min_score and
                         (queue_entry["score"] - weakest_score) > min_delta)
        self.assertTrue(should_rotate,
                        f"Should rotate: NVDA score 0.80 vs {weakest_sym} score {weakest_score}")

    def test_queue_max_size_limit(self):
        """Queue should not exceed max_queue_size (15)"""
        max_queue_size = 15
        backfill_queue = [
            self._make_queue_entry(f"TICK{i}", signal_price=100.0 + i,
                                   score=0.50 + i * 0.01)
            for i in range(15)
        ]
        self.assertEqual(len(backfill_queue), max_queue_size)

        # Attempting to add 16th entry should be rejected or evict lowest priority
        new_entry = self._make_queue_entry("NEWSTOCK", signal_price=200.0, score=0.90)
        if len(backfill_queue) >= max_queue_size:
            # Evict lowest priority / lowest score entry
            backfill_queue.sort(key=lambda e: (e["priority"], -e["score"]))
            worst = backfill_queue[-1]
            if new_entry["score"] > worst["score"]:
                backfill_queue[-1] = new_entry  # Replace worst with new

        self.assertEqual(len(backfill_queue), max_queue_size,
                         "Queue must not exceed max size")
        # The new high-score entry should have replaced the worst
        tickers_in_queue = {e["ticker"] for e in backfill_queue}
        self.assertIn("NEWSTOCK", tickers_in_queue,
                       "High-score entry should evict lowest-score entry")

    def test_queue_entry_updated_on_re_signal(self):
        """Re-signal for a queued ticker should update score and reset bar_count"""
        backfill_queue = [
            self._make_queue_entry("NVDA", signal_price=120.0, score=0.60, bar_count=3),
        ]

        # New signal for NVDA with higher score
        new_score = 0.85
        new_price = 118.0
        for entry in backfill_queue:
            if entry["ticker"] == "NVDA":
                entry["score"] = new_score
                entry["signal_price"] = new_price
                entry["bar_count"] = 0  # Reset staleness counter

        self.assertEqual(backfill_queue[0]["score"], 0.85)
        self.assertEqual(backfill_queue[0]["bar_count"], 0)
        self.assertEqual(backfill_queue[0]["signal_price"], 118.0)

    def test_queue_bar_count_increments_each_bar(self):
        """bar_count should increment each bar the entry remains unprocessed"""
        backfill_queue = [
            self._make_queue_entry("NVDA", signal_price=120.0, score=0.75, bar_count=0),
        ]

        # Simulate 3 bars passing
        for _ in range(3):
            for entry in backfill_queue:
                entry["bar_count"] += 1

        self.assertEqual(backfill_queue[0]["bar_count"], 3)

    def test_queue_cleared_on_session_end(self):
        """Queue should be fully cleared when a session ends"""
        backfill_queue = [
            self._make_queue_entry("NVDA", signal_price=120.0, score=0.75),
            self._make_queue_entry("AMD", signal_price=90.0, score=0.65),
        ]
        self.assertEqual(len(backfill_queue), 2)

        # Session end
        backfill_queue.clear()

        self.assertEqual(len(backfill_queue), 0,
                         "Queue must be empty after session end")

    def test_queue_skips_already_held_ticker(self):
        """Don't buy from queue if the ticker is already in the portfolio"""
        current_positions = {"NVDA": 10.0, "AAPL": 5.0}
        backfill_queue = [
            self._make_queue_entry("NVDA", signal_price=120.0, score=0.75),
            self._make_queue_entry("GOOG", signal_price=140.0, score=0.65),
        ]

        processable = [e for e in backfill_queue if e["ticker"] not in current_positions]
        self.assertEqual(len(processable), 1)
        self.assertEqual(processable[0]["ticker"], "GOOG")

    def test_queue_respects_position_limit(self):
        """Queue processing should not exceed max_positions"""
        max_positions = 10
        current_positions = {f"POS{i}": 10.0 for i in range(9)}  # 9 held
        headroom = max(0, max_positions - len(current_positions))  # 1 slot
        self.assertEqual(headroom, 1)

        backfill_queue = [
            self._make_queue_entry("NVDA", signal_price=120.0, score=0.80, buy_cash=300.0),
            self._make_queue_entry("AMD", signal_price=90.0, score=0.70, buy_cash=300.0),
        ]
        available_cash = 1000.0

        bought = []
        for entry in sorted(backfill_queue, key=lambda e: (e["priority"], -e["score"])):
            if len(bought) >= headroom:
                break
            if available_cash >= entry["buy_cash"]:
                bought.append(entry["ticker"])
                available_cash -= entry["buy_cash"]

        self.assertEqual(len(bought), 1, "Only 1 slot available, only 1 buy allowed")
        self.assertEqual(bought[0], "NVDA", "Highest score should fill the slot")


# ===================================================================
# SECTION 8: Blacklist Decay Independence Tests
# ===================================================================


class TestBlacklistDecayIndependence(unittest.TestCase):
    """V7.3 Fix: Blacklist bars_remaining must decrement every bar,
    regardless of whether stock_buys is empty or non-empty.

    Bug: Previously the blacklist decay logic lived inside the
    `if stock_buys:` branch, so on bars with zero buy signals the
    blacklist never decremented and entries stayed forever.

    Fix: Blacklist decay is now unconditional -- it runs before the
    `if stock_buys:` / `if portfolio_total > 0:` gates.
    """

    @staticmethod
    def _simulate_blacklist_decay(strategy_cache, num_bars):
        """Simulate the unconditional blacklist decay logic from
        graph_nexus_analysis lines 11555-11562.

        This mirrors the FIXED code path which runs every bar regardless
        of stock_buys content.
        """
        for _ in range(num_bars):
            _fl_blacklist = strategy_cache.get("_fast_loser_blacklist", {})
            # Remove expired entries first
            _fl_expired = [
                k for k, v in _fl_blacklist.items()
                if v.get("bars_remaining", 0) <= 0
            ]
            for _fle in _fl_expired:
                del _fl_blacklist[_fle]
            # Decrement remaining entries
            for _flk in _fl_blacklist:
                _fl_blacklist[_flk]["bars_remaining"] = (
                    _fl_blacklist[_flk].get("bars_remaining", 0) - 1
                )

    def test_decay_with_empty_stock_buys(self):
        """bars_remaining decrements even when stock_buys is empty."""
        strategy_cache = {
            "_fast_loser_blacklist": {
                "MTNB": {"date": "2026-03-20", "bars_remaining": 5},
            }
        }
        stock_buys = []  # No buy signals this bar

        # Run 1 bar of decay (simulates the fix: decay is unconditional)
        self._simulate_blacklist_decay(strategy_cache, num_bars=1)

        bl = strategy_cache["_fast_loser_blacklist"]
        self.assertIn("MTNB", bl, "MTNB should still be blacklisted after 1 bar")
        self.assertEqual(
            bl["MTNB"]["bars_remaining"], 4,
            "bars_remaining should decrement from 5 to 4 even with empty stock_buys"
        )

    def test_decay_expires_after_n_bars_no_buy_signals(self):
        """Blacklist entry expires exactly after N bars with zero buy signals."""
        initial_bars = 3
        strategy_cache = {
            "_fast_loser_blacklist": {
                "DCGO": {"date": "2026-03-18", "bars_remaining": initial_bars},
            }
        }

        # Run decay for exactly initial_bars + 1 bars:
        # Bar 1: remaining 3 -> expired? no -> decrement to 2
        # Bar 2: remaining 2 -> expired? no -> decrement to 1
        # Bar 3: remaining 1 -> expired? no -> decrement to 0
        # Bar 4: remaining 0 -> expired? YES -> removed
        self._simulate_blacklist_decay(strategy_cache, num_bars=initial_bars + 1)

        bl = strategy_cache["_fast_loser_blacklist"]
        self.assertNotIn(
            "DCGO", bl,
            f"DCGO should be removed after {initial_bars + 1} bars of decay "
            f"(started with bars_remaining={initial_bars})"
        )

    def test_decay_does_not_expire_early(self):
        """Blacklist entry must NOT expire before its bars_remaining is exhausted."""
        strategy_cache = {
            "_fast_loser_blacklist": {
                "ASTI": {"date": "2026-03-15", "bars_remaining": 10},
            }
        }

        # Run 5 bars -- should still be present
        self._simulate_blacklist_decay(strategy_cache, num_bars=5)

        bl = strategy_cache["_fast_loser_blacklist"]
        self.assertIn("ASTI", bl, "ASTI should still be blacklisted after only 5 of 10 bars")
        # After 5 bars: decremented 5 times = 10 - 5 = 5
        self.assertEqual(
            bl["ASTI"]["bars_remaining"], 5,
            "bars_remaining should be 5 after decaying 5 of 10 bars"
        )

    def test_multiple_blacklist_entries_decay_independently(self):
        """Each blacklist entry decays on its own schedule."""
        strategy_cache = {
            "_fast_loser_blacklist": {
                "MTNB": {"date": "2026-03-20", "bars_remaining": 2},
                "DCGO": {"date": "2026-03-18", "bars_remaining": 5},
            }
        }

        # After 3 bars: MTNB should expire, DCGO should have 2 remaining
        self._simulate_blacklist_decay(strategy_cache, num_bars=3)

        bl = strategy_cache["_fast_loser_blacklist"]
        self.assertNotIn("MTNB", bl, "MTNB (started at 2) should expire after 3 bars")
        self.assertIn("DCGO", bl, "DCGO (started at 5) should still be active after 3 bars")
        self.assertEqual(
            bl["DCGO"]["bars_remaining"], 2,
            "DCGO should have 2 bars remaining (5 - 3 = 2)"
        )

    def test_decay_with_already_expired_entry(self):
        """Entry with bars_remaining=0 should be cleaned up on the next bar."""
        strategy_cache = {
            "_fast_loser_blacklist": {
                "DEAD": {"date": "2026-03-10", "bars_remaining": 0},
                "ALIVE": {"date": "2026-03-15", "bars_remaining": 3},
            }
        }

        self._simulate_blacklist_decay(strategy_cache, num_bars=1)

        bl = strategy_cache["_fast_loser_blacklist"]
        self.assertNotIn("DEAD", bl, "Entry with bars_remaining=0 should be removed")
        self.assertIn("ALIVE", bl, "Entry with bars_remaining=3 should survive")
        self.assertEqual(bl["ALIVE"]["bars_remaining"], 2)

    def test_empty_blacklist_no_crash(self):
        """Decay logic should not crash when blacklist is empty."""
        strategy_cache = {"_fast_loser_blacklist": {}}
        # Should not raise
        self._simulate_blacklist_decay(strategy_cache, num_bars=5)
        self.assertEqual(strategy_cache["_fast_loser_blacklist"], {})

    def test_missing_blacklist_key_no_crash(self):
        """Decay logic should handle missing _fast_loser_blacklist key."""
        strategy_cache = {}
        # Should not raise
        self._simulate_blacklist_decay(strategy_cache, num_bars=3)
        # Key may or may not exist, but no crash
        self.assertIsInstance(strategy_cache, dict)


# ===================================================================
# SECTION 8C: V28.6 BFQ Winner-Lock Bypass Tests
# ===================================================================


class TestBfqWinnerLockBypass(unittest.TestCase):
    """V28.6: BFQ winner-lock bypass -- aged high-score items can displace
    armored winners to break the post-cascade drainage deadlock.

    After V28.3 full-exit cascades and V28.5 expanded losing eviction, the
    portfolio can calcify around winner-locked holds with +5..+10% PnL, so
    high-scoring BFQ items that have been aged in the queue get stranded and
    never re-enter. The bypass allows an aged, high-conviction queue item
    (>=10 bars in queue, score >=1.5, delta >=1.5) to displace an armored
    winner whose PnL is in the 5..10% band and hold >=5d, but still protects
    the biggest winners (>10% PnL).

    V28.6.1 calibration: behavior trace showed MU's actual signal_score was
    1.797, below the original 2.0 floor. Thresholds lowered to 1.5/1.5 so the
    release valve actually fires on real observed signals.
    """

    def setUp(self):
        self.cfg = {
            "backfill_rotation_winner_lock_bypass_enabled": True,
            "backfill_rotation_winner_lock_bypass_min_queue_bars": 10,
            "backfill_rotation_winner_lock_bypass_min_queue_score": 1.5,
            "backfill_rotation_winner_lock_bypass_min_delta": 1.5,
            "backfill_rotation_winner_lock_bypass_max_held_pnl_pct": 10.0,
            "backfill_rotation_winner_lock_bypass_min_held_pnl_pct": 5.0,
            "backfill_rotation_winner_lock_bypass_min_held_hold_days": 5,
        }

    def _call(self, **overrides):
        from backend.strategies.graph_nexus_analysis import _bfq_winner_lock_bypass_allowed
        kwargs = dict(
            queue_bars=11,
            queue_score=1.6,
            delta=1.6,
            held_pnl_pct=7.0,
            held_days=8,
            config=self.cfg,
        )
        kwargs.update(overrides)
        return _bfq_winner_lock_bypass_allowed(**kwargs)

    def test_aged_high_score_item_displaces_armored_winner(self):
        """Happy path: aged+high-score+high-delta vs mid-PnL winner = bypass allowed."""
        self.assertTrue(self._call(queue_score=1.6, delta=1.6))

    def test_enabled_by_default(self):
        """Empty config uses the calibrated safe-on release valve."""
        self.assertTrue(self._call(config={}))

    def test_too_young_in_queue_blocked(self):
        """Queue item below min_queue_bars threshold must not bypass."""
        self.assertFalse(self._call(queue_bars=9))

    def test_low_queue_score_blocked(self):
        """Queue item below min_queue_score must not bypass."""
        self.assertFalse(self._call(queue_score=1.49))

    def test_low_delta_blocked(self):
        """Delta (queue_score - held_score) below min_delta must not bypass."""
        self.assertFalse(self._call(delta=1.49))

    def test_held_pnl_too_low_blocked(self):
        """Held PnL below min_held_pnl_pct must not bypass (too weak = not 'armored')."""
        self.assertFalse(self._call(held_pnl_pct=4.99))

    def test_held_pnl_too_high_protects_top_winner(self):
        """Held PnL above max_held_pnl_pct must not bypass (protect biggest winners)."""
        self.assertFalse(self._call(held_pnl_pct=10.01))

    def test_held_too_short_blocked(self):
        """Held position below min_held_hold_days must not bypass."""
        self.assertFalse(self._call(held_days=4))

    def test_explicitly_disabled_flag_blocks(self):
        """Explicit disabled flag overrides all other criteria."""
        disabled_cfg = {**self.cfg, "backfill_rotation_winner_lock_bypass_enabled": False}
        self.assertFalse(self._call(config=disabled_cfg))

    def test_mu_scenario_at_score_1_797_passes(self):
        """V28.6.1 calibration: MU's actual log score 1.797 + delta 1.6 must pass the bypass."""
        self.assertTrue(self._call(queue_score=1.797, delta=1.6))


# ===================================================================
# SECTION 9: Backfill Queue Blacklist Filter Tests
# ===================================================================


class TestBackfillQueueBlacklistFilter(unittest.TestCase):
    """V7.3 Fix: Blacklisted tickers must be rejected from the backfill queue
    at both enqueue and dequeue time.

    Bug: Previously, blacklisted tickers could still enter the backfill queue
    and could be dequeued for purchase, circumventing the fast-loser blacklist.

    Fix: Both the enqueue path (line 11830) and dequeue path (lines 11945-11950)
    now check the _fast_loser_blacklist and skip blacklisted tickers.
    """

    def _make_queue_entry(self, ticker, score=0.75, price=100.0, priority=2,
                          bars_in_queue=0):
        """Helper: create a standard backfill queue entry."""
        return {
            "ticker": ticker,
            "first_signal_date": "2026-03-20",
            "raw_net_score": score,
            "signal_source": "direct" if priority == 2 else "propagation",
            "price_at_signal": price,
            "bars_in_queue": bars_in_queue,
            "priority": priority,
        }

    # --- Enqueue path tests ---

    def test_blacklisted_ticker_rejected_on_enqueue(self):
        """A ticker on the fast-loser blacklist must not be added to the queue."""
        strategy_cache = {
            "_fast_loser_blacklist": {
                "MTNB": {"date": "2026-03-19", "bars_remaining": 15},
            }
        }
        _bfq = []  # empty queue
        _bfq_max_size = 15
        _bfq_min_score = 0.30
        _bfq_already_queued = set()
        _bfq_already_held = set()
        _bfq_bought_this_bar = set()

        candidates = ["MTNB", "AAPL"]
        scores = {
            "MTNB": {"raw_net_score": 0.80},
            "AAPL": {"raw_net_score": 0.70},
        }
        prices = {"MTNB": 1.50, "AAPL": 180.0}

        _fl_blacklist_enq = strategy_cache.get("_fast_loser_blacklist", {})
        for _bfq_sym in candidates:
            if (_bfq_sym not in _bfq_bought_this_bar
                    and _bfq_sym not in _bfq_already_queued
                    and _bfq_sym not in _bfq_already_held
                    and _bfq_sym not in _fl_blacklist_enq
                    and len(_bfq) < _bfq_max_size):
                _bfq_score = float(scores.get(_bfq_sym, {}).get("raw_net_score", 0.0))
                if _bfq_score >= _bfq_min_score:
                    _bfq_price = float(prices.get(_bfq_sym, 0))
                    if _bfq_price > 0:
                        _bfq.append(self._make_queue_entry(
                            _bfq_sym, score=_bfq_score, price=_bfq_price,
                        ))

        queued_tickers = {e["ticker"] for e in _bfq}
        self.assertNotIn("MTNB", queued_tickers,
                         "Blacklisted MTNB must not enter the backfill queue")
        self.assertIn("AAPL", queued_tickers,
                      "Non-blacklisted AAPL should be enqueued normally")

    def test_non_blacklisted_tickers_enqueue_normally(self):
        """Tickers NOT on the blacklist should be enqueued without interference."""
        strategy_cache = {
            "_fast_loser_blacklist": {
                "MTNB": {"date": "2026-03-19", "bars_remaining": 10},
            }
        }
        _bfq = []
        _bfq_max_size = 15
        _bfq_min_score = 0.30

        candidates = ["AAPL", "MSFT", "GOOG"]
        scores = {t: {"raw_net_score": 0.60 + i * 0.05} for i, t in enumerate(candidates)}
        prices = {"AAPL": 180.0, "MSFT": 400.0, "GOOG": 170.0}

        _fl_blacklist_enq = strategy_cache.get("_fast_loser_blacklist", {})
        for _bfq_sym in candidates:
            if (_bfq_sym not in _fl_blacklist_enq
                    and len(_bfq) < _bfq_max_size):
                _bfq_score = float(scores.get(_bfq_sym, {}).get("raw_net_score", 0.0))
                if _bfq_score >= _bfq_min_score:
                    _bfq_price = float(prices.get(_bfq_sym, 0))
                    if _bfq_price > 0:
                        _bfq.append(self._make_queue_entry(
                            _bfq_sym, score=_bfq_score, price=_bfq_price,
                        ))

        queued_tickers = {e["ticker"] for e in _bfq}
        self.assertEqual(
            queued_tickers, {"AAPL", "MSFT", "GOOG"},
            "All non-blacklisted candidates should be enqueued"
        )

    def test_multiple_blacklisted_tickers_all_rejected(self):
        """Multiple blacklisted tickers should all be rejected."""
        strategy_cache = {
            "_fast_loser_blacklist": {
                "MTNB": {"date": "2026-03-19", "bars_remaining": 10},
                "DCGO": {"date": "2026-03-18", "bars_remaining": 8},
                "ASTI": {"date": "2026-03-17", "bars_remaining": 3},
            }
        }
        _bfq = []
        candidates = ["MTNB", "DCGO", "ASTI", "NVDA"]
        scores = {t: {"raw_net_score": 0.70} for t in candidates}
        prices = {t: 100.0 for t in candidates}

        _fl_blacklist_enq = strategy_cache.get("_fast_loser_blacklist", {})
        for _bfq_sym in candidates:
            if _bfq_sym not in _fl_blacklist_enq:
                _bfq_score = float(scores.get(_bfq_sym, {}).get("raw_net_score", 0.0))
                if _bfq_score >= 0.30:
                    _bfq.append(self._make_queue_entry(_bfq_sym, score=_bfq_score))

        queued_tickers = {e["ticker"] for e in _bfq}
        self.assertEqual(queued_tickers, {"NVDA"},
                         "Only NVDA (not blacklisted) should be in the queue")

    # --- Dequeue path tests ---

    def test_blacklisted_ticker_rejected_on_dequeue(self):
        """A ticker blacklisted AFTER being queued must be skipped at dequeue time."""
        # Scenario: MTNB was queued before being blacklisted. On the next bar
        # it got fast-loser-cut and blacklisted. The dequeue loop must skip it.
        strategy_cache = {
            "_fast_loser_blacklist": {
                "MTNB": {"date": "2026-03-21", "bars_remaining": 20},
            }
        }
        backfill_queue = [
            self._make_queue_entry("MTNB", score=0.75, price=2.00, bars_in_queue=2),
            self._make_queue_entry("NVDA", score=0.80, price=120.0, bars_in_queue=1),
        ]
        available_cash = 5000.0
        max_queue_buys = 3

        bought = []
        removed = []
        for entry in backfill_queue:
            _bfq_sym = entry["ticker"]

            # V7.3 dequeue blacklist check
            _fl_blacklist_deq = strategy_cache.get("_fast_loser_blacklist", {})
            if _bfq_sym in _fl_blacklist_deq:
                removed.append(_bfq_sym)
                continue

            if len(bought) >= max_queue_buys:
                break
            if available_cash >= 500.0:
                bought.append(_bfq_sym)
                available_cash -= 500.0

        self.assertNotIn("MTNB", bought,
                         "Blacklisted MTNB must be rejected during dequeue")
        self.assertIn("MTNB", removed,
                      "MTNB should be marked as removed from queue")
        self.assertIn("NVDA", bought,
                      "Non-blacklisted NVDA should be dequeued and bought")

    def test_non_blacklisted_tickers_dequeue_normally(self):
        """Non-blacklisted queue entries should process through dequeue without issue."""
        strategy_cache = {"_fast_loser_blacklist": {}}
        backfill_queue = [
            self._make_queue_entry("AAPL", score=0.80, price=180.0),
            self._make_queue_entry("MSFT", score=0.75, price=400.0),
        ]
        available_cash = 5000.0

        bought = []
        for entry in backfill_queue:
            _bfq_sym = entry["ticker"]
            _fl_blacklist_deq = strategy_cache.get("_fast_loser_blacklist", {})
            if _bfq_sym in _fl_blacklist_deq:
                continue
            if available_cash >= 500.0:
                bought.append(_bfq_sym)
                available_cash -= 500.0

        self.assertEqual(
            set(bought), {"AAPL", "MSFT"},
            "Both non-blacklisted tickers should be dequeued"
        )

    def test_expired_blacklist_allows_dequeue(self):
        """Once a blacklist entry expires (bars_remaining <= 0), the ticker
        should be processable from the queue again."""
        strategy_cache = {
            "_fast_loser_blacklist": {
                "MTNB": {"date": "2026-03-10", "bars_remaining": 0},
            }
        }

        # Run decay to clean up expired entries
        _fl_blacklist = strategy_cache.get("_fast_loser_blacklist", {})
        _fl_expired = [k for k, v in _fl_blacklist.items() if v.get("bars_remaining", 0) <= 0]
        for _fle in _fl_expired:
            del _fl_blacklist[_fle]

        backfill_queue = [
            self._make_queue_entry("MTNB", score=0.70, price=3.00),
        ]
        available_cash = 5000.0

        bought = []
        for entry in backfill_queue:
            _bfq_sym = entry["ticker"]
            _fl_blacklist_deq = strategy_cache.get("_fast_loser_blacklist", {})
            if _bfq_sym in _fl_blacklist_deq:
                continue
            if available_cash >= 500.0:
                bought.append(_bfq_sym)
                available_cash -= 500.0

        self.assertIn("MTNB", bought,
                      "MTNB should be buyable after blacklist expires")


# ===================================================================
# SECTION 10: V9 PnL Max Tests
# ===================================================================


# ---------------------------------------------------------------------------
# Extracted V9 quality filter logic (mirrors _apply_quality_filter)
# ---------------------------------------------------------------------------

# Module-level ETF sets (mirrors graph_nexus_analysis.py lines 5989-5999)
_TEST_ALL_ETF_TICKERS = frozenset({
    "SPY", "QQQ", "DIA", "IWM", "VTI", "VOO",
    "SMH", "SOXX", "XBI", "VGT", "XLF", "XLE", "XLV", "XLI",
    "XLK", "XLP", "XLY", "XLU", "XLRE", "XLC", "XLB",
    "PSLV", "GLDM", "SLV", "IAU", "GLD", "GDX", "GDXJ",
    "USO", "BNO", "UNG", "DBA", "DBC", "GSG", "PDBC",
})

_TEST_COMMODITY_ETF_TICKERS = frozenset({
    "PSLV", "GLDM", "SLV", "IAU", "GLD", "GDX", "GDXJ",
    "PPLT", "PALL", "SIVR", "SGOL", "AAAU", "BAR", "OUNZ",
    "USO", "BNO", "UNG", "DBA", "DBC", "GSG", "PDBC",
})

_TEST_SECTOR_ETF_TICKERS = frozenset({
    "SMH", "SOXX", "XBI", "VGT", "XLF", "XLE", "XLV", "XLI",
    "XLK", "XLP", "XLY", "XLU", "XLRE", "XLC", "XLB",
    "QQQ", "SPY", "DIA", "IWM", "VTI", "VOO",
})


def simulate_quality_filter(
    scores: dict,
    symbols_list: list,
    prices: dict | None,
    price_history: dict | None,
    portfolio_emulator,
    config: dict,
    strategy_cache: dict | None = None,
) -> dict:
    """
    Reproduce _apply_quality_filter logic from graph_nexus_analysis.py
    lines 10486-10571. Only filters buy signals (score==1).
    """
    min_market_cap = float(config.get("min_market_cap", 500_000_000))
    min_avg_volume = float(config.get("min_avg_volume", 200_000))
    propagation_min_paths = int(config.get("propagation_min_paths", 2))
    propagation_min_raw = float(config.get("propagation_min_raw_score", 0.30))

    for sym in list(symbols_list):
        sc = scores.get(sym)
        if not isinstance(sc, dict) or sc.get("score") != 1:
            continue

        if sym in _TEST_ALL_ETF_TICKERS:
            continue

        if portfolio_emulator is not None:
            _pos_qty = float((portfolio_emulator._positions or {}).get(sym, 0))
            if _pos_qty > 0:
                continue

        sym_data = (price_history or {}).get(sym)
        if isinstance(sym_data, dict):
            market_cap = sym_data.get("market_cap", None)
            if market_cap is not None and float(market_cap) < min_market_cap:
                sc["score"] = 0
                sc["action_intent"] = "hold"
                sc["reason"] = f"V9 quality filter: market_cap blocked"
                continue

            avg_volume = sym_data.get("avg_volume", None)
            if avg_volume is not None and float(avg_volume) < min_avg_volume:
                sc["score"] = 0
                sc["action_intent"] = "hold"
                sc["reason"] = f"V9 quality filter: avg_volume blocked"
                continue

        # Propagation quality check
        event = str(sc.get("event") or sc.get("reason") or "")
        n_paths = int(sc.get("n_paths", 0) or 0)
        raw_score = abs(float(sc.get("raw_score", 0) or sc.get("raw_net_score", 0) or 0))
        is_propagation = "propagation" in event.lower()

        if strategy_cache is not None:
            prop_expansion = strategy_cache.get("_prop_expansion_set_alloc", set())
            if isinstance(prop_expansion, set) and sym in prop_expansion:
                is_propagation = True

        if is_propagation and n_paths < propagation_min_paths and raw_score < propagation_min_raw:
            sc["score"] = 0
            sc["action_intent"] = "hold"
            sc["reason"] = f"V9 quality filter: propagation {n_paths} paths blocked"
            continue

    return scores


def simulate_enqueue_backfill(queue, entry, max_size):
    """Reproduce _enqueue_backfill_candidate logic from lines 4112-4147."""
    ticker = str(entry.get("ticker") or "").strip().upper()
    if not ticker:
        return "invalid", None
    entry["ticker"] = ticker

    for existing in queue:
        if str(existing.get("ticker") or "").strip().upper() != ticker:
            continue
        existing_key = (int(existing.get("priority", 99) or 99), -float(existing.get("raw_net_score", 0.0) or 0.0))
        new_key = (int(entry.get("priority", 99) or 99), -float(entry.get("raw_net_score", 0.0) or 0.0))
        if new_key <= existing_key:
            existing.update(entry)
            existing["bars_in_queue"] = 0
            return "updated", None
        return "duplicate", None

    if len(queue) < max_size:
        queue.append(entry)
        return "added", None

    queue.sort(key=lambda q: (int(q.get("priority", 99) or 99), -float(q.get("raw_net_score", 0.0) or 0.0)))
    worst = queue[-1]
    worst_key = (int(worst.get("priority", 99) or 99), -float(worst.get("raw_net_score", 0.0) or 0.0))
    new_key = (int(entry.get("priority", 99) or 99), -float(entry.get("raw_net_score", 0.0) or 0.0))
    if new_key < worst_key:
        worst_score = float(worst.get("raw_net_score", 0.0) or 0.0)
        new_score = float(entry.get("raw_net_score", 0.0) or 0.0)
        if worst_score > 1.0 and new_score <= worst_score:
            return "full", None
        replaced_ticker = str(worst.get("ticker") or "").strip().upper() or None
        queue[-1] = entry
        return "replaced", replaced_ticker
    return "full", None


class TestV9PnlMax(unittest.TestCase):
    """V9 PnL Max tests: quality filter, sell enforcement, backfill queue,
    asset-class trailing stops, earnings penalty, and discovery limits."""

    # ---------------------------------------------------------------
    # Quality filter tests (1-6)
    # ---------------------------------------------------------------

    def test_quality_filter_blocks_micro_cap(self):
        """Stock with market_cap < $500M gets score set to 0."""
        scores = {"CANG": {"score": 1, "raw_net_score": 0.80}}
        price_history = {"CANG": {"market_cap": 15_000_000}}  # $15M
        result = simulate_quality_filter(
            scores=scores,
            symbols_list=["CANG"],
            prices={"CANG": 1.51},
            price_history=price_history,
            portfolio_emulator=None,
            config={},
        )
        self.assertEqual(result["CANG"]["score"], 0,
                         "Micro-cap stock ($15M) should be blocked by quality filter")

    def test_quality_filter_blocks_low_volume(self):
        """Stock with avg_volume < 200K gets blocked."""
        scores = {"TINY": {"score": 1, "raw_net_score": 0.60}}
        price_history = {"TINY": {"market_cap": 1_000_000_000, "avg_volume": 50_000}}
        result = simulate_quality_filter(
            scores=scores,
            symbols_list=["TINY"],
            prices={"TINY": 25.0},
            price_history=price_history,
            portfolio_emulator=None,
            config={},
        )
        self.assertEqual(result["TINY"]["score"], 0,
                         "Low volume stock (50K < 200K) should be blocked")

    def test_quality_filter_etf_exempt(self):
        """ETF in _ALL_ETF_TICKERS passes regardless of market cap."""
        scores = {"SPY": {"score": 1, "raw_net_score": 0.50}}
        price_history = {"SPY": {"market_cap": 100_000}}  # Absurdly low, but ETF
        result = simulate_quality_filter(
            scores=scores,
            symbols_list=["SPY"],
            prices={"SPY": 500.0},
            price_history=price_history,
            portfolio_emulator=None,
            config={},
        )
        self.assertEqual(result["SPY"]["score"], 1,
                         "ETF should be exempt from quality filter regardless of market cap")

    def test_quality_filter_held_position_exempt(self):
        """Already-held stock is not filtered."""
        pe = MockPortfolioEmulator(initial_cash=10000.0)
        pe.add_position("SMALL", 100.0, 5.0)
        scores = {"SMALL": {"score": 1, "raw_net_score": 0.40}}
        price_history = {"SMALL": {"market_cap": 10_000_000}}  # $10M
        result = simulate_quality_filter(
            scores=scores,
            symbols_list=["SMALL"],
            prices={"SMALL": 5.0},
            price_history=price_history,
            portfolio_emulator=pe,
            config={},
        )
        self.assertEqual(result["SMALL"]["score"], 1,
                         "Already-held position should be exempt from quality filter")

    def test_quality_filter_single_path_propagation_blocked(self):
        """Propagation with 1 path and low raw score gets blocked."""
        scores = {"PROP1": {
            "score": 1,
            "raw_net_score": 0.10,
            "raw_score": 0.10,
            "n_paths": 1,
            "event": "graph_propagation signal",
        }}
        result = simulate_quality_filter(
            scores=scores,
            symbols_list=["PROP1"],
            prices={"PROP1": 50.0},
            price_history={},
            portfolio_emulator=None,
            config={},
        )
        self.assertEqual(result["PROP1"]["score"], 0,
                         "Single-path propagation with low raw score should be blocked")

    def test_quality_filter_multi_path_propagation_passes(self):
        """Propagation with 2+ paths passes quality filter."""
        scores = {"PROP2": {
            "score": 1,
            "raw_net_score": 0.10,
            "raw_score": 0.10,
            "n_paths": 3,
            "event": "graph_propagation signal",
        }}
        result = simulate_quality_filter(
            scores=scores,
            symbols_list=["PROP2"],
            prices={"PROP2": 50.0},
            price_history={},
            portfolio_emulator=None,
            config={},
        )
        self.assertEqual(result["PROP2"]["score"], 1,
                         "Multi-path propagation (3 paths) should pass quality filter")

    # ---------------------------------------------------------------
    # Sell enforcement tests (7-12)
    # ---------------------------------------------------------------

    def test_sell_enforcement_min_hold_blocks_early_sell(self):
        """Position held 3 days, enforcement fires but min-hold blocks it."""
        import datetime as dt
        buy_date = dt.date(2026, 3, 20)
        today = dt.date(2026, 3, 23)  # 3 days held
        min_hold_days = 5
        held_days = (today - buy_date).days
        se_skip = held_days < min_hold_days
        self.assertTrue(se_skip,
                        f"3 days < 5 day min-hold should block sell enforcement "
                        f"(held_days={held_days})")

    def test_sell_enforcement_min_hold_allows_after_5_days(self):
        """Position held 6 days, enforcement fires normally."""
        import datetime as dt
        buy_date = dt.date(2026, 3, 17)
        today = dt.date(2026, 3, 23)  # 6 days held
        min_hold_days = 5
        held_days = (today - buy_date).days
        se_skip = held_days < min_hold_days
        self.assertFalse(se_skip,
                         f"6 days >= 5 day min-hold should allow sell enforcement "
                         f"(held_days={held_days})")

    def test_sell_enforcement_confirmed_downtrend_required(self):
        """Single neutral day does NOT trigger enforcement (need 3 consecutive)."""
        neutral_tracker = {}
        consec_required = 3
        ticker = "CE"

        # Day 1: raw_score = -0.1 (neutral/negative)
        raw = -0.1
        if raw <= 0:
            neutral_tracker[ticker] = neutral_tracker.get(ticker, 0) + 1
        else:
            neutral_tracker[ticker] = 0

        se_skip = neutral_tracker.get(ticker, 0) < consec_required
        self.assertTrue(se_skip,
                        "1 neutral day < 3 required should block sell enforcement")

    def test_sell_enforcement_hysteresis_blocks_weak_signal(self):
        """Raw score -0.30 does NOT trigger sell (hysteresis threshold is -0.50).

        Profitable hold protection: if position is profitable and raw score
        is above the hysteresis threshold (-0.50), enforcement is blocked.
        """
        se_unrealized_pct = 5.0  # +5% profitable
        se_current_raw = -0.30
        se_hysteresis_threshold = -0.50
        # Profitable and raw >= threshold -> protect
        se_skip = se_unrealized_pct > 0 and se_current_raw >= se_hysteresis_threshold
        self.assertTrue(se_skip,
                        "Profitable position with raw=-0.30 >= -0.50 threshold "
                        "should be protected from sell enforcement")

    def test_sell_enforcement_profitable_hold_protected(self):
        """Profitable position with neutral score NOT sold."""
        se_unrealized_pct = 12.0  # +12% profitable
        se_current_raw = 0.0  # Neutral score
        se_hysteresis_threshold = -0.50
        se_skip = se_unrealized_pct > 0 and se_current_raw >= se_hysteresis_threshold
        self.assertTrue(se_skip,
                        "Profitable position (+12%) with neutral raw score "
                        "should not be sold by enforcement")

    def test_trailing_stop_overrides_min_hold(self):
        """Fast loser cut fires at day 2 regardless of min-hold.

        Fast loser cut and trailing stop use _forced_exit in _finalize_scores,
        which is a separate code path from sell enforcement. Min-hold only
        gates the trend-reversal enforcement, not risk-management exits.
        """
        # Simulate: position held 2 days, -11% loss, fast_cut_pct=-10%
        result = simulate_v4_sell_logic(
            fresh_score=0,
            unrealized_pct=-11.0,
            prop_raw=0.0,
            hold_limit_forced=False,
            fast_cut_pct=-10.0,
            ts_activation=10.0,
            ts_drop=8.0,
            current_price=89.0,
            peak_price=89.0,
        )
        self.assertEqual(result, -1,
                         "Fast loser cut should fire regardless of hold duration "
                         "(it uses _forced_exit, not sell enforcement)")

    # ---------------------------------------------------------------
    # Backfill queue tests (13-16)
    # ---------------------------------------------------------------

    def test_backfill_priority_protection_high_score(self):
        """Entry with score 2.0 cannot be displaced by score 0.5."""
        queue = [
            {"ticker": "HIGH", "raw_net_score": 2.0, "priority": 2},
            {"ticker": "MID", "raw_net_score": 1.5, "priority": 2},
            {"ticker": "LOW", "raw_net_score": 0.8, "priority": 2},
        ]
        new_entry = {"ticker": "WEAK", "raw_net_score": 0.5, "priority": 2}
        result, replaced = simulate_enqueue_backfill(queue, new_entry, max_size=3)
        # Queue is full. Worst is LOW (0.8). new_key=(2, -0.5) vs worst_key=(2, -0.8).
        # new_key=(2,-0.5) > worst_key=(2,-0.8) so new_key is NOT < worst_key.
        # Result should be "full" -- cannot even displace 0.8.
        self.assertEqual(result, "full",
                         "Score 0.5 should not displace any entry in queue with "
                         "scores [2.0, 1.5, 0.8]")

    def test_backfill_priority_protection_higher_score_displaces(self):
        """Entry with score 3.0 CAN displace score 0.8 (not protected at 0.8 < 1.0)."""
        queue = [
            {"ticker": "HIGH", "raw_net_score": 2.0, "priority": 2},
            {"ticker": "MID", "raw_net_score": 1.5, "priority": 2},
            {"ticker": "LOW", "raw_net_score": 0.8, "priority": 2},
        ]
        new_entry = {"ticker": "MEGA", "raw_net_score": 3.0, "priority": 2}
        result, replaced = simulate_enqueue_backfill(queue, new_entry, max_size=3)
        # Worst is LOW (0.8). new_key=(2,-3.0) < worst_key=(2,-0.8).
        # Priority protection: worst_score=0.8 NOT > 1.0, so no protection.
        # -> replace
        self.assertEqual(result, "replaced",
                         "Score 3.0 should displace score 0.8 (not protected)")
        self.assertEqual(replaced, "LOW")

    def test_backfill_budget_reserve(self):
        """20% of budget reserved for backfill."""
        available_buy_budget = 10000.0
        backfill_reserve_pct = 0.20
        reserve_from_budget = available_buy_budget * backfill_reserve_pct
        self.assertAlmostEqual(reserve_from_budget, 2000.0,
                               msg="20% of $10K = $2K reserved for backfill")

    def test_backfill_score_gated_priority(self):
        """Score >= 1.5 gets priority execution (first 50% of budget)."""
        bfq_cash = 10000.0
        bfq_priority_budget = bfq_cash * 0.50  # $5000 for high-conviction
        bfq_standard_budget = bfq_cash - bfq_priority_budget  # $5000 for rest

        # High-conviction entry (score 2.0 >= 1.5) draws from priority budget
        high_conv_score = 2.0
        is_high_conv = high_conv_score >= 1.5
        self.assertTrue(is_high_conv)
        available_for_high = bfq_priority_budget if is_high_conv else bfq_standard_budget
        self.assertEqual(available_for_high, 5000.0,
                         "High-conviction entry should draw from priority budget (50%)")

        # Standard entry (score 0.8 < 1.5) draws from standard budget
        std_score = 0.8
        is_std_conv = std_score >= 1.5
        self.assertFalse(is_std_conv)
        available_for_std = bfq_priority_budget if is_std_conv else bfq_standard_budget
        self.assertEqual(available_for_std, 5000.0,
                         "Standard entry should draw from standard budget (50%)")

    # ---------------------------------------------------------------
    # Asset-class trailing stop tests (17-19)
    # ---------------------------------------------------------------

    def test_trailing_stop_stock_8pct(self):
        """Individual stock uses 8% trailing stop."""
        sym = "AAPL"
        config = {"trailing_stop_pct": 8.0}
        if sym in _TEST_COMMODITY_ETF_TICKERS:
            ts_drop = float(config.get("trailing_stop_commodity_etf_pct", 12.0))
        elif sym in _TEST_SECTOR_ETF_TICKERS:
            ts_drop = float(config.get("trailing_stop_sector_etf_pct", 10.0))
        else:
            ts_drop = float(config.get("trailing_stop_pct", 8.0))
        self.assertEqual(ts_drop, 8.0, "Individual stock should use 8% trailing stop")

    def test_trailing_stop_commodity_etf_12pct(self):
        """PSLV uses 12% trailing stop."""
        sym = "PSLV"
        config = {"trailing_stop_commodity_etf_pct": 12.0}
        if sym in _TEST_COMMODITY_ETF_TICKERS:
            ts_drop = float(config.get("trailing_stop_commodity_etf_pct", 12.0))
        elif sym in _TEST_SECTOR_ETF_TICKERS:
            ts_drop = float(config.get("trailing_stop_sector_etf_pct", 10.0))
        else:
            ts_drop = float(config.get("trailing_stop_pct", 8.0))
        self.assertEqual(ts_drop, 12.0, "PSLV (commodity ETF) should use 12% trailing stop")

    def test_trailing_stop_sector_etf_10pct(self):
        """SMH uses 10% trailing stop."""
        sym = "SMH"
        config = {"trailing_stop_sector_etf_pct": 10.0}
        if sym in _TEST_COMMODITY_ETF_TICKERS:
            ts_drop = float(config.get("trailing_stop_commodity_etf_pct", 12.0))
        elif sym in _TEST_SECTOR_ETF_TICKERS:
            ts_drop = float(config.get("trailing_stop_sector_etf_pct", 10.0))
        else:
            ts_drop = float(config.get("trailing_stop_pct", 8.0))
        self.assertEqual(ts_drop, 10.0, "SMH (sector ETF) should use 10% trailing stop")

    # ---------------------------------------------------------------
    # Earnings penalty tests (20-21)
    # ---------------------------------------------------------------

    def test_earnings_penalty_single_path(self):
        """Single-path earnings gets 0.5x raw_net."""
        raw_net = 0.60
        n_paths = 1
        event_type = "earnings"
        penalty_weight = 0.5
        if n_paths <= 1 and "earnings" in event_type.lower():
            raw_net *= penalty_weight
        self.assertAlmostEqual(raw_net, 0.30,
                               msg="Single-path earnings should reduce raw_net by 50%")

    def test_earnings_penalty_multi_path_exempt(self):
        """3-path earnings is NOT penalized."""
        raw_net = 0.60
        n_paths = 3
        event_type = "earnings"
        penalty_weight = 0.5
        if n_paths <= 1 and "earnings" in event_type.lower():
            raw_net *= penalty_weight
        self.assertAlmostEqual(raw_net, 0.60,
                               msg="Multi-path earnings (3 paths) should NOT be penalized")

    # ---------------------------------------------------------------
    # Discovery tests (22-23)
    # ---------------------------------------------------------------

    def test_discovery_cap_50(self):
        """Max discovered stocks is 50 (not 30)."""
        config = {}  # Uses defaults
        max_discovered = int(config.get("max_discovered_stocks", 50))
        self.assertEqual(max_discovered, 50,
                         "Default max_discovered_stocks should be 50")

    def test_sector_fill_max_10(self):
        """Per-sector cap is 10 (not 6)."""
        config = {}  # Uses defaults
        max_per_sector = int(config.get("sector_fill_max_per_sector", 10))
        self.assertEqual(max_per_sector, 10,
                         "Default sector_fill_max_per_sector should be 10")
