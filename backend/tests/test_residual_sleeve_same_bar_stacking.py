"""The bear leg must not stack two deploys in ONE bar and blow its NAV cap.

FOUND IN REAL RUNS, in BOTH arms, at the same timestamp:

    bt 471471 (doc-187)  2026-04-01T13:00  residual_bear_deploy  $4,395 + $1,669
    bt 884112 (doc-185)  2026-04-01T13:00  residual_bear_deploy  $4,389 +   $567

The first clip is correctly sized to `_alloc * nav` (0.70 * 6397 = $4,478). A
SECOND deploy fires in the same bar and re-grants most of the cap again, ending
at 94.8% (doc-187) / 78.1% (doc-185) of NAV in SQQQ — a -3x daily-rebalanced
inverse ETF — against a documented 70% ceiling. `cash + market value` reconciles
exactly to portfolio value at those bars, so the weights are real.

Because it reproduces in the CONTROL, this is base-sleeve behaviour and is
therefore live on doc-179/alpaca-main today.

THE FILENAME IS A MISNOMER, KEPT FOR HISTORY. It is NOT a same-bar defect.

The 13:00 stamp on those trades is the FILL time — the next available quote,
i.e. the market open. The decisions happened on separate OVERNIGHT bars. A
diagnostic added to the deployed engine settled it:

    current_time=2026-04-02 14:00  pending=(2026-04-01 01:00, 4387.66)

Execution is NEXT-EVENT, so a clip decided at 01:00 rests unfilled until the
open. Every sleeve decision in between re-read `get_positions()` / `get_cash()`,
saw no trace of it, and sized as though nothing were outstanding. Two orders
decided at 01:00 and 02:00 both printed at 13:00 for 95% of NAV.

An earlier fix keyed the guard on `current_time` ("same bar") and could never
work, because the calls are on DIFFERENT bars. The fix reads unfilled orders
from the simulator instead (`_sleeve_pending_qty`), which is stateless and
self-clearing: an order leaves `pending_orders` when it fills or expires, so a
rejected order cannot permanently reserve room and starve the hedge.

THREE EARLIER VERSIONS OF THIS FILE WERE FALSELY GREEN. Each is now guarded:
  1. wrong fixtures (SPY `_Emu`/spec) -> ZERO signals, `total <= cap` asserted
     against 0. Every test now asserts `emu.signals` is non-empty FIRST.
  2. wrong entry point — the cash REFILL lives in `_residual_sleeve_release`.
  3. stub returned a bare `True`, which has no `filled` attribute and so reads
     as CONFIRMED; the engine's receipt is accepted-but-not-filled, making
     anything gated on `_signal_result_is_confirmed` dead code in backtest.
     `_Submission` now models it and a guard test pins the contract.
The lesson: assert the path RAN, and model what the engine actually returns.
"""
import os
import sys
from datetime import datetime

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)
_tests = os.path.dirname(os.path.abspath(__file__))
if _tests not in sys.path:
    sys.path.insert(0, _tests)

import test_residual_sleeve as h  # noqa: E402  (reuses its AST-extraction harness)

b = h.b
NAV = 6000.0
PX = 30.0
ALLOC = 0.35                      # BEAR_SPEC's residual_sleeve_bear_alloc_pct
CAP_USD = ALLOC * NAV             # $2,100


class _Submission:
    """What the BACKTEST path actually returns — `simulated_execution.
    SimulationSubmission`: accepted, but NOT filled, because execution is
    next-event.

        accepted: bool = True
        filled:   bool = False
        __bool__ -> accepted

    This matters enormously. `_signal_result_is_confirmed` is
    `bool(result) and bool(getattr(result, "filled", True))`, so anything hung
    off it is DEAD CODE in backtest. A stub returning a bare `True` has no
    `filled` attribute, defaults it to True, and silently exercises a branch the
    engine never takes — which is exactly how an inert fix passed its tests and
    shipped (bt 811098 reproduced the defect unchanged).
    """
    accepted = True
    filled = False

    def __bool__(self):
        return self.accepted


