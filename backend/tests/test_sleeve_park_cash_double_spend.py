"""A sleeve park must not commit the same idle dollar twice.

THE DEFECT (bt 624674, W2 bear 2026-03-02..2026-03-30), reproduced twice with
exact arithmetic:

    L9271  parked $681.43 ... leg=3326/4573 cap, alloc=70%   accepted=True
    L9283  parked $681.43 ... leg=4006/4573 cap, alloc=70%   accepted=True
    L9294  parked $568.26 ... leg=4573/4573 cap, alloc=70%   accepted=True
    L9337  released 11.6577 SQQQ (bear-leg refill: cash 1.6% -> target 15% of NAV)

    L12567 parked $716.09 ... leg=3331/4578      L12578 READ 200%
    L12579 parked $716.09 ... leg=4045/4578      L12589 READ 211%
    L12590 parked $534.62 ... leg=4578/4578      L12611 READ 219%
    L12632 released 11.7695 SQQQ (bear-leg refill: cash 1.3% -> target 15%)

Read the two sizing terms separately. `room` behaved CORRECTLY both times — the
leg readout climbs 3,326 -> 4,006 -> 4,573 against a 4,573 cap, so the
2026-08-04 stacking fix (`cur_val += _sleeve_pending_qty(...)`) is working.
`idle` did not: the first two clips are byte-identical, which is the fingerprint
of `deploy = min(idle, room)` returning `idle` unchanged because the cash for
the accepted-but-unfilled park was never debited.

The cause is one line: `idle = cash - park_floor_pct * nav`, where `cash` is
`get_cash()` — `self._cash` raw (portfolio_emulator.py:1083). Only
`get_buying_power()` nets `_execution_cash_reservations`. The 2026-08-04 fix
netted pending out of the ALLOCATION term and left the CASH term exposed.

Cost in that window: $3,897.92 of the $15,830.16 turnover ledger (24.6%) and
$1,758.51 of forced same-cycle refill sells — churn generated purely by the
accounting error, charged to the very budget it then exhausts.

NOTE THE CAP DOES NOT CATCH IT. In the episodes above the total stayed under
the NAV cap; it is the CASH that is over-committed, which is why the existing
`test_residual_sleeve_same_bar_stacking.py` suite is green against this defect.
These tests bind on cash, not on the cap.

Default OFF (`sleeve_park_nets_pending_cash_enabled`): the fix can only reduce
a park, never create one, but it does change results, so it ships as its own
arm.
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

import test_residual_sleeve as h                      # noqa: E402
import test_residual_sleeve_same_bar_stacking as s    # noqa: E402

b = h.b
NAV = 6000.0
PX = 30.0
# park_floor_pct = max(0.02, 0.15 + 0.02) = 0.17 -> floor $1,020 on a $6,000 NAV.
# Cash $2,000 leaves idle $980, well under the $2,100 bear cap, so IDLE binds
# and ROOM does not. That is the regime the defect lives in.
CASH = 2000.0
# Written the way the engine writes it (max(buffer, release + buffer)) so
# this constant carries the same float as the code under test — 0.15 + 0.02
# is not 0.17 in binary, and a bare equality on the difference fails.
IDLE = CASH - (0.15 + 0.02) * NAV                     # $980
CAP_USD = 0.35 * NAV                                  # $2,100


def setup_function(_fn):
    h.setup_function(_fn)
    h._set_regime("bear")


def _spec(**over):
    cfg = dict(BEAR := h.BEAR_SPEC[0]["config"])
    cfg.update(over)
    return [{"strategy": "graph_nexus_analysis", "config": cfg}]


def _total(emu):
    return sum(float(sig.get("cash_per_trade") or 0.0) for sig in emu.signals)


def test_the_fixture_is_idle_bound_not_room_bound():
    """Anti-vacuity. If ROOM binds, these tests are about the 2026-08-04 fix
    and say nothing about the cash term."""
    assert IDLE < CAP_USD, (IDLE, CAP_USD)
    emu = s._EmuNextEvent(cash=CASH, nav=NAV)
    b._residual_sleeve_deploy(emu, {"SQQQ": PX}, datetime(2026, 3, 13, 1), _spec())
    assert len(emu.signals) == 1, "deploy path did not run — test would be vacuous"
    assert abs(_total(emu) - IDLE) < 1e-6, _total(emu)


def test_today_two_parks_commit_the_same_cash_twice():
    """The measured behaviour, flag OFF. Two byte-identical clips — the exact
    fingerprint of L9271/L9283 and L12567/L12579."""
    emu = s._EmuNextEvent(cash=CASH, nav=NAV)
    for hour in (1, 2):
        b._residual_sleeve_deploy(emu, {"SQQQ": PX},
                                  datetime(2026, 3, 13, hour), _spec())
    assert len(emu.signals) == 2, emu.signals
    sizes = [round(float(x["cash_per_trade"]), 2) for x in emu.signals]
    assert sizes[0] == sizes[1], sizes
    assert _total(emu) > IDLE * 1.9, (
        f"expected the documented double-spend; got ${_total(emu):,.2f} "
        f"against ${IDLE:,.2f} of idle cash")


def test_the_flag_stops_the_second_park():
    """With the fix, the cash committed to the resting order is netted out of
    `idle`, so the second evaluation has nothing left to spend."""
    emu = s._EmuNextEvent(cash=CASH, nav=NAV)
    spec = _spec(sleeve_park_nets_pending_cash_enabled=True)
    for hour in (1, 2, 3):
        b._residual_sleeve_deploy(emu, {"SQQQ": PX},
                                  datetime(2026, 3, 13, hour), spec)
    assert len(emu.signals) == 1, (
        f"the park still fired {len(emu.signals)} times: "
        f"{[x['cash_per_trade'] for x in emu.signals]}")
    assert _total(emu) <= IDLE + 1e-6, _total(emu)


def test_the_first_park_is_unchanged_by_the_flag():
    """A tightening must not shrink the clip that was always correct."""
    on = s._EmuNextEvent(cash=CASH, nav=NAV)
    off = s._EmuNextEvent(cash=CASH, nav=NAV)
    ts = datetime(2026, 3, 13, 1)
    b._residual_sleeve_deploy(on, {"SQQQ": PX}, ts,
                              _spec(sleeve_park_nets_pending_cash_enabled=True))
    b._residual_sleeve_deploy(off, {"SQQQ": PX}, ts, _spec())
    assert _total(on) == _total(off)
    assert abs(_total(on) - IDLE) < 1e-6


def test_a_filled_order_stops_reserving():
    """Self-clearing. Once the order leaves `pending_orders` the cash is really
    gone and the position is really held, so the next bar sizes off the book —
    a stale reservation must never permanently starve the hedge."""
    emu = s._EmuNextEvent(cash=CASH, nav=NAV)
    spec = _spec(sleeve_park_nets_pending_cash_enabled=True)
    b._residual_sleeve_deploy(emu, {"SQQQ": PX}, datetime(2026, 3, 13, 1), spec)
    assert len(emu.signals) == 1
    # settle it: the order fills, cash leaves, the position appears
    emu._execution_simulator.pending_orders.clear()
    emu._cash = CASH - IDLE
    emu._pos["SQQQ"] = IDLE / PX
    b._residual_sleeve_deploy(emu, {"SQQQ": PX}, datetime(2026, 3, 16, 1), spec)
    # nothing left above the 17% floor, so no second park — but the reason is
    # cash, not a phantom reservation
    assert _total(emu) <= IDLE + 1e-6, _total(emu)


# ── the BULL leg ───────────────────────────────────────────────────────────
#
# READ THIS BEFORE TRUSTING A RESULT FROM IT. On doc-193/194/195 this lane is
# UNREACHABLE: `core_sleeve_enabled` lives only in
# `regime_profiles.{bull,chop,recovery}`, so in exactly those regimes the
# `if _core is not None:` block in `_residual_sleeve_deploy` returns before
# control can arrive, and bear/crash go to the bear leg. It runs only on a
# document with NO core — the doc-179 shape, which is what these fixtures model
# (`h.SPEC` carries no `core_sleeve_enabled`). An A/B on a core-bearing document
# will show this lane unchanged, and that means "never ran".

BULL_PX = 600.0


def _bull_spec(**over):
    cfg = dict(h.SPEC[0]["config"])
    cfg.update(over)
    return [{"strategy": "graph_nexus_analysis", "config": cfg}]


def _bull_idle(cash):
    return cash - (0.15 + 0.02) * NAV


def test_bull_leg_today_commits_the_same_cash_twice():
    h._set_regime("bull")
    emu = s._EmuNextEvent(cash=CASH, nav=NAV)
    for hour in (1, 2):
        b._residual_sleeve_deploy(emu, {"SPY": BULL_PX},
                                  datetime(2026, 1, 16, hour), _bull_spec())
    assert [x["sym"] for x in emu.signals] == ["SPY", "SPY"], emu.signals
    assert _total(emu) > _bull_idle(CASH) * 1.9, _total(emu)


def test_bull_leg_flag_stops_the_second_park():
    h._set_regime("bull")
    emu = s._EmuNextEvent(cash=CASH, nav=NAV)
    spec = _bull_spec(sleeve_park_nets_pending_cash_enabled=True)
    for hour in (1, 2, 3):
        b._residual_sleeve_deploy(emu, {"SPY": BULL_PX},
                                  datetime(2026, 1, 16, hour), spec)
    assert len(emu.signals) == 1, [x["cash_per_trade"] for x in emu.signals]
    assert _total(emu) <= _bull_idle(CASH) + 1e-6


def test_bull_leg_nets_its_own_symbol_not_the_hedge():
    """`sym`/`px` must be the bull symbol here, not leftovers from the bear
    branch. A resting SQQQ order must not suppress the SPY park."""
    h._set_regime("bull")
    emu = s._EmuNextEvent(cash=CASH, nav=NAV)
    emu._execution_simulator.pending_orders.append(
        s._PendingOrder("SQQQ", "buy", 40.0))
    b._residual_sleeve_deploy(emu, {"SPY": BULL_PX}, datetime(2026, 1, 16, 1),
                              _bull_spec(sleeve_park_nets_pending_cash_enabled=True))
    assert len(emu.signals) == 1, emu.signals
    assert emu.signals[0]["sym"] == "SPY"
    assert abs(_total(emu) - _bull_idle(CASH)) < 1e-6


def test_bull_leg_does_no_lookup_on_a_bar_that_parks_nothing():
    """The min-deploy guard must run BEFORE the netting. In live the lookup is
    a non-indexed table scan, and running it above the guard put one on every
    bull/chop evaluation including the majority that park nothing."""
    h._set_regime("bull")
    calls = []
    real = b._ns["_sleeve_pending_qty"]

    def _spy(*a, **kw):
        calls.append(a[:2])
        return real(*a, **kw)

    b._ns["_sleeve_pending_qty"] = _spy
    try:
        # cash barely above the 17% floor -> idle under the $300 min-deploy
        emu = s._EmuNextEvent(cash=1100.0, nav=NAV)
        b._residual_sleeve_deploy(
            emu, {"SPY": BULL_PX}, datetime(2026, 1, 16, 1),
            _bull_spec(sleeve_park_nets_pending_cash_enabled=True))
        assert emu.signals == [], emu.signals
        assert calls == [], f"looked up pending orders on a no-park bar: {calls}"
    finally:
        b._ns["_sleeve_pending_qty"] = real


def test_flag_off_is_byte_identical():
    for cash in (1200.0, 2000.0, 3000.0, 6000.0):
        off = s._EmuNextEvent(cash=cash, nav=NAV)
        absent = s._EmuNextEvent(cash=cash, nav=NAV)
        ts = datetime(2026, 3, 13, 1)
        b._residual_sleeve_deploy(
            off, {"SQQQ": PX}, ts,
            _spec(sleeve_park_nets_pending_cash_enabled=False))
        b._residual_sleeve_deploy(absent, {"SQQQ": PX}, ts, _spec())
        assert [x["cash_per_trade"] for x in off.signals] == \
               [x["cash_per_trade"] for x in absent.signals], cash
