"""Unadjusted-split reconciliation (2026-08-01).

Backtests read raw bars and the feed does not split-adjust. VGT split 8:1
mid-window: close $808.88 -> $101.57 in one bar (ratio 7.96) while the share
count stayed put, so NAV fell 13.6% instantly and the position booked a phantom
-$765 against a true +$168 — turning a +13.54% run into -0.92%, twice, before
it was caught. The corruption looks exactly like a market crash on the curve.
"""
import os
import sys

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from portfolio_emulator import PortfolioEmulator  # noqa: E402


def _held(ticker="VGT", shares=1.1119, price=808.88):
    pe = PortfolioEmulator(initial_cash=10000.0)
    pe.buy(ticker, shares, price)
    pe.save_portfolio_snapshot({ticker: price})
    return pe


def test_vgt_eight_for_one_is_reconciled():
    """The real case: 808.88 -> 101.57 is 7.96x, an 8:1 split."""
    pe = _held()
    before = pe.get_portfolio_value({"VGT": 808.88})
    pe.save_portfolio_snapshot({"VGT": 101.57})
    after = pe.get_portfolio_value({"VGT": 101.57})
    assert abs(after - before) / before < 0.01, (
        f"NAV moved {100*(after/before-1):+.1f}% across a pure split")


def test_reverse_split_is_reconciled():
    pe = _held("ABC", shares=100.0, price=2.00)
    before = pe.get_portfolio_value({"ABC": 2.00})
    pe.save_portfolio_snapshot({"ABC": 20.00})       # 1-for-10 reverse
    after = pe.get_portfolio_value({"ABC": 20.00})
    assert abs(after - before) / before < 0.01
    assert abs(pe.get_positions()["ABC"] - 10.0) < 1e-6


def test_ordinary_crash_is_left_alone():
    """A -45% gap is not near any split ratio and must NOT be reconciled."""
    pe = _held("XYZ", shares=10.0, price=100.0)
    pe.save_portfolio_snapshot({"XYZ": 55.0})
    assert abs(pe.get_positions()["XYZ"] - 10.0) < 1e-6, "real loss was erased"


def test_halved_price_that_is_not_a_split_still_reconciles_only_near_ratio():
    """Exactly 2.00x IS a plausible 2:1 split and is reconciled; 2.20x is not."""
    pe = _held("AAA", shares=10.0, price=100.0)
    pe.save_portfolio_snapshot({"AAA": 50.0})
    assert abs(pe.get_positions()["AAA"] - 20.0) < 1e-6
    pe2 = _held("BBB", shares=10.0, price=110.0)
    pe2.save_portfolio_snapshot({"BBB": 50.0})       # 2.20x — not a split
    assert abs(pe2.get_positions()["BBB"] - 10.0) < 1e-6


def test_untouched_when_disabled():
    pe = _held()
    pe._split_reconcile_enabled = False
    pe.save_portfolio_snapshot({"VGT": 101.57})
    assert abs(pe.get_positions()["VGT"] - 1.1119) < 1e-6


def test_no_positions_is_a_noop():
    pe = PortfolioEmulator(initial_cash=1000.0)
    assert pe.reconcile_splits({"ANY": 5.0}) == []


def test_missing_or_zero_price_is_ignored():
    pe = _held()
    pe.save_portfolio_snapshot({})                    # absent
    assert abs(pe.get_positions()["VGT"] - 1.1119) < 1e-6
    pe.save_portfolio_snapshot({"VGT": 0.0})          # zero
    assert abs(pe.get_positions()["VGT"] - 1.1119) < 1e-6


# --- the half-fix that mattered: cost basis, not just share count ---
#
# Restating shares fixes NAV but decisions read the ENTRY price out of trade
# history: unrealized_pct = (current - entry)/entry. An unrestated $808 entry
# against a $101 post-split price still reads -87.4%, so the circuit breaker
# still liquidates a position that did nothing. That is exactly how the
# original bug converted a phantom mark into a realised -$765.

def test_entry_price_is_restated_so_pnl_is_not_minus_87pct():
    pe = _held("VGT", shares=1.3192, price=681.69)
    pe.save_portfolio_snapshot({"VGT": 808.88})
    pe.save_portfolio_snapshot({"VGT": 101.57})       # 8-for-1
    entry = [t for t in pe.get_trade_history() if t["ticker"] == "VGT"][0]
    unrealized_pct = (101.57 - entry["price"]) / entry["price"] * 100.0
    # True economics: bought at $681.69 pre-split = $85.21 post-split, now
    # $101.57 -> a genuine +19.2% GAIN. Unrestated it reads -85%, which is what
    # made the circuit breaker liquidate it.
    assert unrealized_pct > 0, (
        f"entry ${entry['price']:.2f} -> unrealized {unrealized_pct:+.1f}%; "
        "a circuit breaker would fire on a winning position")
    assert abs(unrealized_pct - 19.2) < 1.0


def test_trade_shares_are_restated_too():
    pe = _held("VGT", shares=1.3192, price=681.69)
    pe.save_portfolio_snapshot({"VGT": 808.88})
    pe.save_portfolio_snapshot({"VGT": 101.57})
    entry = [t for t in pe.get_trade_history() if t["ticker"] == "VGT"][0]
    assert abs(entry["shares"] - 1.3192 * 8) < 1e-6
    # notional is preserved: shares x price is unchanged by the split
    assert abs(entry["shares"] * entry["price"] - 1.3192 * 681.69) < 0.01


def test_crypto_is_never_split_reconciled():
    """A ~50% single-bar drawdown in an altcoin is real, not a 2-for-1."""
    pe = _held("BTC/USD", shares=0.05, price=100000.0)
    pe._is_crypto = True
    before = pe.get_positions()["BTC/USD"]          # fees make the fill < 0.05
    pe.save_portfolio_snapshot({"BTC/USD": 50000.0})
    assert abs(pe.get_positions()["BTC/USD"] - before) < 1e-12
