"""ADVERSARIAL BUG SWEEP — demonstrating tests for the exit-discipline /
cost-model changes. NOT FOR COMMIT.

Each test asserts the behaviour a reviewer would expect. A FAILING test is a
CONFIRMED finding. Run:

    cd backend && python3 -m pytest tests/test_adv_exit_discipline_findings.py -q
"""
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

_BACKEND = os.path.join(os.path.dirname(__file__), "..")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from portfolio_emulator import PortfolioEmulator  # noqa: E402
from strategies.graph_nexus_analysis import (  # noqa: E402
    _apply_rank_band_gate,
    _evaluate_position_risk,
    _rank_band_cutoffs,
)

T0 = datetime(2026, 4, 1, 14, 30, tzinfo=timezone.utc)


class _Emu:
    def __init__(self):
        self._positions = {}
        self._trades = []

    def add(self, t, s, px):
        self._positions[t] = self._positions.get(t, 0.0) + s
        self._trades.append({
            "ticker": t, "action": "buy", "price": float(px), "shares": float(s),
            "timestamp": datetime(2026, 1, 1, tzinfo=timezone.utc)})

    def get_positions(self):
        return dict(self._positions)

    def get_trade_history(self):
        return list(self._trades)


def _risk(sym, cfg, emu, price, *, mode="full", cache=None, held_days=30,
          max_hold=0):
    return _evaluate_position_risk(
        sym, fresh_score=0, fresh_reason="No graph signal", config=cfg,
        portfolio_emulator=emu, strategy_cache={} if cache is None else cache,
        prices={sym: price}, price_history={}, date_key="2026-04-01",
        propagated={}, entry_buy_ts=datetime(2026, 1, 1, tzinfo=timezone.utc),
        held_days=held_days, max_hold_days=max_hold, side_effect_mode=mode)


def _ride(cfg, path, *, held_days=30, max_hold=0):
    """Walk a price path, return (exit_price, reason) or None."""
    emu = _Emu()
    emu.add("FOO", 10, 100.0)
    cache = {}
    for px in path:
        s, r, _ = _risk("FOO", cfg, emu, float(px), cache=cache,
                        held_days=held_days, max_hold=max_hold)
        if s == -1:
            return (px, r)
    return None


# doc-179 live values, read from the production Strategies doc 2026-08-02.
_LIVE = {
    "trailing_stop_pct": 10.0,
    "trailing_stop_activation_pct": 8.0,
    "max_hold_days": 90,
    "fast_loser_cut_pct": -15.0,
}

_UP_THEN_DOWN = [110, 130, 160, 200] + list(range(195, 55, -5))


# ===========================================================================
# F1 — trailing_stop_disabled removes the ONLY peak-referenced protection.
# ===========================================================================

def test_F1_disabling_the_trail_does_not_cost_the_whole_gain():
    base = dict(_LIVE)
    off = dict(_LIVE, trailing_stop_disabled=True,
               catastrophic_stop_enabled=True, catastrophic_stop_pct=-20.0)
    with_trail = _ride(base, _UP_THEN_DOWN)
    without = _ride(off, _UP_THEN_DOWN)
    print(f"\n  trail ON : exit @{with_trail[0]} -> {with_trail[1][:70]}")
    print(f"  trail OFF: exit @{without[0]} -> {without[1][:70]}")
    # A "-20% catastrophic stop" replacing an 10%-from-peak trail should not
    # turn a +40% realised exit into a -15% one on the same path.
    assert without[0] >= 100, (
        f"a +100% winner exits at {without[0]} (={without[0]-100:+}%) with the "
        f"trail off vs {with_trail[0]} with it on")


def test_F2_the_catastrophic_stop_is_reachable_at_default_tier_floors():
    """The -20% floor must actually be the thing that fires, or it is inert."""
    off = dict(_LIVE, trailing_stop_disabled=True,
               catastrophic_stop_enabled=True, catastrophic_stop_pct=-20.0)
    exit_px, reason = _ride(off, _UP_THEN_DOWN)
    print(f"\n  exit @{exit_px}: {reason[:90]}")
    assert "Catastrophic stop" in reason, (
        "the circuit-breaker LOW-tier floor (-15%) always fires before the "
        "-20% catastrophic stop, so enabling it changes nothing")


def test_F3_enabling_the_catastrophic_stop_changes_the_outcome():
    a = _ride(dict(_LIVE, trailing_stop_disabled=True,
                   catastrophic_stop_enabled=False), _UP_THEN_DOWN)
    b = _ride(dict(_LIVE, trailing_stop_disabled=True,
                   catastrophic_stop_enabled=True,
                   catastrophic_stop_pct=-20.0), _UP_THEN_DOWN)
    print(f"\n  cat_stop OFF -> {a}\n  cat_stop ON  -> {b}")
    assert a != b, "catastrophic_stop_enabled is a no-op on this path"


