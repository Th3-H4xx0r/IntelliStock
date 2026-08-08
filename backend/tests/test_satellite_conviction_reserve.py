"""Keep room for the winner that has not shown up yet (bt 613166).

The book commits its whole risk budget on day one to whatever is available then.
bt 613166 opened NVDA/HESM/NTR at 14% of NAV each; when SNDK arrived on 02-05 at
raw=+1.705 the allocator sized it correctly at $872 and the sleeve had nothing
left:

    SATELLITE OVERFLOW: SNDK raw=+1.705 >= 1.50 —
                        funding $168 of room out of the core (floor-bounded)
    Buy gate: cash=$150.12 -> FILL $87.45 = 1.5% of NAV

The conviction band is only (max_share - design_share) wide, so once plain buys
have taken the design share there is no room left for the name the band exists
to fund.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core_sleeve import satellite_design_share, satellite_max_share  # noqa: E402


# core 35% target / 25% floor / 2% cash — the live doc-193 shape
BASE = {
    "core_sleeve_enabled": True,
    "core_target_pct": 0.35,
    "core_min_pct": 0.25,
    "cash_reserve_floor_pct": 0.02,
}
NAV = 6000.0


def test_default_off_is_byte_identical():
    assert satellite_design_share(BASE) == pytest.approx(0.63)
    assert satellite_max_share(BASE) == pytest.approx(0.73)


def test_the_bug_the_conviction_band_is_too_narrow_to_fund_a_position():
    """10 points of band = $600, against a 14%-of-NAV ($840) target entry."""
    band = (satellite_max_share(BASE) - satellite_design_share(BASE)) * NAV
    assert band == pytest.approx(600.0)
    assert band < 0.14 * NAV, "cannot fund a full-size conviction entry"


def test_reserve_widens_the_band_enough_for_a_full_entry():
    cfg = dict(BASE, satellite_conviction_reserve_pct=0.15)
    assert satellite_design_share(cfg) == pytest.approx(0.48)
    band = (satellite_max_share(cfg) - satellite_design_share(cfg)) * NAV
    assert band == pytest.approx(1500.0)
    assert band >= 0.14 * NAV


def test_the_core_target_and_floor_are_untouched():
    """A reserve must not become a de-risk: the core still aims at 35% and is
    still bounded at 25%, so the withheld slice stays available to equities."""
    cfg = dict(BASE, satellite_conviction_reserve_pct=0.15)
    assert satellite_max_share(cfg) == pytest.approx(0.73)  # = 1 - 0.25 - 0.02


def test_reserve_never_drives_the_design_share_negative():
    cfg = dict(BASE, satellite_conviction_reserve_pct=0.95)
    assert satellite_design_share(cfg) == pytest.approx(0.05)
    assert satellite_max_share(cfg) >= satellite_design_share(cfg)


def test_max_share_is_never_below_design_share():
    for reserve in (0.0, 0.05, 0.15, 0.40, 0.95):
        cfg = dict(BASE, satellite_conviction_reserve_pct=reserve)
        assert satellite_max_share(cfg) >= satellite_design_share(cfg)


def test_malformed_reserve_is_ignored():
    for bad in ("x", None, -1.0):
        cfg = dict(BASE, satellite_conviction_reserve_pct=bad)
        assert satellite_design_share(cfg) == pytest.approx(0.63)


def test_the_bt613166_bar_would_have_had_room():
    """SNDK needed $872; the reserve leaves $900 of band at a 60% satellite."""
    cfg = dict(BASE, satellite_conviction_reserve_pct=0.15)
    satellite_now = 0.58 * NAV          # what plain buys had taken
    room = satellite_max_share(cfg) * NAV - satellite_now
    assert room >= 872.0
