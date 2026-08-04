"""The bear leg must not stack two deploys in ONE bar and blow its NAV cap.

FOUND IN A REAL RUN, not theorised. bt 471471 (doc-187, 2026-03-02..04-27) has
two SQQQ buys sharing quote_timestamp 2026-04-01T13:00:00:

    sim-...016-SQQQ   55.72 sh   cash 6397 -> 2002   spent $4,395
    sim-...017-SQQQ   21.15 sh   cash 2002 ->  334   spent $1,668

The first is correctly sized to `_alloc * nav` (0.70 * 6397 = $4,478). The second
fired in the same bar and re-granted almost the whole cap again, leaving the book
at **94.8% of NAV in SQQQ** — a -3x daily-rebalanced inverse ETF — against a
documented 70% ceiling.

STATUS — READ THIS BEFORE TRUSTING THESE TESTS
----------------------------------------------
The DEFECT IS CONFIRMED FROM RUN DATA (the two orders above, plus `cash + SQQQ
market value` reconciling exactly to portfolio value at 94.7%). **The MECHANISM
IS NOT CONFIRMED.** The obvious hypothesis was that `room = _alloc * nav -
cur_val` reads `cur_val` from `portfolio_emulator.get_positions()` while
execution is NEXT-EVENT (`NextEventExecutionSimulator`), so the first order has
not filled when the second sizes itself. These tests were written to reproduce
that and they PASS unmodified — i.e. the harness does NOT reproduce it, so that
hypothesis is UNPROVEN and something else (a guard that did not engage, a
different call path, or order splitting below) is in play.

So what follows is an INVARIANT PIN, not a regression test for a known cause:
it asserts the leg never exceeds `bear_alloc_max_pct * NAV`. Do not read a green
run here as "the bt 471471 defect is fixed" — it is not fixed, and it is not yet
explained. See also the SECOND pathology in the same run, unaddressed here:
fourteen `residual_bear_refill` SELLS at 2026-04-06T13:00, each ~10% smaller than
the last, unwinding the whole leg in one bar and paying spread on all fourteen.
"""
import ast
import os
import sys
from datetime import datetime

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

# Reuse the AST-extraction harness pattern from test_residual_sleeve.py —
# broker.py argparse-SystemExits under pytest.
import test_residual_sleeve as h  # noqa: E402

b = h.b

#: doc-179's real bear-leg settings.
SPEC = [{"strategy": "graph_nexus_analysis", "config": {
    "residual_sleeve_enabled": True,
    "residual_sleeve_symbol": "SPY",
    "residual_sleeve_bear_symbol": "SQQQ",
    "residual_sleeve_buffer_pct": 0.02,
    "residual_sleeve_min_deploy_pct": 0.05,
    "residual_sleeve_release_cash_pct": 0.15,
    "residual_sleeve_bear_alloc_pct": 0.35,
    "residual_sleeve_bear_alloc_max_pct": 0.70,
    "residual_sleeve_bear_alloc_scale_enabled": True,
}}]

ALLOC_MAX = 0.70
NAV = 6397.0
PRICES = {"SPY": 600.0, "SQQQ": 78.88}


class _Emu(h._Emu):
    """Emulator whose position does NOT update on submit — models next-event
    execution, where the fill lands on a later price event."""

    def get_positions(self):
        return {"SQQQ": self._sleeve_qty} if self._sleeve_qty > 0 else {}


def setup_function(_fn):
    h.setup_function(_fn)
    h._set_regime("crash")      # crash => alloc == alloc_max, the ceiling case
    b._RESIDUAL_SLEEVE_STATE["bear_pending"] = None


def _deployed(emu):
    return sum(float(s.get("cash_per_trade") or 0.0) for s in emu.signals)


def test_single_deploy_respects_the_nav_cap():
    """Baseline: one call in a bar is already correct. Pins the ceiling."""
    emu = _Emu(cash=NAV, nav=NAV)
    b._residual_sleeve_deploy(emu, PRICES, datetime(2026, 4, 1, 13), SPEC)
    assert _deployed(emu) <= ALLOC_MAX * NAV + 1e-6, (
        f"single deploy already exceeds the cap: {_deployed(emu):.0f} "
        f"> {ALLOC_MAX * NAV:.0f}")


def test_two_deploys_in_the_same_bar_must_not_exceed_the_nav_cap():
    """THE BUG. Two calls at the SAME timestamp with no intervening fill.

    Reproduces bt 471471: without same-bar accounting the leg reaches ~95% of
    NAV in a -3x inverse ETF.
    """
    emu = _Emu(cash=NAV, nav=NAV)
    ts = datetime(2026, 4, 1, 13)
    b._residual_sleeve_deploy(emu, PRICES, ts, SPEC)
    b._residual_sleeve_deploy(emu, PRICES, ts, SPEC)   # same bar, no fill yet
    total = _deployed(emu)
    assert total <= ALLOC_MAX * NAV + 1e-6, (
        f"bear leg stacked to ${total:,.0f} = {total / NAV * 100:.1f}% of NAV, "
        f"above the {ALLOC_MAX * 100:.0f}% cap (${ALLOC_MAX * NAV:,.0f}). "
        "This is the bt 471471 defect: a -3x inverse ETF at ~95% of the book.")


def test_a_new_bar_starts_a_fresh_allowance():
    """The guard must be scoped to the BAR. On a later timestamp the real
    position has settled, so sizing must go back to reading it — otherwise a
    stale reservation would permanently starve the hedge."""
    emu = _Emu(cash=NAV, nav=NAV)
    b._residual_sleeve_deploy(emu, PRICES, datetime(2026, 4, 1, 13), SPEC)
    first = _deployed(emu)
    # next bar, the earlier clip has filled
    emu._sleeve_qty = first / PRICES["SQQQ"]
    emu._cash = NAV - first
    b._residual_sleeve_deploy(emu, PRICES, datetime(2026, 4, 1, 14), SPEC)
    total_position = emu._sleeve_qty * PRICES["SQQQ"] + (_deployed(emu) - first)
    assert total_position <= ALLOC_MAX * NAV + 1e-6