class _PendingOrder:
    def __init__(self, symbol, side, quantity):
        self.symbol, self.side, self.quantity = symbol, side, quantity


class _Sim:
    """Minimal stand-in for NextEventExecutionSimulator's pending-order view.

    Orders REST here and are never settled, which is exactly what happens
    overnight: a clip decided at 01:00 does not fill until the 13:00 open, so
    every bar in between still sees it pending.
    """
    def __init__(self):
        self.pending_orders = []


class _EmuNextEvent(h._Emu2):
    """_Emu2 with a realistic next-event receipt AND a resting-order book."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._execution_simulator = _Sim()

    def execute_signal(self, sym, sig, px, timestamp=None, sell_fraction=None,
                       cash_per_trade=None, order_source=None):
        super().execute_signal(sym, sig, px, timestamp=timestamp,
                               sell_fraction=sell_fraction,
                               cash_per_trade=cash_per_trade)
        if sig == 1:
            qty = float(cash_per_trade or 0.0) / float(px or 1.0)
            side = "buy"
        else:
            held = float(self._pos.get(sym, 0.0) or 0.0)
            qty = held * float(sell_fraction or 1.0)
            side = "sell"
        if qty > 0:
            self._execution_simulator.pending_orders.append(
                _PendingOrder(sym, side, qty))
        return _Submission()


def test_the_stub_models_the_engines_confirmation_semantics():
    """Guard the guard: if this ever reports 'confirmed', these tests have
    stopped exercising the backtest path and their greens mean nothing."""
    assert bool(_Submission()) is True, "an accepted submission is truthy"
    assert b._ns["_signal_result_is_confirmed"](_Submission()) is False, (
        "a next-event submission must NOT read as confirmed — anything gated on "
        "_signal_result_is_confirmed is dead code in backtest")


def setup_function(_fn):
    h.setup_function(_fn)
    h._set_regime("bear")


def _total(emu):
    return sum(float(s.get("cash_per_trade") or 0.0) for s in emu.signals)


def test_single_deploy_is_capped_and_actually_fires():
    """Baseline + anti-vacuity guard: one call must EMIT and respect the cap."""
    emu = _EmuNextEvent(cash=NAV, nav=NAV)
    b._residual_sleeve_deploy(emu, {"SQQQ": PX}, datetime(2026, 4, 1, 13), h.BEAR_SPEC)
    assert len(emu.signals) == 1, "deploy path did not run — test would be vacuous"
    assert abs(_total(emu) - CAP_USD) < 1e-6, _total(emu)


def test_two_deploys_in_the_same_bar_must_not_exceed_the_nav_cap():
    """THE DEFECT. Two calls at the SAME timestamp, no intervening fill.

    The emulator stub does not settle fills, which is exactly the next-event
    behaviour the engine has. Reproduces bt 471471 / bt 884112.
    """
    emu = _EmuNextEvent(cash=NAV, nav=NAV)
    ts = datetime(2026, 4, 1, 13)
    b._residual_sleeve_deploy(emu, {"SQQQ": PX}, ts, h.BEAR_SPEC)
    b._residual_sleeve_deploy(emu, {"SQQQ": PX}, ts, h.BEAR_SPEC)
    assert emu.signals, "deploy path did not run — test would be vacuous"
    total = _total(emu)
    assert total <= CAP_USD + 1e-6, (
        f"bear leg stacked to ${total:,.0f} = {total / NAV * 100:.1f}% of NAV "
        f"across {len(emu.signals)} same-bar orders, above the "
        f"{ALLOC * 100:.0f}% cap (${CAP_USD:,.0f}). This is the bt 471471 / "
        f"bt 884112 defect: a -3x inverse ETF at up to 95% of the book.")


def test_without_same_bar_accounting_the_deploy_does_blow_the_cap():
    """Proves the DEPLOY fix is load-bearing. Clearing `bear_pending_deploy`
    between calls reproduces PRE-FIX behaviour — which is exactly what shipped
    when the accounting was gated on `_signal_result_is_confirmed` and therefore
    never ran in backtest (bt 811098)."""
    emu = _EmuNextEvent(cash=NAV, nav=NAV)
    ts = datetime(2026, 4, 1, 13)
    for _ in range(2):
        emu._execution_simulator.pending_orders.clear()    # pre-fix: in-flight invisible
        b._residual_sleeve_deploy(emu, {"SQQQ": PX}, ts, h.BEAR_SPEC)
    total = _total(emu)
    assert len(emu.signals) == 2, "expected a second same-bar deploy without the fix"
    assert total > CAP_USD * 1.5, (
        f"expected the cap to be blown without same-bar accounting; got "
        f"${total:,.0f} vs cap ${CAP_USD:,.0f}")


def test_refill_raises_the_gap_once_and_does_not_liquidate_the_hedge():
    """The 14-slice unwind: bt 471471 sold ~$4,558 of a ~$4,700 leg to raise a
    ~$594 gap, because `_bcash` never saw the in-flight proceeds and
    `execute_signal` re-applied the same fraction to a shrinking base."""
    nav, px, qty = 6000.0, 30.0, 200.0          # $6,000 leg
    cash = 100.0                                 # far below the 15% target ($900)
    emu = _EmuNextEvent(cash=cash, nav=nav, positions={"SQQQ": qty})
    ts = datetime(2026, 4, 6, 13)
    for _ in range(14):                          # the engine called it 14x that bar
        b._residual_sleeve_release(emu, {"SQQQ": px}, ts, h.BEAR_SPEC)

    sells = [s for s in emu.signals if s.get("sig") == -1]
    assert sells, "refill path did not run — test would be vacuous"
    sold = sum(float(s.get("sell_fraction") or 0.0) for s in sells) * qty * px
    gap = 0.15 * nav - cash                      # ~$800
    assert sold <= gap * 1.5, (
        f"refill sold ${sold:,.0f} across {len(sells)} same-bar orders to raise "
        f"a ${gap:,.0f} gap — the bt 471471 hedge liquidation")
    assert sold >= gap * 0.5, f"refill raised too little: ${sold:,.0f} vs ${gap:,.0f}"


def test_without_same_bar_accounting_the_refill_does_liquidate_the_hedge():
    """Proves the fix is what prevents it, not the harness.

    Clearing `bear_pending_refill` between calls reproduces PRE-FIX behaviour
    (no memory of proceeds already raised this bar). The leg should then be
    sold far past the demand — the bt 471471 shape.
    """
    nav, px, qty = 6000.0, 30.0, 200.0
    emu = _EmuNextEvent(cash=100.0, nav=nav, positions={"SQQQ": qty})
    ts = datetime(2026, 4, 6, 13)
    for _ in range(14):
        emu._execution_simulator.pending_orders.clear()   # pre-fix: in-flight invisible
        b._residual_sleeve_release(emu, {"SQQQ": px}, ts, h.BEAR_SPEC)

    sells = [s for s in emu.signals if s.get("sig") == -1]
    sold = sum(float(s.get("sell_fraction") or 0.0) for s in sells) * qty * px
    gap = 0.15 * nav - 100.0
    assert len(sells) > 1, "expected repeated same-bar refills without the fix"
    assert sold > gap * 2, (
        f"expected runaway selling without same-bar accounting; got ${sold:,.0f} "
        f"for a ${gap:,.0f} gap across {len(sells)} orders")


def test_a_later_bar_sizes_against_the_settled_position():
    """Scoping check: once the fill has settled, room is computed off the real
    position, so the leg converges to the cap rather than being starved."""
    emu = _EmuNextEvent(cash=NAV, nav=NAV)
    b._residual_sleeve_deploy(emu, {"SQQQ": PX}, datetime(2026, 4, 1, 13), h.BEAR_SPEC)
    first = _total(emu)
    assert first > 0
    emu._pos = {"SQQQ": first / PX}          # settled
    emu._cash = NAV - first
    b._residual_sleeve_deploy(emu, {"SQQQ": PX}, datetime(2026, 4, 1, 14), h.BEAR_SPEC)
    held = emu._pos["SQQQ"] * PX + (_total(emu) - first)
    assert held <= CAP_USD + 1e-6, held
