"""The fourth max_positions counter must move onto `slot_exclusions` too.

`slot_exclusions` (graph_nexus_analysis.py:5630) exists because a one-site
sleeve-leg exclusion was written and reverted, with the instruction to "do this
properly by moving all four counters together". Three were moved onto the helper
(:29497 `_z41_held_now`, :30944 `_mw_open_set`, :31921 `_count_open_positions`).
The fourth — the V28.8.1 breach counter — was left as

    _current_positions = len(portfolio_emulator.get_positions()) ...

so it was the only site that disagreed: the state the note warned about rather
than the state it described.

WHY THE EXISTING SUITE DID NOT CATCH IT. `test_max_positions_sleeve_exclusion.py`
::test_all_four_counters_agree_by_construction is a HAND-WRITTEN MIRROR — it
re-implements each counter inline (`{s.upper() for s, q in BOOK.items() ...}`)
and never calls the real ones. A mirror can only ever prove the mirror agrees
with itself, so it stayed green for a week while the real fourth site diverged.
The same anti-pattern was found the same day in
`test_buy_order_conviction_ranked.py`. These tests read the SOURCE instead.

WHY IT MATTERS MOST WHEN THE CAP IS TIGHTENED. With the core leg held and a cap
of 4, the breach counter reads 5 while the buy gate reads 4, so
`_current_positions > _max_positions` latches a permanent BREACH and blocks every
new-ticker buy. The alpha book gets 3 usable slots and the flag never clears.

Default OFF (`slot_exclusions_all_counters_enabled`) — deliberately its own key
rather than the existing `max_positions_exclude_sleeve_legs`, which doc-193
already sets, so honouring it here would change a production document with no
paired run behind it.
"""
import ast
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from strategies.graph_nexus_analysis import (  # noqa: E402
    _count_open_positions,
    slot_exclusions,
)

_GNA = os.path.join(os.path.dirname(__file__), "..", "strategies",
                    "graph_nexus_analysis.py")
_SRC = open(_GNA, encoding="utf-8").read()

ON = {"residual_sleeve_enabled": True, "residual_sleeve_symbol": "SPY",
      "residual_sleeve_bear_symbol": "SQQQ",
      "max_positions_exclude_sleeve_legs": True,
      "slot_exclusions_all_counters_enabled": True}
OFF = dict(ON, slot_exclusions_all_counters_enabled=False)

# a full book at cap=4: three alpha names plus both sleeve legs
BOOK = {"SPY": 3.5, "SQQQ": 2.0, "WDC": 1.7, "LRCX": 0.8, "TXN": 4.0}


class _Emu:
    def __init__(self, positions):
        self._positions = dict(positions)

    def get_positions(self):
        return dict(self._positions)


def test_the_breach_counter_site_is_guarded_and_uses_the_helper():
    """Structural. Reads the real source at the fourth site."""
    m = re.search(
        r"if bool\(config\.get\(\"slot_exclusions_all_counters_enabled\", False\)\):\s*\n"
        r"\s*_current_positions = _count_open_positions\(\s*\n"
        r"\s*portfolio_emulator, slot_exclusions\(config\)\)\s*\n"
        r"\s*else:\s*\n"
        r"\s*_current_positions = len\(portfolio_emulator\.get_positions\(\)\)",
        _SRC)
    assert m, (
        "the V28.8.1 breach counter no longer reads as "
        "`slot_exclusions_all_counters_enabled` -> _count_open_positions(..., "
        "slot_exclusions(config)) with the raw len() as the else branch. If this "
        "was refactored, re-pin it — a hand-written mirror in the sibling test "
        "file cannot see a divergence here.")


def test_no_other_slot_counter_still_uses_a_bare_len():
    """Any NEW bare `len(...get_positions())` used as a slot count is the same
    defect appearing somewhere else."""
    hits = [i + 1 for i, line in enumerate(_SRC.splitlines())
            if re.search(r"len\(portfolio_emulator\.get_positions\(\)\)", line)]
    assert hits == [_breach_else_line()], (
        f"bare position-count expressions at lines {hits}; only the guarded "
        "else-branch of the breach counter may use one")


def _breach_else_line():
    for i, line in enumerate(_SRC.splitlines(), 1):
        if "_current_positions = len(portfolio_emulator.get_positions())" in line:
            return i
    raise AssertionError("breach counter else-branch not found")


def test_the_helper_is_what_the_other_three_counters_call():
    """Guard the premise: if the other three stop calling `slot_exclusions`,
    moving this one onto it is no longer 'moving them together'."""
    tree = ast.parse(_SRC)
    calls = sum(
        1 for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "slot_exclusions")
    assert calls >= 4, (
        f"only {calls} call sites of slot_exclusions; the design is ONE "
        "definition shared by every counter that decides capacity")


def test_flag_off_counts_the_legs_exactly_as_before():
    assert slot_exclusions(OFF) == {"SPY", "SQQQ"}, (
        "the OFF switch is the NEW key; the pre-existing "
        "max_positions_exclude_sleeve_legs must be untouched by it")
    assert _count_open_positions(_Emu(BOOK), set()) == 5


def test_flag_on_frees_the_two_legs_at_a_cap_of_four():
    """The state the treatment arm runs in: cap 4, both legs held."""
    cap = 4
    assert _count_open_positions(_Emu(BOOK), set()) > cap, (
        "premise: the raw count breaches a cap of 4 and latches BREACH")
    assert _count_open_positions(_Emu(BOOK), slot_exclusions(ON)) == 3
    assert _count_open_positions(_Emu(BOOK), slot_exclusions(ON)) < cap, (
        "with the legs excluded the alpha book has real headroom at cap 4")


def test_a_closed_position_left_at_zero_does_not_consume_a_slot():
    """The second, smaller error in the same expression: `len()` counts dict
    ENTRIES, so a position closed to qty 0 still took a slot."""
    book = dict(BOOK, LRCX=0.0)
    assert len(book) == 5
    assert _count_open_positions(_Emu(book), slot_exclusions(ON)) == 2


def test_the_new_key_is_independent_of_the_old_one():
    """doc-193 sets max_positions_exclude_sleeve_legs; it must not switch this
    site on by itself."""
    doc193_shape = {"residual_sleeve_enabled": True,
                    "residual_sleeve_symbol": "SPY",
                    "max_positions_exclude_sleeve_legs": True}
    assert doc193_shape.get("slot_exclusions_all_counters_enabled", False) is False
    assert re.search(
        r"config\.get\(\"slot_exclusions_all_counters_enabled\", False\)", _SRC), \
        "the site must read its OWN key, not max_positions_exclude_sleeve_legs"
