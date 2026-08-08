"""A new entry must be worth a max_positions SLOT (bt 498816).

`min_position_size` is an absolute dollar floor, so on a $6,000 book it admits a
1.7%-of-NAV entry. bt 498816 opened three names at 12.0% each and then let AMD
($94, 1.6%) and KLAC ($100, 1.7%) take two of the five alpha slots via BFQ lanes
that bypass the concentrate cap. With max_positions=6 and the index core holding
one, those runts were 40% of the alpha book's CAPACITY for 3.3% of its money:

    MAX_POSITIONS_GATE: blocked SNDK (held=6, cap=6)   [01-12, 01-13, 01-14]

SNDK finally entered on 01-16 at $414.69 instead of $388.46. That is the
objective's blocker #3 -- "a great name is refused because a mediocre one sits
on the budget". The scarce resource is the SLOT, so the floor must be a share of
NAV, not a dollar amount.
"""
import pytest


def effective_floor(config, portfolio_total):
    """Mirror of the broker-side floor resolution."""
    floor = float(config.get("min_position_size", 100.0) or 100.0)
    pct = float(config.get("min_position_nav_pct", 0.0) or 0.0)
    if pct > 0 and portfolio_total > 0:
        floor = max(floor, float(portfolio_total) * pct)
    return floor


def surviving(sizes, config, nav, held=frozenset()):
    f = effective_floor(config, nav)
    return {s: v for s, v in sizes.items()
            if s in held or not (0.0 < float(v) < f)}


NAV = 6000.0
BT498816 = {"NXT": 720.0, "WDC": 720.0, "CPER": 720.0, "AMD": 94.0, "KLAC": 100.0}


def test_default_off_admits_the_runts_exactly_as_before():
    cfg = {"min_position_size": 100.0}
    assert effective_floor(cfg, NAV) == pytest.approx(100.0)
    # AMD at $94 is the only one the old absolute floor caught
    assert set(surviving(BT498816, cfg, NAV)) == {"NXT", "WDC", "CPER", "KLAC"}


def test_six_percent_floor_frees_both_runt_slots():
    cfg = {"min_position_size": 100.0, "min_position_nav_pct": 0.06}
    assert effective_floor(cfg, NAV) == pytest.approx(360.0)
    assert set(surviving(BT498816, cfg, NAV)) == {"NXT", "WDC", "CPER"}


def test_real_positions_are_never_dropped():
    cfg = {"min_position_size": 100.0, "min_position_nav_pct": 0.06}
    out = surviving(BT498816, cfg, NAV)
    assert all(out[s] == pytest.approx(720.0) for s in out)


def test_floor_scales_with_the_book():
    cfg = {"min_position_size": 100.0, "min_position_nav_pct": 0.06}
    assert effective_floor(cfg, 6000.0) == pytest.approx(360.0)
    assert effective_floor(cfg, 60000.0) == pytest.approx(3600.0)
    # never below the absolute floor on a tiny book
    assert effective_floor(cfg, 500.0) == pytest.approx(100.0)


def test_adds_to_held_names_are_exempt():
    """winner_add / anchor_reinforce top-ups have their own per-path floors."""
    cfg = {"min_position_size": 100.0, "min_position_nav_pct": 0.06}
    out = surviving({"SNDK": 150.0}, cfg, NAV, held=frozenset({"SNDK"}))
    assert out == {"SNDK": 150.0}


def test_zero_nav_falls_back_to_the_absolute_floor():
    cfg = {"min_position_size": 100.0, "min_position_nav_pct": 0.06}
    assert effective_floor(cfg, 0.0) == pytest.approx(100.0)


def test_slots_freed_are_what_the_winner_needed():
    """Two runt slots freed is exactly the capacity SNDK was refused for."""
    cfg = {"min_position_size": 100.0, "min_position_nav_pct": 0.06}
    before = len(surviving(BT498816, cfg, NAV, held=frozenset(BT498816)))
    after = len(surviving(BT498816, cfg, NAV))
    assert before - after == 2
