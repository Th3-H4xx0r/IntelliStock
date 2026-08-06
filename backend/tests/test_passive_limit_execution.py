"""Passive (limit) execution in the next-event simulator.

Every fill in this codebase crosses the spread BY CONSTRUCTION: a market buy
lifts the ask, a market sell hits the bid. At the measured notional-weighted
spread of 45.6 bps that is 22.8 bps paid on every side of every trade, and
measured slippage vs mid is ~0.1 bps — so the cost is the crossing itself, not
execution quality. A resting order is the only way to stop paying it.

The danger is obvious: an optimistic passive model is indistinguishable from
free money, and this repo has already shipped one fantasy execution model
(sub-$1 fills, zero spread) that made a strategy look profitable when it was
not. These tests pin the PESSIMISM:

  * a buy fills only if the ASK falls to the limit (a seller crossed to us) —
    never merely because the mid or bid drifted our way
  * the fill price is the LIMIT, never better
  * unfilled orders EXPIRE and are counted, so the non-fill cost that replaces
    the spread is always visible
  * omitting limit_price leaves today's marketable behaviour byte-identical
"""
import os
import sys
from datetime import datetime, timedelta, timezone

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from simulated_execution import (  # noqa: E402
    LIQUIDITY_ADJUSTED_EQUITY_COST_MODEL,
    NextEventExecutionSimulator,
    SimulationOrder,
    SimulationQuote,
)

T0 = datetime(2026, 8, 3, 15, 0, tzinfo=timezone.utc)


def _sim():
    return NextEventExecutionSimulator(
        cost_model=LIQUIDITY_ADJUSTED_EQUITY_COST_MODEL)


def _order(side, qty=10.0, limit=None, expire=0, oid="o1"):
    return SimulationOrder(
        order_id=oid, symbol="SPY", side=side, quantity=qty,
        decision_at=T0, execute_not_before=T0,
        limit_price=limit, expire_after_quotes=expire)


def _quote(bid, ask, secs=60):
    return SimulationQuote(symbol="SPY", bid=bid, ask=ask,
                           timestamp=T0 + timedelta(seconds=secs))


# ── the marketable path must not change ───────────────────────────────────

def test_no_limit_price_is_todays_marketable_behaviour():
    sim = _sim()
    sim.submit(_order("buy"))
    fills = sim.on_quote(_quote(99.0, 101.0))
    assert len(fills) == 1
    f = fills[0]
    # crosses: pays at/above the ask, and books a real spread cost
    assert f.price >= 101.0
    assert f.spread_cost > 0


# ── passive fills only when the market comes to us ────────────────────────

def test_a_buy_limit_below_the_ask_does_NOT_fill():
    sim = _sim()
    sim.submit(_order("buy", limit=100.0))
    assert sim.on_quote(_quote(99.0, 101.0)) == ()      # ask 101 > limit 100
    assert sim.pending_order_count == 1


def test_a_buy_limit_fills_when_the_ASK_reaches_it():
    sim = _sim()
    sim.submit(_order("buy", limit=100.0))
    fills = sim.on_quote(_quote(99.5, 100.0))           # ask touched the limit
    assert len(fills) == 1
    assert fills[0].price == 100.0


def test_a_drifting_mid_alone_never_fills_a_buy():
    """The mid can sit below our limit while the ask stays above it. Filling on
    the mid would manufacture fills that never happened."""
    sim = _sim()
    sim.submit(_order("buy", limit=100.0))
    # mid = 99.75, comfortably below the limit — but the ASK is 100.5
    assert sim.on_quote(_quote(99.0, 100.5)) == ()


def test_a_sell_limit_requires_the_BID_to_reach_it():
    sim = _sim()
    sim.submit(_order("sell", limit=100.0))
    assert sim.on_quote(_quote(99.5, 100.5)) == ()      # bid 99.5 < limit
    fills = sim.on_quote(_quote(100.0, 100.5), )        # bid reached it
    assert len(fills) == 1 and fills[0].price == 100.0


def test_the_fill_price_is_the_limit_never_better():
    """Real price improvement exists; assuming it is how a backtest lies."""
    sim = _sim()
    sim.submit(_order("buy", limit=100.0))
    fills = sim.on_quote(_quote(97.0, 98.0))            # ask far below limit
    assert fills[0].price == 100.0, "must not credit improvement to ourselves"


# ── the saving is real, and so is the risk that replaces it ───────────────

def test_a_passive_fill_pays_no_spread_and_no_slippage():
    sim = _sim()
    sim.submit(_order("buy", limit=100.0))
    f = sim.on_quote(_quote(99.5, 100.0))[0]
    assert f.spread_cost == 0.0
    assert f.slippage_cost == 0.0
    assert f.fees > 0, "regulatory pass-throughs still apply"


def test_unfilled_orders_expire_and_are_counted():
    """Non-fill is the cost that replaces the spread. It must be visible."""
    sim = _sim()
    sim.submit(_order("buy", limit=100.0, expire=3))
    for i in range(3):
        assert sim.on_quote(_quote(101.0, 102.0, secs=60 * (i + 1))) == ()
    assert sim.pending_order_count == 0
    assert sim.expired_order_count == 1


def test_expiry_zero_rests_forever():
    sim = _sim()
    sim.submit(_order("buy", limit=100.0, expire=0))
    for i in range(5):
        sim.on_quote(_quote(101.0, 102.0, secs=60 * (i + 1)))
    assert sim.pending_order_count == 1
    assert sim.expired_order_count == 0


def test_a_rested_order_still_fills_if_the_market_arrives_before_expiry():
    sim = _sim()
    sim.submit(_order("buy", limit=100.0, expire=5))
    assert sim.on_quote(_quote(101.0, 102.0, secs=60)) == ()
    fills = sim.on_quote(_quote(99.0, 99.5, secs=120))
    assert len(fills) == 1 and fills[0].price == 100.0
    assert sim.expired_order_count == 0


# ── validation ────────────────────────────────────────────────────────────

def test_bad_limit_and_expiry_values_are_rejected():
    for kwargs in ({"limit": 0.0}, {"limit": -5.0}, {"limit": float("nan")},
                   {"expire": -1}):
        try:
            _order("buy", **kwargs)
        except ValueError:
            continue
        raise AssertionError(f"should have rejected {kwargs}")


def test_the_measured_saving_is_the_half_spread():
    """Quantifies the lever: same quote, marketable vs passive at the mid."""
    mid, half = 100.0, 100.0 * 45.6 / 20_000.0     # 22.8 bps
    bid, ask = mid - half, mid + half

    taker = _sim(); taker.submit(_order("buy", oid="taker"))
    tf = taker.on_quote(_quote(bid, ask))[0]

    maker = _sim(); maker.submit(_order("buy", limit=mid, oid="maker"))
    mf = maker.on_quote(_quote(bid, mid))[0]        # ask came down to the mid

    saved_bps = (tf.price - mf.price) / mid * 10_000.0
    assert 20.0 < saved_bps < 26.0, saved_bps
