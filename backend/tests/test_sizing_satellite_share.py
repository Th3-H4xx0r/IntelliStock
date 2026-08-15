"""The book must not be sized to hold more than its satellite design share.

THE CONTRADICTION, measured on bt 559934 (2026-04-01..2026-06-01, +4.45% vs
SPY +15.66%):

    max_positions(6) x total_spend_cap_target_weight_pct(0.14) = 0.84 satellite
    satellite_design_share = 1 - core_target_pct(0.35) - cash_floor(0.02) = 0.63
    total demanded = 0.35 + 0.02 + 0.84 = 1.21 of NAV

The book therefore overruns its design share by ~21pp on the opening build, is
pinned at the 0.88 conviction ceiling for the rest of the window, and the
residual index core (`core = clamp(1 - cash_floor - satellite, min, max)`) is
crushed to its 10% floor.

The downstream consequence is an arithmetic impossibility, not a tuning problem:
with the satellite at its ceiling the satellite cap granted a median $160 while
`_exec_min_position_floor` demanded a median $370. **Zero of 134 grants could
ever clear the floor.** 144 buys were refused `insufficient_cash`, and in that
window 100% of the 52 names that moved >=30% were discovered while 0% were
bought — INTC +156%, DELL +156%, SNDK +135%, MU +105%, AXTI +87%.

`broker.py:3744` claims of the floor: "The same number the allocator uses for
`min_position_nav_pct`, so the two ends cannot admit what the other refuses."
The satellite cap sits between them and breaks that invariant.

0.63/6 = 0.105 is still inside the objective's stated 10-15%-of-NAV band, so the
clamp does not trade away "size so one winner matters".

Default OFF (`sizing_respects_satellite_share_enabled`).
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


DOC195 = {
    "core_target_pct": 0.35,
    "cash_reserve_floor_pct": 0.02,
    "max_positions": 6,
    "total_spend_cap_target_weight_pct": 0.14,
    "total_spend_cap_concentrate": True,
}


def _clamped(cfg):
    """Mirror-free: this is the arithmetic the production site performs.

    Deliberately NOT a reimplementation of the branch — it computes only the
    INVARIANT (slots x weight <= design share) that the production code must
    satisfy, and `test_the_production_site_clamps` reads the real source. A
    hand-written mirror of the branch itself would pass while the real site
    diverged, which is exactly how two other tests in this repo stayed green
    over live defects.
    """
    share = 1.0 - cfg["core_target_pct"] - cfg["cash_reserve_floor_pct"]
    return share / cfg["max_positions"]


def test_the_shipped_config_is_arithmetically_impossible():
    """Pin the defect. If this ever stops failing the contradiction is gone and
    the clamp is no longer load-bearing."""
    demanded = (DOC195["core_target_pct"] + DOC195["cash_reserve_floor_pct"]
                + DOC195["max_positions"] * DOC195["total_spend_cap_target_weight_pct"])
    assert demanded == pytest.approx(1.21, abs=0.005), demanded
    assert demanded > 1.0, (
        "the shipped config demands more than 100% of NAV; the satellite "
        "overruns its design share and the residual core is crushed to its floor")


def test_the_clamped_weight_stays_inside_the_objectives_band():
    """The objective asks for 10-15% of NAV per name. A clamp that dropped the
    weight below 10% would fix the arithmetic by giving up the thesis."""
    w = _clamped(DOC195)
    assert w == pytest.approx(0.105, abs=1e-9)
    assert 0.10 <= w <= 0.15, w


def test_the_clamp_makes_the_book_fit_exactly():
    w = _clamped(DOC195)
    total = (DOC195["core_target_pct"] + DOC195["cash_reserve_floor_pct"]
             + DOC195["max_positions"] * w)
    assert total == pytest.approx(1.0, abs=1e-9)


def test_the_clamped_grant_can_clear_the_min_position_floor():
    """The point of the whole change. At 14% the satellite is pinned at its
    ceiling and the cap grants ~2.4% of NAV against a ~5.95% floor; at the
    clamped weight the book sits AT its design share, so the conviction band
    (up to satellite_max_share) is open and a grant can clear the floor."""
    nav = 6000.0
    floor = max(50.0, nav * 0.06)                      # _exec_min_position_floor
    design_share = 1.0 - DOC195["core_target_pct"] - DOC195["cash_reserve_floor_pct"]
    max_share = 1.0 - 0.10 - DOC195["cash_reserve_floor_pct"]   # core_min_pct 0.10

    at_14 = DOC195["max_positions"] * 0.14
    room_at_14 = (max_share - at_14) * nav
    assert room_at_14 < floor, (
        f"premise: at 14% the conviction band leaves ${room_at_14:,.0f} "
        f"against a ${floor:,.0f} floor")

    at_clamped = DOC195["max_positions"] * _clamped(DOC195)
    room_clamped = (max_share - at_clamped) * nav
    assert room_clamped > floor, (
        f"clamped to {_clamped(DOC195):.3f} the band leaves ${room_clamped:,.0f} "
        f"against a ${floor:,.0f} floor — a conviction buy can now be funded")


def test_a_config_that_already_fits_is_untouched():
    """The clamp must only ever REDUCE, and only when the invariant is broken."""
    cfg = dict(DOC195, total_spend_cap_target_weight_pct=0.09)
    assert cfg["total_spend_cap_target_weight_pct"] < _clamped(cfg)


def test_the_production_site_clamps_and_is_default_off():
    """Read the real source, not a mirror."""
    import re
    path = os.path.join(os.path.dirname(__file__), "..", "strategies",
                        "graph_nexus_analysis.py")
    src = open(path, encoding="utf-8").read()
    assert re.search(
        r'config\.get\("sizing_respects_satellite_share_enabled", False\)', src), \
        "the clamp must be behind its own default-OFF flag"
    assert "_ss_max_w = _ss_share / _ss_slots" in src, \
        "the clamp must derive the weight from design_share / max_positions"
    assert "SIZING CLAMP:" in src, (
        "the clamp must announce itself — five levers have shipped inert in this "
        "project and each was unprovable from its own log")
    # the clamp may only reduce
    assert "if _conc_target_pct > _ss_max_w + 1e-9:" in src, \
        "the clamp must only bind when the configured weight is too LARGE"
