"""The passive core must leave the alpha book enough cash to open one position.

THE DEFECT, measured on bt 523085 (W0 2026-01-01..2026-03-01, the reference
control) and reconciled to the cent:

    L13193 2026-01-17T01:00  [core] deploy of $1165.72 SPY was NOT confirmed (band_deploy)
    L13194                   order sim-000000000013-SPY accepted=True filled=False
    L13631 2026-01-19T15:00  Buy gate inputs for AMZN: cash=$1299.21 reserved=$0.00 -> PASS
    L13632                   SKIP BUY AMZN - fundable $133.49 ... < min $379
    L13642                   SKIP BUY SKYT - fundable $133.49 ... < min $379
    L13649                   SKIP BUY SNDK - fundable $133.49 ... < min $379
    L14816 2026-01-20T16:00  FILL BUY SPY qty=1.68169158 price=685.375940

$1,299.21 - $1,165.72 = $133.49. The core deployed after the close on a Friday,
its order pended through the whole of the next session, and its cash
reservation refused every conviction buy on the one tick that mattered. Over
the run, all five core deploys blocked at least one alpha buy that had already
passed the gate: $5,774.16 of core notional against $5,756.28 deployed, i.e.
100.3% of the core's gross buying held an alpha buy hostage at some point.

WHY THE CLIP AND NOT A VETO. `buy = min(drift_usd, _spendable)` is CASH-bound,
not drift-bound, on four of the five deploys — the core sat at 11.6-12.1% of NAV
against a 27-30% target all run, so the drift always exceeded the cash and the
clip collapsed to "every dollar there is, less the 2% floor and the 60bp cost
haircut". Verified against the log: at L13194, (1299.21 - 0.02*6329)/1.006 =
$1,165.64 against a logged $1,165.72. A veto-shaped gate ("hold while alpha has
queued buys") therefore switches the core OFF ENTIRELY rather than deferring it.
Shrinking the clip keeps the core deploying and still leaves the book able to
open a position.

Default 0.0 == OFF == byte-identical.
"""
import os
import sys

import pytest

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from core_sleeve import (  # noqa: E402
    CORE_DEPLOY_COST_HAIRCUT,
    MIN_CORE_DEPLOY_USD,
    MIN_CORE_ORDER_USD,
    core_rebalance_order,
    core_sleeve_config,
)

MIN_DEPLOY = max(MIN_CORE_DEPLOY_USD, MIN_CORE_ORDER_USD)

# The 2026-01-16 book, from the log's own figures.
NAV = 6329.0
CASH = 1299.21
CORE_VALUE = 0.116 * NAV          # the logged "core 11.6% of NAV"
SATELLITE = NAV - CASH - CORE_VALUE
ALPHA_FLOOR = 379.0               # min $379 on the SKIP BUY lines, = 6% of NAV


def _cfg(**over):
    # The doc-193-family values the three audited runs actually ran with, not
    # the module defaults (0.60/0.30/0.98) — with the defaults the core target
    # is 98% and this bar is not the bar the log recorded.
    base = {
        "core_sleeve_enabled": True,
        "cash_reserve_floor_pct": 0.02,
        "core_target_pct": 0.35,
        "core_min_pct": 0.10,
        "core_max_pct": 0.40,
    }
    base.update(over)
    return core_sleeve_config(base)


def _deploy(**over):
    return core_rebalance_order(
        _cfg(**over),
        nav=NAV,
        core_value=CORE_VALUE,
        satellite_value=SATELLITE,
        cash=CASH,
        regime="bull",
        days_since_rebalance=99,      # cadence satisfied
    )


def _spendable():
    return (CASH - 0.02 * NAV) / (1.0 + CORE_DEPLOY_COST_HAIRCUT)


def test_the_fixture_reproduces_the_measured_deploy():
    """Guard the arithmetic: if this drifts, every assertion below describes a
    different bar than the one the log recorded."""
    assert _spendable() == pytest.approx(1165.64, abs=0.5)


