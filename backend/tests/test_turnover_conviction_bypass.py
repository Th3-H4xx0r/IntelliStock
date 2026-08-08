"""The turnover brake is for churn, not for the trade that matters (bt 264179).

Concentrating into 14%-of-NAV positions raises turnover mechanically -- the
opening basket alone is ~56% of NAV -- so the 50% monthly brake was already
pinned when the winner arrived:

    V31.2 total-spend cap [CONCENTRATE]: funded 3 of 5 by conviction (SNDK@$879, ...)
    SATELLITE OVERFLOW: SNDK raw=+1.700 >= 1.50
    TURNOVER BUDGET BLOCK: SNDK skipped - 67% of NAV traded in 21 sessions

That repeated 01-12, 01-13, 01-14 with SNDK sized at 14.6% of NAV. SNDK was
finally bought 01-30 at $614.80 for $126 -- 96.7% through a +166% move -- and
contributed $3.42.
"""
import pytest


CONV_MIN = 1.5


def admits(raw, turnover_blocked, bypass_enabled, conv_min=CONV_MIN):
    """Mirror of the broker-side bypass decision."""
    if not turnover_blocked:
        return True
    if not bypass_enabled or conv_min <= 0:
        return False
    return float(raw) >= conv_min


def test_the_bug_a_pinned_brake_refuses_the_winner():
    assert admits(1.700, turnover_blocked=True, bypass_enabled=False) is False


def test_conviction_passes_a_pinned_brake():
    """SNDK carried raw=+1.700 on every one of those blocked bars."""
    assert admits(1.700, turnover_blocked=True, bypass_enabled=True) is True


def test_ordinary_churn_is_still_refused():
    """doc-193 measured p50 1.000 / p75 1.300 / p90 1.800. Everything below the
    top decile stays blocked, so the sweep's rotation churn is unaffected."""
    for raw in (0.0, 0.5, 1.000, 1.300, 1.499):
        assert admits(raw, turnover_blocked=True, bypass_enabled=True) is False


def test_boundary_is_inclusive_and_matches_the_overflow_cutoff():
    assert admits(1.5, turnover_blocked=True, bypass_enabled=True) is True


def test_default_off_changes_nothing():
    for raw in (0.0, 1.700, 2.5):
        assert admits(raw, turnover_blocked=True, bypass_enabled=False) is False


def test_disabled_conviction_threshold_disables_the_bypass():
    """conv_min is the satellite overflow cutoff; 0 means the overflow is off,
    so there is no notion of 'conviction' to bypass with."""
    assert admits(1.700, turnover_blocked=True, bypass_enabled=True, conv_min=0.0) is False


def test_an_unpinned_brake_admits_everything_as_before():
    for raw in (0.0, 1.700):
        assert admits(raw, turnover_blocked=False, bypass_enabled=True) is True
        assert admits(raw, turnover_blocked=False, bypass_enabled=False) is True


def test_a_bad_score_cannot_sneak_through_on_magnitude():
    """raw_net_score is signed; a -1.7 bearish aggregate must not read as
    conviction (the same trap the min-paths bypass had to fix)."""
    assert admits(-1.700, turnover_blocked=True, bypass_enabled=True) is False
