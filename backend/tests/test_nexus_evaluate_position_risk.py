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
# Fast-loser hard floor vs. recent-runup carve-out (CRWV 2026-07 regression)
# ---------------------------------------------------------------------------


def _crwv_config():
    return _base_config(
        fast_loser_cut_pct=-10.0,
        fast_loser_cut_pct_high_vol=-18.0,
        fast_loser_cut_recent_runup_block_pct=40.0,
        fast_loser_cut_recent_runup_lookback_bars=20,
    )


def test_fast_loser_hard_floor_beats_runup_block():
    """CRWV 2026-07: -19.5% with a >40% recent runup. The recent-runup carve-out
    may defer fast-loser cuts between -10% and -15%, but beyond the circuit-
    breaker floor (-15%, tighter than the -18% high-vol threshold) the cut MUST
    fire regardless of runup history — no carve-out can defer it that far.

    On current code the -15% circuit breaker enforces this hard floor; the
    fast-loser runup carve-out is unreachable at -19.5% because the circuit
    breaker already set the sell before the fast-loser block runs.
    """
    cfg = _crwv_config()
    emu = _Emu()
    emu.add("CRWV", 5.61, 104.5534,
            ts=datetime(2026, 6, 25, 13, 35, tzinfo=timezone.utc))
    # price history with a +50%+ runup within the 20-bar lookback, now 84.21
    price_history = {"CRWV": [{"close": c} for c in
                     [58.0, 70.0, 84.0, 92.0, 88.0, 84.21]]}  # runup=58.6%>40
    score, reason, _ = _evaluate_position_risk(
        "CRWV",
        fresh_score=0,
        fresh_reason="No graph signal",
        config=cfg,
        portfolio_emulator=emu,
        strategy_cache={},
        prices={"CRWV": 84.21},  # (84.21-104.5534)/104.5534 = -19.5%
        price_history=price_history,
        date_key="2026-07-02",
        propagated={},
        entry_buy_ts=datetime(2026, 6, 25, 13, 35, tzinfo=timezone.utc),
        held_days=7,
        max_hold_days=90,
        side_effect_mode="full",
    )
    # The cut MUST fire — the runup carve-out never suppresses a -19.5% loss.
    assert score == -1
    assert ("Circuit breaker" in reason) or ("Fast loser" in reason)


def test_runup_block_still_defers_between_cut_and_high_vol():
    """Inverse guard: at -12% (past the -10% fast-cut, above the -15% circuit-
    breaker floor) with a >40% recent runup, the recent-runup carve-out DOES
    still defer the fast-loser cut. This confirms the carve-out remains active
    in its intended band and is only overridden by the hard floor beyond it."""
    cfg = _crwv_config()
    emu = _Emu()
    emu.add("CRWV", 5.61, 100.0)
    price_history = {"CRWV": [{"close": c} for c in
                     [50.0, 60.0, 75.0, 90.0, 88.0]]}  # runup=80%>40
    score, reason, _ = _evaluate_position_risk(
        "CRWV",
        fresh_score=0,
        fresh_reason="No graph signal",
        config=cfg,
        portfolio_emulator=emu,
        strategy_cache={},
        prices={"CRWV": 88.0},  # -12%
        price_history=price_history,
        date_key="2026-07-02",
        propagated={},
        entry_buy_ts=datetime(2026, 6, 25, 13, 35, tzinfo=timezone.utc),
        held_days=7,
        max_hold_days=90,
        side_effect_mode="full",
    )
    # Runup carve-out defers the cut in the -10%..-15% band → NOT a sell.
    assert score != -1
    assert "Fast loser" not in (reason or "")


def test_hard_floor_cut_fires_in_monitor_mode():
    """The -19.5% hard-floor cut must also fire in side_effect_mode='monitor'
    for a position entered days earlier — the monitor cadence performs the same
    risk-exit evaluation, only suppressing the blacklist side effect."""
    cfg = _crwv_config()
    emu = _Emu()
    emu.add("CRWV", 5.61, 104.5534,
            ts=datetime(2026, 6, 25, 13, 35, tzinfo=timezone.utc))
    price_history = {"CRWV": [{"close": c} for c in
                     [58.0, 70.0, 84.0, 92.0, 88.0, 84.21]]}
    sc: dict = {}
    score, reason, _ = _evaluate_position_risk(
        "CRWV",
        fresh_score=0,
        fresh_reason="monitor: hold",
        config=cfg,
        portfolio_emulator=emu,
        strategy_cache=sc,
        prices={"CRWV": 84.21},
        price_history=price_history,
        date_key="2026-07-02",
        propagated={},
        entry_buy_ts=datetime(2026, 6, 25, 13, 35, tzinfo=timezone.utc),
        held_days=7,
        max_hold_days=90,
        side_effect_mode="monitor",
    )
    assert score == -1
    assert ("Circuit breaker" in reason) or ("Fast loser" in reason)
    # Monitor mode must not create a fast-loser blacklist entry.
    assert "_fast_loser_blacklist" not in sc or "CRWV" not in sc.get(
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
# Risk-pipeline SKIP loud alert (silent-bypass guard)
# ---------------------------------------------------------------------------


def test_risk_pipeline_skip_alerts_once_per_sym_per_day(monkeypatch):
    """When price/entry is unresolvable (all risk gates bypassed), a live
    instance pages operators exactly once per (sym, PT-date). Dedup state
    lives in strategy_cache['_risk_skip_alerted']; backtests/tests without an
    _instance_id stay silent."""
    import strategies.graph_nexus_analysis as gna

    calls: list = []
    monkeypatch.setattr(
        gna, "_alert_risk_pipeline_skip",
        lambda instance_id, sym: calls.append((instance_id, sym)),
    )

    emu = _Emu()
    emu.add("FOO", 100, 100.0)
    emu._instance_id = "alpaca-main"  # marks a live instance
    sc: dict = {}
    # No price for FOO anywhere → _cp resolves <= 0 → risk pipeline SKIP.
    for _ in range(2):
        gna._evaluate_position_risk(
            "FOO",
            fresh_score=0,
            fresh_reason="No graph signal",
            config=_base_config(),
            portfolio_emulator=emu,
            strategy_cache=sc,
            prices={},
            price_history={},
            date_key="2026-07-02",
            propagated={},
            entry_buy_ts=datetime(2026, 1, 1, tzinfo=timezone.utc),
            held_days=5,
            max_hold_days=90,
        )
    # Fired once despite two evaluations on the same day.
    assert len(calls) == 1
    assert calls[0] == ("alpaca-main", "FOO")
    assert any(k.startswith("FOO|") for k in sc.get("_risk_skip_alerted", {}))


def test_risk_pipeline_skip_silent_without_instance_id():
    """A non-live PortfolioEmulator (no _instance_id) never pages — no alert
    state is created."""
    emu = _Emu()
    emu.add("FOO", 100, 100.0)
    sc: dict = {}
    _evaluate_position_risk(
        "FOO",
        fresh_score=0,
        fresh_reason="No graph signal",
        config=_base_config(),
        portfolio_emulator=emu,
        strategy_cache=sc,
        prices={},
        price_history={},
        date_key="2026-07-02",
        propagated={},
        entry_buy_ts=datetime(2026, 1, 1, tzinfo=timezone.utc),
        held_days=5,
        max_hold_days=90,
    )
    assert "_risk_skip_alerted" not in sc


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
