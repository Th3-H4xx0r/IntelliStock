"""A position too small to matter must not cost a max_positions slot.

The allocator already enforces a NAV-proportional floor on SIZED buys via
`min_position_nav_pct`. The execution path did not: it used a hardcoded $50,
which is 0.8% of a $6,000 book. So a buy sized at 14% of NAV and then truncated
by available cash still opened a position AND consumed a slot.

bt 371379, on a book that was refusing new names at the cap:
    GH   sized $32.55  -> filled $32.41  (0.5% of NAV)   open_pos=7
    AMZN sized $557.05 -> cash  $108.30  (1.8%)          open_pos=7
    ETN  sized $467.91 -> cash  $162.37  (2.7%)          open_pos=7

That is the objective's blocker #3 — "a great name is refused because a mediocre
one sits on the budget" — arriving through the execution path rather than the
allocator.
"""
import pytest


HISTORICAL_MIN = 50.0


def exec_min_pos(config, nav):
    """Mirror of the broker-side floor: dollar minimum or NAV share, larger wins."""
    floor = HISTORICAL_MIN
    try:
        pct = float((config or {}).get("min_position_nav_pct", 0.0) or 0.0)
    except (TypeError, ValueError):
        return floor
    if pct > 0 and nav and nav > 0:
        floor = max(floor, float(nav) * pct)
    return floor


def skips(cash_to_use, cash_per_trade, config, nav):
    """The broker's skip test.

    When a NAV floor is configured, size is the whole question and the buy is
    refused however it got small. Without one, the legacy rule stands: only
    refuse a buy that was TRUNCATED from a larger intent. That legacy clause is
    the hole GH went through — the allocator had already sized it down to the
    available $32.55, so cash_to_use == cash_per_trade and it never fired.
    """
    m = exec_min_pos(config, nav)
    hard = m > HISTORICAL_MIN
    return cash_to_use < m and (hard or cash_to_use < cash_per_trade)


NAV = 6000.0
ON = {"min_position_nav_pct": 0.06}      # $360 on this book
OFF = {}


def test_default_keeps_the_historical_fifty_dollar_floor():
    assert exec_min_pos(OFF, NAV) == pytest.approx(50.0)
    # legacy rule intact: a truncated sub-$50 buy is refused, an untruncated one
    # is not — GH slipped through precisely because it was NOT truncated.
    assert skips(32.41, 900.0, OFF, NAV) is True
    assert skips(32.55, 32.55, OFF, NAV) is False, "the hole, preserved when off"


def test_the_three_runts_that_ate_slots_are_now_skipped():
    for cash_to_use, sized in ((32.55, 32.55), (108.30, 557.05), (162.37, 467.91)):
        assert skips(cash_to_use, sized, ON, NAV) is True


def test_a_full_size_buy_is_untouched():
    assert skips(839.97, 839.97, ON, NAV) is False


def test_a_buy_at_exactly_the_floor_is_kept():
    assert skips(360.0, 840.0, ON, NAV) is False


def test_an_untruncated_runt_is_still_refused_when_a_floor_is_set():
    """GH's exact shape: allocator already sized it down, so nothing was
    'truncated' — and it still must not take a slot."""
    assert skips(100.0, 100.0, ON, NAV) is True
    assert skips(100.0, 100.0, OFF, NAV) is False


def test_the_floor_scales_with_the_book():
    assert exec_min_pos(ON, 6000.0) == pytest.approx(360.0)
    assert exec_min_pos(ON, 60000.0) == pytest.approx(3600.0)
    assert exec_min_pos(ON, 500.0) == pytest.approx(50.0), "never below the dollar floor"


def test_zero_or_missing_nav_falls_back():
    assert exec_min_pos(ON, 0.0) == pytest.approx(50.0)
    assert exec_min_pos(ON, None) == pytest.approx(50.0)


def test_malformed_config_falls_back():
    for bad in ({"min_position_nav_pct": "x"}, None, {"min_position_nav_pct": -1}):
        assert exec_min_pos(bad, NAV) == pytest.approx(50.0)


def test_it_agrees_with_the_allocator_floor():
    """Both ends must use the same number or one will admit what the other
    refuses, which is how the runts got through in the first place."""
    allocator_floor = max(100.0, NAV * ON["min_position_nav_pct"])
    assert exec_min_pos(ON, NAV) == pytest.approx(allocator_floor)
