"""A demand-sized SELL expressed as a FRACTION compounds if it is re-issued.

PROVEN MECHANISM behind the 14-slice hedge unwind in bt 471471 / bt 884112
(2026-04-06T13:00, `residual_bear_refill`), where ~78% of the SQQQ hedge was
liquidated to raise cash the book had already raised on the first clip:

    598, 537, 482, 433, 389, 349, 313, 281, 252, 227, 203, 183, 164, 147
    ratio ~0.898 each step; first slice = 10.2% of the $5,851 leg

`PortfolioEmulator.execute_signal` takes NO absolute share count on the sell
side. Its whole sell rule is:

    total_shares = positions[ticker] - reserved_shares
    shares = total_shares * frac        (frac < 1.0)

So a caller that computes a precise quantity, converts it to
`frac = qty / qty_read_earlier`, and then gets invoked again in the same bar
sells that SAME fraction of a now-smaller position. The intended one-shot
"sell $594 of SQQQ" becomes a geometric series that walks the position toward
zero. `reserved_shares` makes it worse, not better: it shrinks the base for each
in-flight order, which is exactly what produces the smooth decay.

This is not hypothetical — it is the observed live-config behaviour: doc-185
(what alpaca-main runs today) shows the identical 14-order cluster.
"""
import math
import os
import sys

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)


class _Emu:
    """The exact sell arithmetic from PortfolioEmulator.execute_signal."""

    def __init__(self, qty):
        self.positions = {"SQQQ": float(qty)}
        self.sold = []

    def sell(self, ticker, sell_fraction):
        total = float(self.positions.get(ticker, 0.0) or 0.0)
        if total <= 0:
            return False
        frac = max(0.0, min(1.0, float(sell_fraction)))
        shares = total * frac if frac < 1.0 else total
        if shares <= 0:
            return False
        self.positions[ticker] = total - shares
        self.sold.append(shares)
        return True


PRICE = 76.11
QTY = 76.87                      # the leg as held at 2026-04-06
LEG_USD = QTY * PRICE            # ~$5,851
NEEDED_USD = 598.0               # the observed first clip == the cash gap
#: Alpaca bounces any equity order under $1 of notional, and the emulator
#: mirrors that. A residual below this is not a trade.
MIN_ORDER_USD = 1.0


def test_one_fractional_sell_raises_the_intended_amount():
    """Baseline: issued ONCE, the fraction is correct."""
    emu = _Emu(QTY)
    frac = (NEEDED_USD / PRICE) / QTY          # ~10.2%
    emu.sell("SQQQ", frac)
    raised = emu.sold[0] * PRICE
    assert math.isclose(raised, NEEDED_USD, rel_tol=0.01), raised


def test_reissuing_the_same_fraction_compounds_into_a_liquidation():
    """THE BUG. Re-issued within the bar, the same fraction sells ~78% of the leg.

    Reproduces the observed sizes and the ~0.898 decay ratio.
    """
    emu = _Emu(QTY)
    frac = (NEEDED_USD / PRICE) / QTY
    for _ in range(14):
        emu.sell("SQQQ", frac)

    sizes = [round(s * PRICE) for s in emu.sold]
    total = sum(s * PRICE for s in emu.sold)

    # the decay ratio the run exhibited
    ratios = [emu.sold[i + 1] / emu.sold[i] for i in range(len(emu.sold) - 1)]
    assert all(math.isclose(r, 1.0 - frac, rel_tol=1e-6) for r in ratios), ratios
    assert math.isclose(ratios[0], 0.898, abs_tol=0.01), ratios[0]

    # first clip matches the observed $598, and the run's shape is reproduced
    assert math.isclose(sizes[0], 598, abs_tol=3), sizes[0]

    # and the damage: ~78% of the hedge gone to raise ~$594
    assert total > 0.75 * LEG_USD, total
    assert total > 7 * NEEDED_USD, (
        f"sold ${total:,.0f} to raise ${NEEDED_USD:,.0f} — "
        f"{total / NEEDED_USD:.1f}x the requirement")


def test_the_safe_shape_is_recomputing_demand_against_the_live_position():
    """What the sleeve SHOULD do: re-derive the fraction from the CURRENT
    position each time, so once the demand is met further calls sell nothing."""
    emu = _Emu(QTY)
    raised = 0.0
    for _ in range(14):
        remaining_need = max(0.0, NEEDED_USD - raised)
        if remaining_need < MIN_ORDER_USD:   # below the broker floor = no trade
            break
        held = emu.positions["SQQQ"]
        frac = min(1.0, (remaining_need / PRICE) / held)
        emu.sell("SQQQ", frac)
        raised += emu.sold[-1] * PRICE
    assert math.isclose(raised, NEEDED_USD, rel_tol=0.01), raised
    assert len(emu.sold) == 1, "one clip is enough when demand is re-derived"
    assert emu.positions["SQQQ"] > 0.85 * QTY, "the hedge survives"
