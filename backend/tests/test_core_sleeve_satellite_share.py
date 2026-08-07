"""Regime-aware satellite-share resolution (bt 804832).

The bug: `core_sleeve_enabled` lives ONLY in `regime_profiles.*` on doc-193, and
`_apply_regime_profile` merges the matching overlay in before the config is
read. During warm-up no overlay has merged, so the allocator's satellite clamp
read False and sized the opening basket against `nexus_portfolio_pct` (0.95)
instead of the 0.38 design share. It opened at 48.5% of NAV and satellite
headroom was negative for the remaining two months.

The trap: an absent flag is AMBIGUOUS. It also means "this regime has no
profile, so the core is off on purpose" — which is exactly doc-193's bear, and
`test_regime_conditional_core` measures that bear arm at +10.07% precisely
because the core stays off there. Arming the clamp in bear would reserve ~62% of
NAV for a core that is never bought. The detected regime is what separates the
two cases.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core_sleeve import (  # noqa: E402
    core_sleeve_armed_for_bar,
    satellite_design_share,
)

# Shape of doc-193: flag and target live only in the profiles, and there is no
# `bear` profile at all.
DOC193 = {
    "cash_reserve_floor_pct": 0.02,
    "nexus_portfolio_pct": 0.95,
    "regime_profiles": {
        "bull": {"core_sleeve_enabled": True, "core_target_pct": 0.6},
        "chop": {"core_sleeve_enabled": True, "core_target_pct": 0.6},
        "recovery": {"core_sleeve_enabled": True, "core_target_pct": 0.6},
    },
}


def test_warmup_arms_the_clamp_before_any_overlay_has_merged():
    """The actual bt 804832 defect: tick 1, no regime yet, flag not merged."""
    assert core_sleeve_armed_for_bar(DOC193, regime=None) is True
    assert abs(satellite_design_share(DOC193, regime=None) - 0.38) < 1e-9


def test_bear_does_not_arm_the_clamp_when_it_has_no_profile():
    """doc-193 has no bear overlay, so the core is off in bear BY DESIGN.

    Arming here would reserve 62% of NAV for a core that is never bought, and
    would cost the hedge that is the whole bear result.
    """
    assert core_sleeve_armed_for_bar(DOC193, regime="bear") is False


def test_a_merged_overlay_arms_the_clamp():
    merged = dict(DOC193, core_sleeve_enabled=True, core_target_pct=0.6)
    assert core_sleeve_armed_for_bar(merged, regime="bull") is True
    assert abs(satellite_design_share(merged, regime="bull") - 0.38) < 1e-9


def test_a_known_regime_reads_its_own_profile_not_the_unmerged_flag():
    """bt 632754 caught this: the FIRST version of the fix reproduced the bug.

    The detector picks a regime almost immediately (`V31 market regime: chop
    (closes=90)`), but the overlay never reaches the allocator's `config` — so
    `core_sleeve_enabled` is still absent while `regime` is already "chop".
    Short-circuiting to False on a known regime therefore left the clamp inert
    on exactly the tick that builds the book, which is the original defect.
    """
    assert core_sleeve_armed_for_bar(DOC193, regime="chop") is True
    assert abs(satellite_design_share(DOC193, regime="chop") - 0.38) < 1e-9
    assert core_sleeve_armed_for_bar(DOC193, regime="bull") is True
    assert core_sleeve_armed_for_bar(DOC193, regime="recovery") is True


def test_a_regime_whose_profile_disables_the_core_does_not_arm():
    cfg = {
        "cash_reserve_floor_pct": 0.02,
        "regime_profiles": {
            "bull": {"core_sleeve_enabled": True, "core_target_pct": 0.6},
            "crash": {"core_sleeve_enabled": False},
        },
    }
    assert core_sleeve_armed_for_bar(cfg, regime="crash") is False
    assert core_sleeve_armed_for_bar(cfg, regime="bull") is True


def test_a_doc_with_no_core_anywhere_never_arms():
    """doc-179 shape — the live real-money doc must be untouched."""
    bare = {"nexus_portfolio_pct": 0.95, "cash_reserve_floor_pct": 0.02}
    assert core_sleeve_armed_for_bar(bare, regime=None) is False
    assert core_sleeve_armed_for_bar(bare, regime="bull") is False


def test_disagreeing_profiles_resolve_order_independently():
    """`first profile wins` was order-dependent, and RethinkDB returns SORTED
    keys — so `bear` would have won by being alphabetically first, imposing a
    de-risked target on every bull bar. Resolution must not depend on key order.
    """
    a = {
        "cash_reserve_floor_pct": 0.02,
        "regime_profiles": {
            "bull": {"core_sleeve_enabled": True, "core_target_pct": 0.5},
            "chop": {"core_sleeve_enabled": True, "core_target_pct": 0.7},
        },
    }
    b = {
        "cash_reserve_floor_pct": 0.02,
        "regime_profiles": {
            "chop": {"core_sleeve_enabled": True, "core_target_pct": 0.7},
            "bull": {"core_sleeve_enabled": True, "core_target_pct": 0.5},
        },
    }
    assert satellite_design_share(a) == satellite_design_share(b)
    # Conservative reading: the LARGEST core target, i.e. tightest satellite.
    assert abs(satellite_design_share(a) - (1.0 - 0.7 - 0.02)) < 1e-9
    # A named regime uses its OWN target rather than the pooled worst case.
    assert abs(satellite_design_share(a, regime="bull") - (1.0 - 0.5 - 0.02)) < 1e-9


def test_a_profile_with_the_core_off_cannot_set_the_share():
    """A regime that switches the core OFF has no say in how much room it
    reserves. Alphabetically `bear` sorts first, so this also pins ordering."""
    cfg = {
        "cash_reserve_floor_pct": 0.02,
        "regime_profiles": {
            "bear": {"core_sleeve_enabled": False, "core_target_pct": 0.9},
            "bull": {"core_sleeve_enabled": True, "core_target_pct": 0.3},
        },
    }
    assert abs(satellite_design_share(cfg) - (1.0 - 0.3 - 0.02)) < 1e-9


def test_share_stays_inside_its_bounds_on_hostile_input():
    base = {"cash_reserve_floor_pct": 0.02}
    for target in (0.99, 1.5, 2.0, -3.0, float("inf"), float("nan"), "abc", None, True):
        share = satellite_design_share(dict(base, core_target_pct=target))
        assert 0.05 <= share <= 0.95, (target, share)


def test_non_dict_and_empty_configs_do_not_raise():
    for cfg in (None, {}, {"regime_profiles": None}, {"regime_profiles": []}):
        assert core_sleeve_armed_for_bar(cfg, regime=None) is False
        assert 0.05 <= satellite_design_share(cfg) <= 0.95
