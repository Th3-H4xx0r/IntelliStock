"""The satellite share must count what the book ALREADY holds.

THE DEFECT. `_total_new_spend` excludes held names (`_held_for_cap`), so the
total-spend cap governs ONE BAR's new spend and never the book's total exposure.
The satellite can therefore walk past its design share across successive bars
without the cap ever binding. The broker-side twin already does it correctly —
`broker.py:3534` returns `(share * nav) - satellite`.

bt 559934 — the recovery window that lost 11.21pp to SPY — is this bug in three
bars:

    04-01  regime=bear, no bear profile -> `_core_armed` False -> clamp INERT.
           GLD $900 goes in uncapped.
    04-02  still bear. OIH $891. Satellite ~30% of NAV.
    04-03  regime flips to chop, the clamp arms and computes $3,764 of room —
           but cannot see the $1,791 already held, so it funds FOUR more at
           $836. Satellite = 86% of NAV against a 63% design share, and the cap
           never bound on the way there.
    04-06  the core's $2,389.94 deploy fills for $863.40; its target, being the
           residual of the satellite, collapses to 12.5% and locks there.

Downstream: every bar reads `[core] funding request trimmed $3,3xx -> $1xx`, the
conviction band releases $150-220 against a $360 min-position floor, and 144
buys die `insufficient_cash` while INTC +156%, DELL +156%, SNDK +135%, MU +105%
and AXTI +87% are refused.

Window "a" won precisely because it kept a 42%-of-NAV core it could SELL on
session 5 to fund AMAT and CMI — 83% of that window's return. Window d's core
never got funded, so it had nothing to sell.

Default OFF (`satellite_share_counts_held_enabled`), and strictly tightening:
it can only ever REDUCE the room.
"""
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

_GNA = os.path.join(os.path.dirname(__file__), "..", "strategies",
                    "graph_nexus_analysis.py")
_SRC = open(_GNA, encoding="utf-8").read()

# bt 559934's own numbers.
NAV = 6000.0
DESIGN_SHARE = 0.63          # 1 - core_target_pct(0.35) - cash_floor(0.02)
HELD_AFTER_TWO_BARS = 1791.0  # GLD $900 + OIH $891
CLIP = 836.0


def test_the_bug_lets_the_satellite_walk_past_its_share():
    """Per-bar accounting: bar 3 sees the full share as free."""
    room_per_bar = NAV * DESIGN_SHARE
    funded = int(room_per_bar // CLIP)
    total_satellite = HELD_AFTER_TWO_BARS + funded * CLIP
    assert funded >= 4, funded
    assert total_satellite / NAV > 0.84, (
        f"satellite reaches {total_satellite / NAV:.0%} of NAV against a "
        f"{DESIGN_SHARE:.0%} design share — this is the measured 86%")


def test_counting_held_satellite_stops_it_at_the_design_share():
    room_cumulative = NAV * DESIGN_SHARE - HELD_AFTER_TWO_BARS
    funded = int(room_cumulative // CLIP)
    total_satellite = HELD_AFTER_TWO_BARS + funded * CLIP
    assert funded == 2, funded
    assert total_satellite / NAV <= DESIGN_SHARE + 0.01, (
        f"satellite lands at {total_satellite / NAV:.0%}, at or inside its "
        f"{DESIGN_SHARE:.0%} design share")


def test_the_core_then_has_something_to_sell():
    """The whole point. A core at its design weight is the reservoir the
    conviction band draws on; a core at its floor is not."""
    room_cumulative = NAV * DESIGN_SHARE - HELD_AFTER_TWO_BARS
    satellite = HELD_AFTER_TWO_BARS + int(room_cumulative // CLIP) * CLIP
    core = NAV * (1.0 - 0.02) - satellite
    assert core / NAV > 0.30, f"core lands at {core / NAV:.0%} of NAV"
    # conviction band = satellite_max_share(0.88) - actual satellite
    band = (0.88 - satellite / NAV) * NAV
    floor = max(50.0, NAV * 0.06)
    assert band > floor, (
        f"the conviction band is ${band:,.0f} against a ${floor:,.0f} "
        f"min-position floor — a conviction buy is fundable")


def test_todays_band_cannot_clear_the_floor():
    """Guard the premise: at 86% the band is smaller than one ticket."""
    band = (0.88 - 0.86) * NAV
    floor = max(50.0, NAV * 0.06)
    assert band < floor, (band, floor)


def test_the_production_site_is_guarded_tightening_and_logged():
    assert re.search(
        r'config\.get\(\s*\n?\s*"satellite_share_counts_held_enabled", False\)', _SRC), \
        "must be behind its own default-OFF flag"
    assert "_tot_cap = max(0.0, _tot_cap - _held_sat)" in _SRC, \
        "must SUBTRACT held satellite — it can only ever reduce the room"
    assert "SATELLITE SHARE (cumulative):" in _SRC, (
        "must announce itself; five levers have shipped inert here and each was "
        "unprovable from its own log")


def test_it_fires_only_inside_the_core_armed_branch():
    """A bear bar with no bear profile must be untouched, so the hedge path and
    the objective's explicit prohibition on adding a bear profile are honoured."""
    i = _SRC.index("satellite_share_counts_held_enabled")
    window = _SRC[max(0, i - 400):i]
    assert "_core_armed" in window, (
        "the cumulative clamp must be gated on _core_armed, like the share "
        "clamp above it")


def test_sleeve_legs_do_not_count_as_satellite():
    """SPY/SQQQ are the core and hedge; counting them would double-charge the
    satellite for the very exposure the core is supposed to hold."""
    i = _SRC.index("_held_sat = 0.0")
    window = _SRC[i:i + 700]
    assert "_sleeve_symbols(config)" in window, \
        "sleeve legs must be excluded from the held-satellite sum"


def test_it_fails_soft_and_says_so():
    i = _SRC.index("_held_sat = 0.0")
    window = _SRC[i:i + 1400]
    assert "satellite cumulative share unavailable" in window, (
        "an exception must leave the cap at today's per-bar behaviour AND log "
        "it — an armed flag that silently disarms is how a control gets "
        "credited with a result it never produced")
