"""A held position must not vanish from the valuation.

`get_portfolio_value` used to value a held ticker at ZERO whenever it was
absent from that bar's `prices`, so a position silently dropped out of NAV and
reappeared later. On the portfolio curve that renders as a cliff-drop followed
by a plateau followed by a jump — indistinguishable from a real market move.

`_last_prices` was already maintained for this exact fallback and was simply
never consulted. This is NAV: it feeds position sizing, the single-position
cap, ramp room, portfolio_value_high/low, max_drawdown_magnitude and the
drawdown halt, so a spurious zero could halt trading or mis-size a buy.
"""
import os
import sys

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from portfolio_emulator import PortfolioEmulator  # noqa: E402


def _emu(cash=10000.0):
    return PortfolioEmulator(initial_cash=cash, taker_fee=0.0)


def test_a_missing_ticker_is_carried_at_its_last_price():
    emu = _emu()
    emu._positions = {"AAPL": 10.0}
    emu.save_portfolio_snapshot({"AAPL": 100.0})          # seeds _last_prices
    assert emu.get_portfolio_value({"AAPL": 100.0}) == 11000.0
    # AAPL absent from this bar — it must NOT drop to cash-only
    assert emu.get_portfolio_value({"MSFT": 50.0}) == 11000.0
    assert emu.get_portfolio_value({}) == 11000.0
    assert emu.get_portfolio_value(None) == 11000.0


def test_a_fresh_price_still_wins_over_the_stale_one():
    emu = _emu()
    emu._positions = {"AAPL": 10.0}
    emu.save_portfolio_snapshot({"AAPL": 100.0})
    assert emu.get_portfolio_value({"AAPL": 120.0}) == 11200.0


def test_a_never_seen_ticker_is_still_skipped():
    """There is nothing to carry forward for it."""
    emu = _emu()
    emu._positions = {"GHOST": 10.0}
    assert emu.get_portfolio_value({}) == 10000.0


def test_the_cliff_and_plateau_artefact_is_gone():
    """Reproduces the chart shape: value must not collapse and recover purely
    because a symbol came and went from the bar's price dict."""
    emu = _emu(6000.0)
    emu._cash = 1000.0
    emu._positions = {"AAOI": 100.0}
    emu.save_portfolio_snapshot({"AAOI": 50.0})           # 1000 + 5000 = 6000
    curve = [emu.get_portfolio_value(p) for p in (
        {"AAOI": 50.0},   # priced
        {},               # symbol missing from this bar
        {},               # still missing
        {"AAOI": 50.0},   # back
    )]
    assert curve == [6000.0] * 4, curve
    assert min(curve) > 1000.0, "must never collapse to cash-only"


def test_snapshots_record_the_carried_value():
    emu = _emu()
    emu._positions = {"AAPL": 10.0}
    emu.save_portfolio_snapshot({"AAPL": 100.0})
    emu.save_portfolio_snapshot({})                       # missing symbol
    values = [s["value"] for s in emu.get_portfolio_history()]
    assert values == [11000.0, 11000.0], values