def test_F4_a_positive_catastrophic_stop_pct_is_not_silently_inert():
    """Sign footgun: `catastrophic_stop_pct: 20` means "20% loss" to a human
    and "never fire" to the code, with no warning."""
    cfg = {"max_open_loss_pct": -95.0, "fast_loser_cut_pct": -95.0,
           "trailing_stop_activation_pct": 999.0,
           "peak_protection_enabled": False,
           "catastrophic_stop_enabled": True, "catastrophic_stop_pct": 20.0}
    emu = _Emu()
    emu.add("FOO", 10, 100.0)
    score, reason, _ = _risk("FOO", cfg, emu, 60.0)     # -40%
    print(f"\n  catastrophic_stop_pct=+20 at -40%: score={score} reason={reason[:60]}")
    assert score == -1, "a positive pct disarms the stop with no error or log"


# ===========================================================================
# F5 — degenerate rank bands freeze every signal exit.
# ===========================================================================

def _band_book(entry, exit_):
    syms = [f"S{i:02d}" for i in range(10)]
    scores = {}
    for i, s in enumerate(syms):
        scores[s] = {"score": -1, "raw_net_score": -0.5 - 0.01 * i,
                     "reason": "Graph: downgrade", "ml": {}}
    emu = _Emu()
    for s in syms:
        emu.add(s, 10, 100.0)
    _apply_rank_band_gate(scores, syms, emu, {
        "rank_band_enabled": True, "rank_band_entry_pct": entry,
        "rank_band_exit_pct": exit_, "rotation_ml_weight": 0.0})
    return [s for s in syms if scores[s]["score"] == -1]


def test_F5_inverted_band_degrades_to_no_band_as_documented():
    """_rank_band_cutoffs' docstring: clamping "degrades that typo to 'buy and
    sell at the same rank', which is exactly today's no-band behaviour"."""
    assert _rank_band_cutoffs(100, 100.0, 50.0) == (100, 100)
    survivors = _band_book(100.0, 50.0)
    print(f"\n  entry=100 exit=50 -> signal sells surviving: {survivors}")
    assert len(survivors) == 10, (
        "the inverted-band typo freezes EVERY signal sell, it does not "
        "reproduce no-band behaviour")


def test_F6_exit_pct_100_still_permits_a_signal_exit():
    survivors = _band_book(10.0, 100.0)
    print(f"\n  entry=10 exit=100 -> signal sells surviving: {survivors}")
    assert survivors, "rank_band_exit_pct=100 disables every signal sell forever"


def test_F7_a_one_name_universe_can_still_be_exited():
    """When discovery collapses (LLM outage / news failure) symbols_list is
    just the held book. exit_cut is floored at 1, so rank #1 of 1 is inside
    the hold band and the only position can never be sold on signal."""
    scores = {"AAA": {"score": -1, "raw_net_score": -0.9,
                      "reason": "Graph: downgrade", "ml": {}}}
    emu = _Emu()
    emu.add("AAA", 10, 100.0)
    _apply_rank_band_gate(scores, ["AAA"], emu, {
        "rank_band_enabled": True, "rank_band_entry_pct": 10.0,
        "rank_band_exit_pct": 50.0, "rotation_ml_weight": 0.0})
    print(f"\n  1-name universe: score={scores['AAA']['score']} "
          f"reason={scores['AAA']['reason'][:90]}")
    assert scores["AAA"]["score"] == -1


def test_F8_band_is_symmetric_when_positions_cannot_be_enumerated():
    """portfolio_emulator=None (the live historical-lookback pre-pass, and any
    caller that omits it) gates BUYS but lets every signal sell through."""
    syms = [f"S{i:02d}" for i in range(10)]
    scores = {s: {"score": 0, "raw_net_score": 1.0 - 0.1 * i, "reason": "g",
                  "ml": {}} for i, s in enumerate(syms)}
    scores["S07"] = {"score": 1, "raw_net_score": 0.3, "reason": "g", "ml": {}}
    scores["S02"] = {"score": -1, "raw_net_score": 0.8, "reason": "Graph: dg",
                     "ml": {}}
    _apply_rank_band_gate(scores, syms, None, {
        "rank_band_enabled": True, "rank_band_entry_pct": 10.0,
        "rank_band_exit_pct": 50.0, "rotation_ml_weight": 0.0})
    print(f"\n  emulator=None: buy S07={scores['S07']['score']} "
          f"sell S02={scores['S02']['score']}")
    assert (scores["S07"]["score"], scores["S02"]["score"]) in [(1, -1), (0, 0)], (
        "band blocks buys but not sells when positions are unknowable — a "
        "one-way de-risking ratchet the backtest never exercises")


# ===========================================================================
# F9/F10 — emulator: dust and the dividend clock.
# ===========================================================================

