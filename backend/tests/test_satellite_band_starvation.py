"""A sleeve whose per-name target is <= the no-churn band can never open.

`core_band_pct` (0.05) is a fraction of NAV, and a 20% satellite spread over 4
names targets exactly 0.05 of NAV each. Opening one from zero is a delta of
exactly the band, so `abs(delta)/nav <= band` is true on the FIRST bar and every
bar after: the position is never established, and the sleeve's whole budget sits
in cash forever.

Measured before the fix: with satellite_pct=0.20 and 4 names, a $10,000 book
deployed 60% core + 20% commodity and left 19% ($1,906) idle, and two arms of a
tiebreak study returned BYTE-IDENTICAL returns despite holding different names —
because neither arm held anything at all.

`targets_to_orders` already treats exits as unconditional for the same reason
("the band is meaningless around a target of zero"). This pins the symmetric
case: an OPEN is not churn either.
"""
import os
import sys

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from strategy_x import targets_to_orders  # noqa: E402

CFG = {"core_band_pct": 0.05, "min_order_usd": 25.0, "cost_haircut_pct": 0.006}


def test_a_satellite_name_at_exactly_the_band_is_still_opened():
    """THE bug: target 0.05 of NAV, band 0.05 of NAV, nothing held."""
    d, sizes = targets_to_orders(
        {"AAPL": 0.05}, nav=10_000.0, positions={}, prices={"AAPL": 200.0},
        cash=10_000.0, config=CFG, owned={"AAPL"})
    assert d.get("AAPL") == 1, "a 5% satellite target was never bought"
    assert sizes["AAPL"]["buy_cash"] > 400


def test_the_whole_four_name_sleeve_opens_not_just_one():
    targets = {"AAPL": 0.05, "MSFT": 0.05, "NVDA": 0.05, "AMZN": 0.05,
               "TQQQ": 0.60, "GLD": 0.10, "GDX": 0.10}
    prices = {s: 100.0 for s in targets}
    d, sizes = targets_to_orders(targets, nav=10_000.0, positions={},
                                 prices=prices, cash=10_000.0, config=CFG,
                                 owned=set(targets))
    bought = {s for s, v in d.items() if v == 1}
    assert {"AAPL", "MSFT", "NVDA", "AMZN"} <= bought, (
        f"satellite names missing from the order set: {sorted(bought)}")
    # And the book should be nearly fully deployed, not 20% in cash.
    spent = sum(v["buy_cash"] for v in sizes.values())
    assert spent > 9_000, f"only ${spent:.0f} of $10,000 deployed"


def test_the_band_still_suppresses_churn_on_a_position_already_held():
    """The band must keep doing its job when a position EXISTS — this is the
    behaviour the fix must not break."""
    # Holding $510 against a $500 target: a $10 drift, well inside the band.
    d, _ = targets_to_orders(
        {"AAPL": 0.05}, nav=10_000.0, positions={"AAPL": 5.1},
        prices={"AAPL": 100.0}, cash=5_000.0, config=CFG, owned={"AAPL"})
    assert "AAPL" not in d, "the band no longer suppresses a small drift"


def test_a_tiny_target_below_min_order_is_still_refused():
    """Opening unconditionally must not mean opening dust."""
    d, _ = targets_to_orders(
        {"AAPL": 0.001}, nav=10_000.0, positions={}, prices={"AAPL": 100.0},
        cash=10_000.0, config=CFG, owned={"AAPL"})
    assert "AAPL" not in d, "a $10 order cleared the $25 minimum"