def test_today_the_core_takes_every_dollar():
    """Flag absent: the measured behaviour. If this ever changes, the default
    is no longer byte-identical and every prior run is incomparable."""
    order = _deploy()
    assert order.reason == "band_deploy", order.reason
    assert order.notional == pytest.approx(1165.64, abs=0.5)


def test_headroom_leaves_the_alpha_book_a_fundable_position():
    """Flag ON at 7% of NAV: the core still deploys, and what it leaves behind
    clears the alpha min-position floor."""
    order = _deploy(core_deploy_alpha_headroom_pct=0.07)
    assert order.reason == "band_deploy", order.reason
    assert order.notional == pytest.approx(722.6, abs=1.0)
    left_for_alpha = CASH - order.notional
    assert left_for_alpha > ALPHA_FLOOR, (
        f"the core left ${left_for_alpha:,.2f}, under the ${ALPHA_FLOOR:,.0f} "
        "floor — the whole point of the reservation is to clear it")


def test_the_core_still_deploys_this_is_not_a_veto():
    """The failure mode this fix exists to avoid. A gate that holds while the
    alpha book has queued buys would fire on almost every bar of bt 523085 —
    the run logged a funding request on 41 of 41 bars — and the core would
    deploy zero times."""
    for pct in (0.02, 0.05, 0.07, 0.10, 0.15):
        order = _deploy(core_deploy_alpha_headroom_pct=pct)
        assert order.notional > 0.0, (
            f"headroom {pct:.0%} switched the core off entirely: {order.reason}")


def test_an_absurd_headroom_cannot_starve_the_core():
    """The guard, stated as the invariant it is really for: a mis-set flag must
    never pin the core below its band with no way back in.

    This asserted `notional >= 0.5 * _spendable` — "keep half" — until an
    adversarial review measured that against all 133 real `[core] bought $X`
    lines in the repo's logs: on 23 of them (17.3%) halving the deploy STILL
    left the alpha book under its min-position floor, so those bars paid the
    full cost of the reservation and bought nothing with it. "Keep half" was a
    proxy for the invariant; "keep the core's own minimum" IS the invariant, and
    it leaves the alpha book above its floor on 133 of 133."""
    order = _deploy(core_deploy_alpha_headroom_pct=0.9)
    assert order.reason == "band_deploy", order.reason
    assert order.notional >= MIN_DEPLOY - 1e-9, order.notional


def test_the_headroom_always_clears_the_alpha_floor_when_it_can():
    """The point of the lever. Wherever the cash exists to leave the alpha book
    its floor AND the core its minimum, both must hold — this is the band the
    old 50% guard got wrong."""
    for cash in (700.0, 800.0, 1000.0, 1299.21, 2000.0, 4000.0):
        order = core_rebalance_order(
            _cfg(core_deploy_alpha_headroom_pct=0.07),
            nav=NAV, core_value=CORE_VALUE, satellite_value=SATELLITE,
            cash=cash, regime="bull", days_since_rebalance=99)
        if order.notional <= 0:
            continue
        assert order.notional >= MIN_DEPLOY - 1e-9, (cash, order.notional)
        assert cash - order.notional >= ALPHA_FLOOR, (
            f"cash ${cash:,.2f}: core took ${order.notional:,.2f} and left "
            f"${cash - order.notional:,.2f}, under the ${ALPHA_FLOOR:,.0f} floor "
            "— the bar paid for the reservation and bought nothing with it")


def test_the_shrink_is_reported_so_the_lever_is_provable_from_its_log():
    """The lever's common path is a SHRUNK deploy, which is otherwise
    byte-indistinguishable from a smaller deploy with the lever off. Five levers
    have shipped inert in this project and each was unprovable from its own log,
    so the withheld amount rides on the order."""
    off = _deploy()
    on = _deploy(core_deploy_alpha_headroom_pct=0.07)
    assert off.withheld == 0.0
    assert on.withheld > 0.0
    assert on.notional + on.withheld == pytest.approx(off.notional, abs=0.5), (
        "withheld must account for exactly the difference the lever made")


