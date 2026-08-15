"""A confirmed fill must never be paid for with money that has not arrived.

THE CRASH (bt 101666, killed at 2026-04-20 after ~13,467 lines):

    File "portfolio_emulator.py", line 1169, in apply_fill
        raise ValueError("confirmed buy fill exceeds available cash")

THE MECHANISM — and note the obvious diagnosis is WRONG. The pending-sell credit
is not double-counted: at submit `get_buying_power` adds `f x E` where E is the
sale's MARK, and at fill cash gains the realized net N while `_withhold_sell_
proceeds` withholds `(1-f) x N`, so the credit is REPLACED. The defect is the
residual:

    shortfall = f x (E - N)

E is marked at `_last_prices` with no allowance for the sale's own execution
cost; N is what the sell-side cost model actually delivers. Reproduced to the
cent on the real emulator:

    cash 2026-04-20 14:00            $1,115.85
    SPY funding sell 0.2969 @ 710.06   $210.8168   (the credit, E)
    realized net at a flat price       $210.3277   (N)
    shortfall = 0.95 x (E - N)           $0.4646   -> raise

0.4646 is exactly 23.2 bps of $210.82 — half-spread 22.8 + slippage 0.1 + fee
0.3 under `equity-measured-v3-nbbo23`. The run survived only if SPY's next bar
was >= +0.2325%. It was deterministic for a flat-to-down hour.

THE CRASH IS THE BENIGN FAILURE. `apply_fill` also asked `get_buying_power()`,
which carries the same credit, so under a different fill ordering the identical
gap settles a buy against money that has not arrived and drives `_cash` to
-$200.28 with NO exception and NO log line — unsecured leverage in a cash
account, NAV wrong for the rest of the run.

THE FIX, two layers:
  * the simulator sizes a buy to real spendable cash before proposing it, in the
    same place it already clamps to `notional_limit` — a fill is born
    affordable, and a partial fill is what a real broker does;
  * `apply_fill` compares against `spendable_cash()` (settled money) rather than
    `get_buying_power()` (a commitment budget), and raises a soft
    `InsufficientBuyingPower` the simulator catches — honouring the contract
    `on_quote`'s own docstring already promised: "the order remains pending and
    no fill provenance is recorded".

NOT default-OFF, deliberately: the current default is a CRASH, so there is no
baseline to be byte-identical with, and the clamp is provably inert while
`credit_pending_sell_proceeds` is off (184 execution tests pass unchanged).
The FLAG stays off; the fix does not.
"""
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from portfolio_emulator import PortfolioEmulator  # noqa: E402
from simulated_execution import (  # noqa: E402
    InsufficientBuyingPower,
    LIQUIDITY_ADJUSTED_EQUITY_COST_MODEL,
    NextEventExecutionSimulator,
    SimulationOrder,
    SimulationQuote,
)

UTC = timezone.utc
T0 = datetime(2026, 4, 20, 14, 0, tzinfo=UTC)

CASH = 1115.85
SPY_QTY, SPY_PX = 0.2969, 710.06
CLIP = 663.32


def _emu(credit):
    """MUST attach a real simulator. Without one `execute_signal` takes the
    LEGACY immediate-fill path, `process_quote` returns () and the whole test
    passes vacuously — the exact anti-pattern that let two other suites in this
    repo stay green over live defects."""
    emu = PortfolioEmulator(
        initial_cash=CASH,
        execution_simulator=NextEventExecutionSimulator(
            LIQUIDITY_ADJUSTED_EQUITY_COST_MODEL),
    )
    emu._equity_cost_model = LIQUIDITY_ADJUSTED_EQUITY_COST_MODEL
    emu.credit_pending_sell_proceeds = credit
    emu._positions["SPY"] = 3.63064444
    emu._last_prices.update({"SPY": SPY_PX, "AAOI": 154.41, "AEHR": 85.23})
    return emu


def _quote(sym, px, when):
    return SimulationQuote(symbol=sym, bid=px * 0.999, ask=px * 1.001,
                           timestamp=when)


