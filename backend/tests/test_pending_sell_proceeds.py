"""Credit the same-tick funding sell, or the buy it funded cannot pay (bt 725146).

Execution is next-event: an order submitted while a bar is processed fills at the
next quote. The index core submits its funding SPY sell on the SAME tick as the
buy it is funding, so the buy is sized against a balance that does not yet hold
the money raised for it.

Measured over bt 820236 / 613166 / 725146: 41 cash-bound buy events, $14,801.27 of
approved size refused, $14,084.02 of it (95.2%) a submitted-and-unfilled core
release. Canonical bar, bt 725146 2026-02-05:

    [core] funding $828 of conviction overflow (floor-bounded $902)
    [core] released 1.0249 SPY @ 686.10 (accepted=True, filled=False)   = $703.18
    Buy gate: cash=$124.64 floor=$120.00 available=$4.64 -> SKIP
    [execution] FILL SELL SPY qty=1.02494573 price=675.789  (NEXT bar)

need = 827.86 - 124.64 = 703.22, released 703.18. Sized to the cent; only the
clock was wrong. SNDK was then locked out for the rest of the run by
`V32 mw_buy extension-block: SNDK recent runup +108.0% > 25%`.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from portfolio_emulator import PortfolioEmulator  # noqa: E402


class _Order:
    def __init__(self, symbol, side, quantity):
        self.symbol, self.side, self.quantity = symbol, side, quantity
        self.order_id = f"{side}-{symbol}"


class _Sim:
    def __init__(self, orders=()):
        self._orders = tuple(orders)

    @property
    def pending_orders(self):
        return self._orders


def _emu(cash=124.64, pending=(), marks=None):
    e = PortfolioEmulator(initial_cash=cash)
    e._execution_simulator = _Sim(pending)
    e._last_prices = dict(marks or {"SPY": 686.10})
    return e


def test_the_bug_pending_release_is_invisible():
    e = _emu(pending=[_Order("SPY", "sell", 1.0249)])
    assert e.credit_pending_sell_proceeds is False, "must default OFF"
    assert e.get_buying_power() == pytest.approx(124.64)


def test_crediting_the_release_unblocks_the_buy():
    e = _emu(pending=[_Order("SPY", "sell", 1.0249)])
    e.credit_pending_sell_proceeds = True
    # 0.95 haircut, the same one the T+1 model already uses
    assert e.pending_sell_proceeds() == pytest.approx(703.18, abs=0.05)
    assert e.get_buying_power() == pytest.approx(124.64 + 0.95 * 703.18, abs=0.05)
    assert e.get_buying_power() > 700.0, "the $828 conviction buy can now be funded"


def test_haircut_means_it_never_exceeds_cash_plus_proceeds():
    e = _emu(pending=[_Order("SPY", "sell", 1.0249)])
    e.credit_pending_sell_proceeds = True
    assert e.get_buying_power() <= 124.64 + 703.18


def test_pending_BUYS_are_never_credited():
    """A pending buy consumes cash; it must not create it."""
    e = _emu(pending=[_Order("SPY", "buy", 1.0249)])
    e.credit_pending_sell_proceeds = True
    assert e.pending_sell_proceeds() == pytest.approx(0.0)
    assert e.get_buying_power() == pytest.approx(124.64)


def test_unmarked_symbol_contributes_nothing():
    """Value at the last traded price or not at all — never a hoped-for price."""
    e = _emu(pending=[_Order("XYZ", "sell", 10.0)], marks={"SPY": 686.10})
    e.credit_pending_sell_proceeds = True
    assert e.pending_sell_proceeds() == pytest.approx(0.0)


def test_reserved_still_subtracts():
    e = _emu(pending=[_Order("SPY", "sell", 1.0249)])
    e.credit_pending_sell_proceeds = True
    a = e.get_buying_power()
    b = e.get_buying_power(reserved=100.0)
    assert b == pytest.approx(a - 100.0, abs=0.01)


def test_never_negative():
    e = _emu(cash=10.0)
    e.credit_pending_sell_proceeds = True
    assert e.get_buying_power(reserved=1e9) == 0.0


def test_no_simulator_is_safe():
    e = PortfolioEmulator(initial_cash=100.0)
    e._execution_simulator = None
    e.credit_pending_sell_proceeds = True
    assert e.pending_sell_proceeds() == 0.0
    assert e.get_buying_power() == pytest.approx(100.0)


def test_explicit_prices_override_stale_marks():
    e = _emu(pending=[_Order("SPY", "sell", 2.0)], marks={"SPY": 100.0})
    e.credit_pending_sell_proceeds = True
    assert e.pending_sell_proceeds({"SPY": 200.0}) == pytest.approx(400.0)
