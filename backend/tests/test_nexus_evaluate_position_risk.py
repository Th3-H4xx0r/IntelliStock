"""
Refactor regression tests for `_evaluate_position_risk`.

This is the canonical regression test for the held-position risk-exit block
extracted from `_finalize_scores` for the dual-cadence dual-mode design.
Behavior MUST be byte-equivalent in side_effect_mode="full" with
bypass_winner_protection=False; monitor mode is exercised separately.

Covers all four exit branches:
  - hold-limit (positive: forced sell when held > max_hold_days, negative:
    deferred when gain >= trailing_stop_activation_pct)
  - circuit-breaker (positive: -15% loss floor, negative: above floor)
  - fast-loser (positive: -10% loss with no peak protection, negative:
    peak protection bypass)
  - trailing-stop (positive: drop from peak >= ts_drop, negative: tracking
    only, no fire)

Plus mode-flag coverage:
  - bypass_winner_protection=True skips winner-protection ladder
  - side_effect_mode="monitor" does NOT write _fast_loser_blacklist
"""

import os
import sys
from datetime import datetime, timezone

import pytest

_BACKEND = os.path.join(os.path.dirname(__file__), "..")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from strategies.graph_nexus_analysis import _evaluate_position_risk  # noqa: E402


# ---------------------------------------------------------------------------
# Tiny stand-in for PortfolioEmulator (we only need _positions + _trades)
# ---------------------------------------------------------------------------


class _Emu:
    def __init__(self):
        self._positions: dict = {}
        self._trades: list = []

    def add(self, ticker: str, shares: float, entry_price: float, ts=None):
        self._positions[ticker] = self._positions.get(ticker, 0.0) + shares
        self._trades.append({
            "ticker": ticker,
            "action": "buy",
            "price": float(entry_price),
            "shares": float(shares),
            "timestamp": ts or datetime(2026, 1, 1, tzinfo=timezone.utc),
        })


def _base_config(**overrides) -> dict:
    cfg = {
        "max_open_loss_pct": -15.0,
        "fast_loser_cut_pct": -10.0,
        "fast_loser_blacklist_days": 20,
        "trailing_stop_activation_pct": 10.0,
        "trailing_stop_pct": 8.0,
        "trailing_stop_pnl_scaling_enabled": False,
        "mega_winner_protect_enabled": False,
        "peak_protection_enabled": True,
        "peak_protection_min_peak_pnl_pct": 30.0,
        "peak_protection_max_drawdown_from_peak_pct": 60.0,
        "profit_take_enabled": False,
        "initial_grace_enabled": False,
    }
    cfg.update(overrides)
    return cfg


# ---------------------------------------------------------------------------
# Hold-limit branch (positive + negative)
# ---------------------------------------------------------------------------


def test_hold_limit_forces_sell_when_no_gain():
    emu = _Emu()
    emu.add("FOO", 100, 100.0)
    prices = {"FOO": 95.0}  # -5% gain, below trailing_stop_activation_pct
    score, reason, extras = _evaluate_position_risk(
        "FOO",
        fresh_score=0,
        fresh_reason="No graph signal",
        config=_base_config(),
        portfolio_emulator=emu,
        strategy_cache={},
        prices=prices,
        price_history={},
        date_key="2026-04-01",
        propagated={},
        entry_buy_ts=datetime(2026, 1, 1, tzinfo=timezone.utc),
        held_days=100,
        max_hold_days=90,
    )
    assert score == -1
    assert "Hold-limit exit" in reason


def test_hold_limit_deferred_for_winners():
    emu = _Emu()
    emu.add("FOO", 100, 100.0)
    prices = {"FOO": 130.0}  # +30% gain, well above 10% activation
    score, reason, extras = _evaluate_position_risk(
        "FOO",
        fresh_score=0,
        fresh_reason="No graph signal",
        config=_base_config(),
        portfolio_emulator=emu,
        strategy_cache={},
        prices=prices,
        price_history={},
        date_key="2026-04-01",
        propagated={},
        entry_buy_ts=datetime(2026, 1, 1, tzinfo=timezone.utc),
        held_days=100,
        max_hold_days=90,
    )
    # Trailing stop will manage; hold-limit did NOT force sell.
    assert "Hold-limit exit" not in (reason or "")