def test_a_drift_bound_deploy_is_trimmed_when_the_slack_is_thin():
    """The counter-case to the test below. A drift-bound clip IS reduced once
    the headroom exceeds the slack between the drift and the cash — the
    docstring that claimed otherwise was describing one fixture, not the rule."""
    # core 30% against a 40% ceiling with no satellite: drift $632.90 against
    # $669.40 of spendable, so the clip is drift-bound with only $36.50 of
    # slack — far less than the $443.03 headroom.
    kw = dict(nav=NAV, core_value=0.30 * NAV, satellite_value=0.0,
              cash=800.0, regime="bull", days_since_rebalance=99)
    off = core_rebalance_order(_cfg(), **kw)
    on = core_rebalance_order(_cfg(core_deploy_alpha_headroom_pct=0.07), **kw)
    assert off.notional < (800.0 - 0.02 * NAV), "premise: this clip is drift-bound"
    assert on.notional < off.notional, (on.notional, off.notional)


def test_a_drift_bound_deploy_below_the_headroom_is_untouched():
    """The opening deploy (L1918, $2,400 = 40% of NAV) was drift-bound, not
    cash-bound: it asked for far less than the cash available. Where that
    slack EXCEEDS the headroom the clip is untouched. Where it does not, the
    clip is reduced — see the counter-case above; the original docstring here
    claimed a blanket exemption that the code never had."""
    cfg = _cfg(core_deploy_alpha_headroom_pct=0.07)
    order = core_rebalance_order(
        cfg, nav=6000.0, core_value=0.0, satellite_value=0.0,
        cash=6000.0, regime="bull", days_since_rebalance=99)
    base = core_rebalance_order(
        _cfg(), nav=6000.0, core_value=0.0, satellite_value=0.0,
        cash=6000.0, regime="bull", days_since_rebalance=99)
    assert order.notional == pytest.approx(base.notional, abs=1e-9), (
        "a deploy that was already smaller than the cash it could see must not "
        "be trimmed by a cash reservation")


def test_the_headroom_can_no_longer_be_what_refuses_a_deploy():
    """There is deliberately NO `deploy_alpha_headroom` refusal reason.

    An earlier draft had one, for the case where the reservation pushed a clip
    under the core's minimum. Clamping the headroom at `_spendable - _min_deploy`
    makes that case unreachable — and an unreachable diagnostic is worse than
    none, because a reader greps for it, finds nothing, and concludes the lever
    never fired. A too-small clip must report exactly what it reports with the
    lever off."""
    kw = dict(nav=NAV, core_value=CORE_VALUE, satellite_value=SATELLITE,
              cash=207.0, regime="bull", days_since_rebalance=99)
    on = core_rebalance_order(_cfg(core_deploy_alpha_headroom_pct=0.07), **kw)
    off = core_rebalance_order(_cfg(), **kw)
    assert on.reason == off.reason, (on.reason, off.reason)
    assert "alpha_headroom" not in on.reason


def test_deploy_below_min_still_reported_when_headroom_is_off():
    """The same too-small clip with the flag OFF keeps its existing reason."""
    order = core_rebalance_order(
        _cfg(), nav=NAV, core_value=CORE_VALUE, satellite_value=SATELLITE,
        cash=130.0, regime="bull", days_since_rebalance=99)
    assert order.reason == "deploy_below_min", order.reason


def test_flag_off_is_byte_identical_across_the_book():
    """No book may change the OFF-path decision."""
    for nav, cash, core_w in ((6000.0, 6000.0, 0.0), (6329.0, 1299.21, 0.116),
                              (10000.0, 200.0, 0.60), (6250.0, 1097.41, 0.12)):
        core_value = core_w * nav
        sat = max(0.0, nav - cash - core_value)
        off = core_rebalance_order(
            _cfg(core_deploy_alpha_headroom_pct=0.0), nav=nav,
            core_value=core_value, satellite_value=sat, cash=cash,
            regime="bull", days_since_rebalance=99)
        absent = core_rebalance_order(
            _cfg(), nav=nav, core_value=core_value, satellite_value=sat,
            cash=cash, regime="bull", days_since_rebalance=99)
        assert (off.reason, round(off.notional, 9)) == \
               (absent.reason, round(absent.notional, 9)), (nav, cash, core_w)


