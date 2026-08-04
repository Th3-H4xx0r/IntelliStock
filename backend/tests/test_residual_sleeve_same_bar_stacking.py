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

WHY A SECOND CALL HAPPENS: execution is NEXT-EVENT
(`NextEventExecutionSimulator`), so the first order has not filled when the
second deploy sizes itself and `room = _alloc*nav - cur_val` still sees ~0 held.
The dual-cadence MONITOR + FULL cycles both call `_residual_sleeve_deploy` on a
bar, so two calls per timestamp is ordinary.

NOTE ON A PREVIOUS VERSION OF THIS FILE: it used the SPY-oriented `_Emu`/spec
and emitted ZERO signals, so its `total <= cap` assertion passed against 0 —
a vacuous green. Every test here now asserts a signal actually fired first.
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


def setup_function(_fn):
    h.setup_function(_fn)
    h._set_regime("bear")


def _total(emu):
    return sum(float(s.get("cash_per_trade") or 0.0) for s in emu.signals)


def test_single_deploy_is_capped_and_actually_fires():
    """Baseline + anti-vacuity guard: one call must EMIT and respect the cap."""
    emu = h._Emu2(cash=NAV, nav=NAV)
    b._residual_sleeve_deploy(emu, {"SQQQ": PX}, datetime(2026, 4, 1, 13), h.BEAR_SPEC)
    assert len(emu.signals) == 1, "deploy path did not run — test would be vacuous"
    assert abs(_total(emu) - CAP_USD) < 1e-6, _total(emu)


def test_two_deploys_in_the_same_bar_must_not_exceed_the_nav_cap():
    """THE DEFECT. Two calls at the SAME timestamp, no intervening fill.

    The emulator stub does not settle fills, which is exactly the next-event
    behaviour the engine has. Reproduces bt 471471 / bt 884112.
    """
    emu = h._Emu2(cash=NAV, nav=NAV)
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


def test_refill_raises_the_gap_once_and_does_not_liquidate_the_hedge():
    """The 14-slice unwind: bt 471471 sold ~$4,558 of a ~$4,700 leg to raise a
    ~$594 gap, because `_bcash` never saw the in-flight proceeds and
    `execute_signal` re-applied the same fraction to a shrinking base."""
    nav, px, qty = 6000.0, 30.0, 200.0          # $6,000 leg
    cash = 100.0                                 # far below the 15% target ($900)
    emu = h._Emu2(cash=cash, nav=nav, positions={"SQQQ": qty})
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
    emu = h._Emu2(cash=100.0, nav=nav, positions={"SQQQ": qty})
    ts = datetime(2026, 4, 6, 13)
    for _ in range(14):
        b._RESIDUAL_SLEEVE_STATE["bear_pending_refill"] = None   # pre-fix state
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
    emu = h._Emu2(cash=NAV, nav=NAV)
    b._residual_sleeve_deploy(emu, {"SQQQ": PX}, datetime(2026, 4, 1, 13), h.BEAR_SPEC)
    first = _total(emu)
    assert first > 0
    emu._pos = {"SQQQ": first / PX}          # settled
    emu._cash = NAV - first
    b._residual_sleeve_deploy(emu, {"SQQQ": PX}, datetime(2026, 4, 1, 14), h.BEAR_SPEC)
    held = emu._pos["SQQQ"] * PX + (_total(emu) - first)
    assert held <= CAP_USD + 1e-6, held