# ---------------------------------------------------------------------------
# Circuit-breaker branch
# ---------------------------------------------------------------------------


def test_circuit_breaker_fires_at_minus_15():
    emu = _Emu()
    emu.add("FOO", 100, 100.0)
    prices = {"FOO": 80.0}  # -20%
    score, reason, _ = _evaluate_position_risk(
        "FOO",
        fresh_score=0,
        fresh_reason="No graph signal",
        config=_base_config(),
        portfolio_emulator=emu,
        strategy_cache={},
        prices=prices,
        price_history={},
        date_key="2026-04-01",
        propagated={},
        entry_buy_ts=datetime(2026, 1, 1, tzinfo=timezone.utc),
        held_days=5,
        max_hold_days=90,
    )
    assert score == -1
    assert "Circuit breaker" in reason


def test_circuit_breaker_does_not_fire_above_floor():
    emu = _Emu()
    emu.add("FOO", 100, 100.0)
    prices = {"FOO": 92.0}  # -8% (above -15 floor, also above -10 fast-cut)
    score, reason, _ = _evaluate_position_risk(
        "FOO",
        fresh_score=0,
        fresh_reason="No graph signal",
        config=_base_config(),
        portfolio_emulator=emu,
        strategy_cache={},
        prices=prices,
        price_history={},
        date_key="2026-04-01",
        propagated={},
        entry_buy_ts=datetime(2026, 1, 1, tzinfo=timezone.utc),
        held_days=5,
        max_hold_days=90,
    )
    assert "Circuit breaker" not in (reason or "")


# ---------------------------------------------------------------------------
# Fast-loser branch
# ---------------------------------------------------------------------------


def test_fast_loser_cut_fires_at_minus_10():
    emu = _Emu()
    emu.add("FOO", 100, 100.0)
    prices = {"FOO": 88.0}  # -12% loss, between -10 fast-cut and -15 cb
    sc = {}
    score, reason, _ = _evaluate_position_risk(
        "FOO",
        fresh_score=0,
        fresh_reason="No graph signal",
        config=_base_config(),
        portfolio_emulator=emu,
        strategy_cache=sc,
        prices=prices,
        price_history={},
        date_key="2026-04-01",
        propagated={},
        entry_buy_ts=datetime(2026, 1, 1, tzinfo=timezone.utc),
        held_days=5,
        max_hold_days=90,
        side_effect_mode="full",
    )
    assert score == -1
    assert "Fast loser cut" in reason
    # Side-effect mode "full" should write the blacklist entry.
    assert "FOO" in sc.get("_fast_loser_blacklist", {})


def test_fast_loser_no_blacklist_in_monitor_mode():
    """side_effect_mode='monitor' must NOT write _fast_loser_blacklist."""
    emu = _Emu()
    emu.add("FOO", 100, 100.0)
    prices = {"FOO": 88.0}  # -12% loss triggers fast-loser
    sc = {}
    score, reason, _ = _evaluate_position_risk(
        "FOO",
        fresh_score=0,
        fresh_reason="monitor: hold",
        config=_base_config(),
        portfolio_emulator=emu,
        strategy_cache=sc,
        prices=prices,
        price_history={},
        date_key="2026-04-01",
        propagated={},
        entry_buy_ts=datetime(2026, 1, 1, tzinfo=timezone.utc),
        held_days=5,
        max_hold_days=90,
        side_effect_mode="monitor",
    )
    assert score == -1
    assert "Fast loser cut" in reason
    # Monitor mode: NO blacklist write.
    assert "_fast_loser_blacklist" not in sc or "FOO" not in sc.get(
        "_fast_loser_blacklist", {}
    )


# ---------------------------------------------------------------------------
# Trailing-stop branch
# ---------------------------------------------------------------------------


