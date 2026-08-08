"""All four max_positions counters must exclude the sleeve legs TOGETHER.

bt 820236: SNDK cleared every gate on 01-12 -- sized at $873 (14.6% of NAV) by
the concentrate allocator, funded by the satellite overflow, waved through a
105% turnover budget by the conviction bypass -- and died on

    MAX_POSITIONS_GATE: blocked SNDK (held=6, cap=6)

on a log line that simultaneously read `open_pos=5`. `_mpg_held` counts the SPY
core leg; the buy gate's counter does not.

broker.py:14099-14117 records that excluding it from `_mpg_held` ALONE was
written and reverted: `_z41_held_now`, `_count_open_positions` and
`_mw_open_set` count the legs too, so a one-site fix desynchronises them and
moved the latched bear's headroom from 0 to len(legs), re-opening the per-bar
refill the latch exists to stop. These tests pin that all four move together.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from strategies.graph_nexus_analysis import (  # noqa: E402
    _count_open_positions,
    slot_exclusions,
)


ON = {"residual_sleeve_enabled": True, "residual_sleeve_symbol": "SPY",
      "residual_sleeve_bear_symbol": "SQQQ",
      "max_positions_exclude_sleeve_legs": True}
OFF = dict(ON, max_positions_exclude_sleeve_legs=False)


class _Emu:
    def __init__(self, positions):
        self._positions = dict(positions)

    def get_positions(self):
        return dict(self._positions)


BOOK = {"SPY": 3.5, "WDC": 1.7, "LRCX": 0.8, "CPER": 20.0, "TXN": 4.0, "OMER": 9.0}


def test_default_off_is_byte_identical():
    assert slot_exclusions(OFF) == set()
    assert _count_open_positions(_Emu(BOOK), slot_exclusions(OFF)) == 6


def test_the_core_leg_gives_up_its_slot():
    """held=6/cap=6 becomes 5/6 -- the slot SNDK was refused for."""
    assert slot_exclusions(ON) == {"SPY", "SQQQ"}
    assert _count_open_positions(_Emu(BOOK), slot_exclusions(ON)) == 5


def test_the_bear_leg_gives_up_its_slot_too():
    book = dict(BOOK); book.pop("SPY"); book["SQQQ"] = 12.0
    assert _count_open_positions(_Emu(book), slot_exclusions(ON)) == 5


def test_all_four_counters_agree_by_construction():
    """The regression that got the one-site version reverted.

    _mpg_held (broker), _z41_held_now, _count_open_positions and _mw_open_set
    must every one of them read the SAME exclusion set, or the latch gains
    headroom the gate does not know about.
    """
    excl = slot_exclusions(ON)

    mpg_held = {s.upper() for s, q in BOOK.items() if q > 0 and s.upper() not in excl}
    z41 = _count_open_positions(_Emu(BOOK), excl)
    counted = _count_open_positions(_Emu(BOOK), excl)
    mw_open = {s.upper() for s, q in BOOK.items() if q > 0 and s.upper() not in excl}

    assert len(mpg_held) == z41 == counted == len(mw_open) == 5
    assert "SPY" not in mpg_held and "SPY" not in mw_open


def test_latch_headroom_cannot_go_positive_from_the_exclusion_alone():
    """The exact reverted regression: a latched bear holding ONLY sleeve legs
    must still read a full book, not free capacity."""
    only_legs = {"SPY": 3.5, "SQQQ": 2.0}
    # every counter sees zero ALPHA positions -- that is correct and shared,
    # so the latch and the gate cannot disagree about it
    assert _count_open_positions(_Emu(only_legs), slot_exclusions(ON)) == 0
    assert _count_open_positions(_Emu(only_legs), slot_exclusions(OFF)) == 2


def test_zero_quantity_legs_are_not_counted_either_way():
    book = dict(BOOK); book["SPY"] = 0.0
    assert _count_open_positions(_Emu(book), slot_exclusions(OFF)) == 5
    assert _count_open_positions(_Emu(book), slot_exclusions(ON)) == 5


def test_padded_config_value_still_matches_a_position_key():
    cfg = dict(ON, residual_sleeve_symbol=" SPY ")
    assert _count_open_positions(_Emu(BOOK), slot_exclusions(cfg)) == 5


def test_inert_when_the_sleeve_is_disabled():
    cfg = dict(ON, residual_sleeve_enabled=False)
    assert slot_exclusions(cfg) == set()
    assert _count_open_positions(_Emu(BOOK), slot_exclusions(cfg)) == 6


def test_alpha_names_are_never_exempted():
    assert "WDC" not in slot_exclusions(ON)
    assert "SNDK" not in slot_exclusions(ON)


def test_malformed_config_fails_closed_to_no_exclusion():
    """Fail CLOSED here: an unreadable config must not silently hand out slots."""
    assert slot_exclusions(None) == set()
    assert slot_exclusions({}) == set()
    # flag on but no sleeve configured -> nothing to exclude, so no free slot
    assert slot_exclusions({"max_positions_exclude_sleeve_legs": True}) == set()


def test_the_freed_slot_is_what_sndk_needed():
    """cap=6, book=6 refuses; excluding the core leaves room for exactly one."""
    cap = 6
    assert _count_open_positions(_Emu(BOOK), slot_exclusions(OFF)) >= cap   # refused
    assert _count_open_positions(_Emu(BOOK), slot_exclusions(ON)) < cap     # admitted