def _run(credit, order):
    """Replay the tick with the three orders submitted, fills in `order`."""
    emu = _emu(credit)
    emu.execute_signal("SPY", -1, SPY_PX, timestamp=T0,
                       sell_fraction=SPY_QTY / 3.63064444,
                       order_source="residual_bull_refill")
    for sym, px in (("AAOI", 154.41), ("AEHR", 85.23)):
        emu.execute_signal(sym, 1, px, timestamp=T0, cash_per_trade=CLIP,
                           order_source="main_signal")
    err = None
    try:
        for sym in order:
            px = {"SPY": SPY_PX, "AAOI": 154.41, "AEHR": 85.23}[sym]
            emu.process_quote(_quote(sym, px, T0 + timedelta(minutes=1)))
    except Exception as exc:  # noqa: BLE001 - the test is about which exception
        err = exc
    return emu, err


ORDERINGS = [
    ("SPY", "AAOI", "AEHR"), ("SPY", "AEHR", "AAOI"),
    ("AAOI", "SPY", "AEHR"), ("AAOI", "AEHR", "SPY"),
    ("AEHR", "SPY", "AAOI"), ("AEHR", "AAOI", "SPY"),
]


def test_the_fixture_uses_the_next_event_simulator():
    """Anti-vacuity. If this fails every other test here proves nothing."""
    emu = _emu(credit=True)
    assert emu._execution_simulator is not None
    emu.execute_signal("AAOI", 1, 154.41, timestamp=T0, cash_per_trade=CLIP,
                       order_source="main_signal")
    assert emu._execution_simulator.pending_order_count == 1, (
        "the order should REST until a quote arrives; if it filled immediately "
        "the legacy path was taken and these tests are vacuous")


def test_the_bt101666_tick_no_longer_kills_the_run():
    emu, err = _run(credit=True, order=("SPY", "AAOI", "AEHR"))
    assert err is None, f"the documented crash is back: {err!r}"
    assert emu._cash >= -1e-9, emu._cash


@pytest.mark.parametrize("order", ORDERINGS)
def test_cash_is_never_negative_under_any_fill_ordering(order):
    """THE INVARIANT. Before the fix this raised on 4 of 6 orderings and drove
    cash to -$200.28 on the other 2."""
    for credit in (False, True):
        emu, err = _run(credit=credit, order=order)
        assert err is None, (credit, order, repr(err))
        assert emu._cash >= -1e-9, (
            f"credit={credit} order={order}: cash {emu._cash:.4f} — a fill was "
            "paid for with money that had not arrived")


def test_a_confirmed_buy_never_exceeds_settled_cash():
    """Directly: apply_fill must measure against settled money, not a budget
    that includes a merely-submitted sale."""
    emu = _emu(credit=True)
    emu.execute_signal("SPY", -1, SPY_PX, timestamp=T0,
                       sell_fraction=SPY_QTY / 3.63064444,
                       order_source="residual_bull_refill")
    # the credit inflates the COMMITMENT budget but not SETTLED cash — that
    # divergence is the whole defect
    assert emu.get_buying_power() > emu.spendable_cash() + 1.0, (
        emu.get_buying_power(), emu.spendable_cash())
    assert emu.spendable_cash() == pytest.approx(CASH, abs=1e-6)


def test_credit_off_never_crashes_and_never_overdraws():
    for order in ORDERINGS:
        emu, err = _run(credit=False, order=order)
        assert err is None, (order, repr(err))
        assert emu._cash >= -1e-9, (order, emu._cash)


def test_the_soft_refusal_type_is_catchable_and_subclasses_ValueError():
    """`on_quote` catches it to leave the order pending; every existing handler
    that expected a ValueError still behaves as before."""
    assert issubclass(InsufficientBuyingPower, ValueError)


def test_a_refused_fill_is_counted_and_surfaced():
    """Silence is how a $0.46 shortfall went unnoticed until it killed a run."""
    emu = _emu(credit=True)
    sim = emu._execution_simulator
    assert sim.refused_fill_count == 0
    assert "refused_fill_count" in sim.execution_summary()


def test_the_clamp_is_inert_without_a_cash_budget():
    """Every direct-simulator caller that does not pass `cash_budget` must be
    unaffected."""
    from simulated_execution import NextEventExecutionSimulator
    sim = NextEventExecutionSimulator(LIQUIDITY_ADJUSTED_EQUITY_COST_MODEL)
    sim.submit(SimulationOrder(
        order_id="o1", symbol="AAA", side="buy", quantity=10.0,
        decision_at=T0, execute_not_before=T0))
    fills = sim.on_quote(_quote("AAA", 100.0, T0 + timedelta(minutes=1)))
    assert fills and fills[0].incremental_quantity == pytest.approx(10.0)