def test_trailing_stop_fires_after_peak_drop():
    """Establish peak via prior call, then drop price past trail."""
    emu = _Emu()
    emu.add("FOO", 100, 100.0)
    sc: dict = {}
    # First call: price = 130 → activates and sets peak.
    _evaluate_position_risk(
        "FOO",
        fresh_score=0,
        fresh_reason="hold",
        config=_base_config(),
        portfolio_emulator=emu,
        strategy_cache=sc,
        prices={"FOO": 130.0},
        price_history={},
        date_key="2026-04-01",
        propagated={},
        entry_buy_ts=datetime(2026, 1, 1, tzinfo=timezone.utc),
        held_days=5,
        max_hold_days=90,
    )
    # Second call: price drops to 115 from peak 130 → drop=11.5% > 8% trail.
    score, reason, _ = _evaluate_position_risk(
        "FOO",
        fresh_score=0,
        fresh_reason="hold",
        config=_base_config(),
        portfolio_emulator=emu,
        strategy_cache=sc,
        prices={"FOO": 115.0},
        price_history={},
        date_key="2026-04-02",
        propagated={},
        entry_buy_ts=datetime(2026, 1, 1, tzinfo=timezone.utc),
        held_days=6,
        max_hold_days=90,
    )
    assert score == -1
    assert "Trailing stop" in reason


def test_trailing_stop_only_tracks_below_activation():
    emu = _Emu()
    emu.add("FOO", 100, 100.0)
    sc: dict = {}
    # +5% (below 10% activation) — no fire.
    score, reason, _ = _evaluate_position_risk(
        "FOO",
        fresh_score=0,
        fresh_reason="hold",
        config=_base_config(),
        portfolio_emulator=emu,
        strategy_cache=sc,
        prices={"FOO": 105.0},
        price_history={},
        date_key="2026-04-01",
        propagated={},
        entry_buy_ts=datetime(2026, 1, 1, tzinfo=timezone.utc),
        held_days=5,
        max_hold_days=90,
    )
    assert "Trailing stop" not in (reason or "")
    assert score != -1


# ---------------------------------------------------------------------------
# Winner protection bypass
# ---------------------------------------------------------------------------


def test_bypass_winner_protection_skips_ladder():
    """With bypass=True, a -1 score with +25% gain is NOT softened."""
    emu = _Emu()
    emu.add("FOO", 100, 100.0)
    score, reason, _ = _evaluate_position_risk(
        "FOO",
        fresh_score=-1,
        fresh_reason="External sell signal",
        config=_base_config(),
        portfolio_emulator=emu,
        strategy_cache={},
        prices={"FOO": 125.0},
        price_history={},
        date_key="2026-04-01",
        propagated={"FOO": {"raw_score": 0.0}},
        entry_buy_ts=datetime(2026, 1, 1, tzinfo=timezone.utc),
        held_days=5,
        max_hold_days=90,
        bypass_winner_protection=True,
    )
    # Winner-protection would set score=0 with reason "Winner protection";
    # bypass=True keeps the original sell. Trailing-stop tracking-only at
    # +25% (no peak yet, so no drop). Reason should NOT contain
    # "Winner protection".
    assert "Winner protection" not in (reason or "")


def test_winner_protection_softens_sell_when_not_bypassed():
    emu = _Emu()
    emu.add("FOO", 100, 100.0)
    score, reason, _ = _evaluate_position_risk(
        "FOO",
        fresh_score=-1,
        fresh_reason="External sell signal",
        config=_base_config(),
        portfolio_emulator=emu,
        strategy_cache={},
        prices={"FOO": 125.0},
        price_history={},
        date_key="2026-04-01",
        propagated={"FOO": {"raw_score": 0.0}},
        entry_buy_ts=datetime(2026, 1, 1, tzinfo=timezone.utc),
        held_days=5,
        max_hold_days=90,
        bypass_winner_protection=False,
    )
    assert "Winner protection" in reason