def test_F9_sub_dollar_dust_does_not_generate_unbounded_reject_telemetry():
    p = PortfolioEmulator(initial_cash=100000.0)
    p.buy("DUST", 10.0, 100.0, timestamp=T0)
    p.sell("DUST", 9.995, 100.0, timestamp=T0 + timedelta(days=2))
    for attempt in range(200):
        p.sell("DUST", p._positions.get("DUST", 0.0), 100.0,
               timestamp=T0 + timedelta(days=3 + attempt))
    rs = p.get_realism_summary()
    print(f"\n  stuck qty={p._positions.get('DUST')} "
          f"rejects={rs['sub_minimum_rejected_order_count']} "
          f"reject_notional=${rs['sub_minimum_rejected_notional']}")
    assert rs["sub_minimum_rejected_order_count"] <= 1, (
        "one un-closable $0.50 dust position re-rejects every bar, so "
        "sub_minimum_rejected_order_count — the headline realism diagnostic — "
        "counts retries, not distinct impossible orders")


def test_F10_dividend_accrual_clock_is_monotonic_like_the_settlement_clock():
    p = PortfolioEmulator(initial_cash=1_000_000.0,
                          dividend_yields={"QQQ": 0.0050})
    p.buy("QQQ", 1000, 500.0, timestamp=T0)
    p.save_portfolio_snapshot({"QQQ": 500.0}, timestamp=T0)
    p.save_portfolio_snapshot({"QQQ": 500.0}, timestamp=T0 + timedelta(days=90))
    once = p._dividends_credited
    p.save_portfolio_snapshot({"QQQ": 500.0}, timestamp=T0 + timedelta(days=1))
    p.save_portfolio_snapshot({"QQQ": 500.0}, timestamp=T0 + timedelta(days=90))
    print(f"\n  one 90d pass = ${once:.2f}; after an out-of-order replay = "
          f"${p._dividends_credited:.2f}; _clock (monotonic) = {p._clock}")
    assert p._dividends_credited == pytest.approx(once), (
        "_dividend_accrual_clock is assigned before the elapsed<=0 guard, so "
        "an out-of-order timestamp rewinds it and the same calendar period is "
        "credited twice")


def test_F11_dividends_accrue_on_the_position_held_over_the_interval():
    p = PortfolioEmulator(initial_cash=1_000_000.0,
                          dividend_yields={"QQQ": 0.0050})
    p.save_portfolio_snapshot({}, timestamp=T0)
    p.buy("QQQ", 1000, 500.0, timestamp=T0 + timedelta(days=60))
    p.save_portfolio_snapshot({"QQQ": 500.0}, timestamp=T0 + timedelta(days=60))
    print(f"\n  QQQ held 0 days, credited ${p._dividends_credited:.2f}")
    assert p._dividends_credited == pytest.approx(0.0), (
        "a snapshot gap credits the END-of-interval position for the WHOLE "
        "interval — the index core is exactly the position this over-credits")


# ===========================================================================
# Things that PASS — recorded so the report can say where nothing was found.
# ===========================================================================

def test_OK_cost_model_is_symmetric_and_not_double_charged():
    p = PortfolioEmulator(initial_cash=100000.0)
    assert p._equity_cost_model.version == "equity-measured-v3-nbbo23"
    p.buy("FOO", 100, 100.0, timestamp=T0)
    b = p._trades[-1]
    p.sell("FOO", 100, 100.0, timestamp=T0 + timedelta(days=2))
    s = p._trades[-1]
    rt_bps = (b["total"] - s["total"]) / 10000.0 * 10000.0
    assert rt_bps == pytest.approx(46.4, abs=0.05)      # 2 x 23.2
    assert b["price"] / 100.0 - 1 == pytest.approx(0.00229, abs=1e-5)
    assert 1 - s["price"] / 100.0 == pytest.approx(0.00229, abs=1e-5)


def test_OK_crypto_is_untouched_by_the_equity_cost_model():
    p = PortfolioEmulator(initial_cash=100000.0, taker_fee=0.0002)
    p.buy("BTC/USD", 1.0, 50000.0, timestamp=T0)
    t = p._trades[-1]
    assert "cost_model_version" not in t
    assert t["total"] == pytest.approx(50000.0)
    p.sell("BTC/USD", 0.5, 50000.0, timestamp=T0)
    assert p._withheld_cash() == 0.0


def test_OK_t1_settlement_releases_and_does_not_deadlock():
    p = PortfolioEmulator(initial_cash=10000.0)
    p.buy("FOO", 50, 100.0, timestamp=T0)
    p.sell("FOO", 50, 100.0, timestamp=T0 + timedelta(hours=1))
    proceeds = p._trades[-1]["total"]
    # exactly one 5% haircut, on the proceeds, never applied twice
    assert p._withheld_cash() == pytest.approx(proceeds * 0.05, rel=1e-9)
    p._advance_clock(T0 + timedelta(days=2))
    assert p._withheld_cash() == 0.0
    assert p.get_buying_power() == pytest.approx(p.get_cash())