def test_headroom_never_affects_the_sell_side():
    """The reservation is about cash the core would SPEND. A de-risk or a band
    release must be untouched — a bear leg that cannot sell is a different and
    much worse defect."""
    kw = dict(nav=NAV, core_value=0.9 * NAV, satellite_value=0.0, cash=100.0,
              regime="bull", days_since_rebalance=99)
    on = core_rebalance_order(_cfg(core_deploy_alpha_headroom_pct=0.15), **kw)
    off = core_rebalance_order(_cfg(), **kw)
    assert on.notional < 0.0, on
    assert (on.reason, round(on.notional, 9)) == \
           (off.reason, round(off.notional, 9))


def test_negative_and_garbage_headroom_fall_back_to_off():
    for bad in (-0.5, "", None, "abc"):
        order = _deploy(core_deploy_alpha_headroom_pct=bad)
        assert order.notional == pytest.approx(1165.64, abs=0.5), bad


# ── interaction with the funding-release credit ────────────────────────────
#
# `core_funding_release_reserve_decisions = 4` is ON in the target document, and
# no test set both keys until now. The headroom shrinks `buy` BEFORE the reserve
# block runs, so when the outstanding credit is smaller than the headroom the
# reserve no longer changes the outcome and therefore never consumes a decision
# — the credit stops expiring. That is benign (the headroom already limits the
# clip, so the core is not starved) but it IS a changed ladder, and the module's
# own note sizes that budget carefully. Pin it so it cannot drift silently.

def _both(credit, headroom, cash=CASH):
    return core_rebalance_order(
        _cfg(core_funding_release_reserve_decisions=4,
             core_deploy_alpha_headroom_pct=headroom),
        nav=NAV, core_value=CORE_VALUE, satellite_value=SATELLITE,
        cash=cash, regime="bull", days_since_rebalance=99,
        funding_request=credit)


def test_both_levers_on_still_deploys_every_bar():
    import core_sleeve
    core_sleeve.reset_funding_release_reserve()
    for _ in range(8):
        order = _both(credit=0.0, headroom=0.07)
        assert order.reason == "band_deploy", order.reason
        assert order.notional == pytest.approx(722.6, abs=1.0)


def test_the_credit_stops_expiring_when_the_headroom_already_binds():
    """Documented, not desired. If this ever needs to change, change it
    deliberately — do not let a future edit alter the ladder by accident."""
    import core_sleeve
    core_sleeve.reset_funding_release_reserve()
    # arm a credit smaller than the headroom via a funding release
    core_rebalance_order(
        _cfg(core_funding_release_reserve_decisions=4,
             core_deploy_alpha_headroom_pct=0.07),
        nav=NAV, core_value=CORE_VALUE, satellite_value=SATELLITE,
        cash=10.0, regime="bull", days_since_rebalance=99,
        funding_request=310.0)
    armed = core_sleeve.funding_release_reserve_state()
    assert armed["usd"] > 0.0 and armed["decisions"] == 4, armed
    for _ in range(8):
        _both(credit=0.0, headroom=0.07)
    after = core_sleeve.funding_release_reserve_state()
    assert after["decisions"] == 4, (
        f"the ladder changed: {armed} -> {after}. The headroom shrinks `buy` "
        "before the reserve block, so the reserve stops changing the outcome "
        "and stops consuming decisions.")


def test_without_the_headroom_the_credit_still_expires_as_designed():
    """Guard the premise of the test above: the credit's own ladder is intact
    when the headroom is off, so the interaction is what changed it."""
    import core_sleeve
    core_sleeve.reset_funding_release_reserve()
    core_rebalance_order(
        _cfg(core_funding_release_reserve_decisions=4),
        nav=NAV, core_value=CORE_VALUE, satellite_value=SATELLITE,
        cash=10.0, regime="bull", days_since_rebalance=99,
        funding_request=310.0)
    assert core_sleeve.funding_release_reserve_state()["decisions"] == 4
    for _ in range(8):
        _both(credit=0.0, headroom=0.0)
    assert core_sleeve.funding_release_reserve_state()["decisions"] == 0, (
        "the credit must still expire when the headroom is off")
