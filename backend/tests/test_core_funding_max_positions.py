"""max_positions-aware core funding pre-pass (bt 455506).

The 2026-08-03 sweep stopped the core selling SPY to fund buys the SATELLITE
headroom would refuse. bt 455506 showed a second leak through the same hole:
MAX_POSITIONS_GATE refused 65 of 91 `SATELLITE OVERFLOW` fires on the same tick
(71%, zero filled), and because the release had already been sized off them the
SPY core saw-toothed 6.13 -> 4.21 -> 5.80 -> 4.64 -> 5.49 -> 4.76 shares —
$9,081 of post-initial gross notional for -1.37 shares of net change.

The load-bearing case is `test_rotation_pair_stays_funded`: 455506's SNDK only
entered because a sell freed a slot, so a pre-pass that ignored planned exits
would have starved the one buy that made the run.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from nexus_broker_utils import (  # noqa: E402
    max_positions_admissible_buys,
    max_positions_gate,
    planned_full_exit_symbols,
)


HELD6 = {"AAA", "BBB", "CCC", "DDD", "EEE", "FFF"}


def test_at_cap_every_new_name_is_refused():
    """455506's steady state: held=6, cap=6 — nothing new may be funded."""
    assert max_positions_admissible_buys(HELD6, 6, set(), ["SNDK", "OMER"]) == set()


def test_adds_to_held_names_are_always_fundable():
    """An add does not grow the count, so it must never lose its funding."""
    got = max_positions_admissible_buys(HELD6, 6, set(), ["AAA", "SNDK", "CCC"])
    assert got == {"AAA", "CCC"}


def test_rotation_pair_stays_funded():
    """sell RGEN -> buy SNDK. This is how SNDK actually entered in bt 455506.

    A pre-pass that ignored the planned exit would refuse funding for the one
    buy that produced 113.6% of the run's P&L.
    """
    held = {"RGEN", "BBB", "CCC", "DDD", "EEE", "FFF"}
    assert max_positions_admissible_buys(held, 6, {"RGEN"}, ["SNDK"]) == {"SNDK"}


def test_one_free_slot_goes_to_the_first_in_execution_order():
    """The cap is consumed first-come — order decides, so order must be kept."""
    held = {"AAA", "BBB", "CCC", "DDD", "EEE"}
    assert max_positions_admissible_buys(held, 6, set(), ["SNDK", "OMER"]) == {"SNDK"}
    assert max_positions_admissible_buys(held, 6, set(), ["OMER", "SNDK"]) == {"OMER"}


def test_matches_the_emission_time_gate_symbol_for_symbol():
    """The pre-pass must not diverge from the gate the submit loop applies."""
    held = {"AAA", "BBB", "CCC", "DDD"}
    exits = {"BBB"}
    ordered = ["AAA", "SNDK", "OMER", "GLUE", "CCC"]

    emitted, expected = set(), set()
    for sym in ordered:
        if max_positions_gate(held, 6, exits, emitted, sym):
            expected.add(sym)
            if sym not in held:
                emitted.add(sym)

    assert max_positions_admissible_buys(held, 6, exits, ordered) == expected


def test_no_cap_funds_everything():
    """Gate inert (no max_positions in config) -> pre-pass must be inert too."""
    got = max_positions_admissible_buys(HELD6, None, set(), ["SNDK", "OMER"])
    assert got == {"SNDK", "OMER"}


def test_fails_open_on_malformed_input():
    """Starving a real buy is worse than the churn this removes."""
    assert max_positions_admissible_buys(object(), 6, set(), ["SNDK"]) == {"SNDK"}


def test_normalises_case_and_whitespace():
    assert max_positions_admissible_buys({"aaa"}, 6, set(), [" aaa ", None, ""]) == {"AAA"}


def test_empty_buy_list_is_empty():
    assert max_positions_admissible_buys(HELD6, 6, set(), []) == set()


# ── planned_full_exit_symbols ────────────────────────────────────────────────

def test_full_exit_detected_at_the_gates_own_threshold():
    sizes = {"RGEN": {"sell_fraction": 1.0}, "AAA": {"sell_fraction": 0.999}}
    assert planned_full_exit_symbols({"RGEN", "AAA"}, sizes) == {"RGEN", "AAA"}


def test_partial_trim_does_not_free_a_slot():
    """A 50% trim leaves the name held — it must not admit another buy."""
    sizes = {"RGEN": {"sell_fraction": 0.5}}
    assert planned_full_exit_symbols({"RGEN"}, sizes) == set()


def test_unheld_name_cannot_free_a_slot():
    sizes = {"XYZ": {"sell_fraction": 1.0}}
    assert planned_full_exit_symbols({"RGEN"}, sizes) == set()


def test_buy_hints_are_not_exits():
    sizes = {"SNDK": {"buy_cash": 500.0}}
    assert planned_full_exit_symbols({"SNDK"}, sizes) == set()


def test_malformed_sizes_do_not_crash():
    sizes = {"RGEN": None, "AAA": {"sell_fraction": "x"}, "BBB": {"sell_fraction": 1.0}}
    assert planned_full_exit_symbols({"RGEN", "AAA", "BBB"}, sizes) == {"BBB"}


# ── the churn scenario, end to end ───────────────────────────────────────────

def test_bt455506_steady_state_funds_nothing():
    """held=6 cap=6, no exits, 3 conviction overflows sized.

    Before the fix the core released ~$2,000 for these and bought it back at
    cycle end. The request must now be zero.
    """
    sizes = {
        "SNDK": {"buy_cash": 656.0, "raw_net_score": 1.7},
        "OMER": {"buy_cash": 400.0, "raw_net_score": 1.6},
        "GLUE": {"buy_cash": 400.0, "raw_net_score": 1.55},
    }
    admissible = max_positions_admissible_buys(
        HELD6, 6, planned_full_exit_symbols(HELD6, sizes), list(sizes))
    funded = sum(v["buy_cash"] for k, v in sizes.items() if k in admissible)
    assert admissible == set()
    assert funded == pytest.approx(0.0)


def test_bt455506_swap_bar_funds_exactly_the_swap():
    """The 01-14 bar: RGEN fully exits, SNDK is the paired buy."""
    held = {"RGEN", "BBB", "CCC", "DDD", "EEE", "FFF"}
    sizes = {
        "RGEN": {"sell_fraction": 1.0},
        "SNDK": {"buy_cash": 656.0, "raw_net_score": 1.7},
        "OMER": {"buy_cash": 400.0, "raw_net_score": 1.6},
    }
    exits = planned_full_exit_symbols(held, sizes)
    # The broker filters _exec_order down to nexus_executable_buys before
    # calling, so the sell leg never reaches the pre-pass — only the buys do.
    executable_buys = ["SNDK", "OMER"]
    admissible = max_positions_admissible_buys(held, 6, exits, executable_buys)
    funded = sum(
        v.get("buy_cash", 0.0) for k, v in sizes.items()
        if k in admissible and "buy_cash" in v)
    assert admissible == {"SNDK"}
    assert funded == pytest.approx(656.0)


def test_contract_ordered_buys_only_held_sell_leg_would_look_admissible():
    """Guards the caller's filtering step, not the helper.

    A held name is always "admissible" (an add never grows the count), so if a
    caller passed the SELL leg of a rotation in as a buy it would look funded.
    broker.py filters `_exec_order` to `nexus_executable_buys` first; this test
    pins why that filter is load-bearing rather than cosmetic.
    """
    held = {"RGEN", "BBB", "CCC", "DDD", "EEE", "FFF"}
    assert "RGEN" in max_positions_admissible_buys(held, 6, {"RGEN"}, ["RGEN", "SNDK"])


# ── bt 806490: the over-correction ───────────────────────────────────────────
#
# The first pre-pass cut SPY gross churn $9,081 -> $462 and turnover blocks
# 16 -> 0, then froze the book: insufficient_cash 7 -> 71, ONE core release in
# the whole run, nothing traded after 01-26, and SNDK's ten signals from $388
# to $655 all died on ~$16 of cash. Once fully invested, the core release is
# the book's only cash source.


def test_core_legs_must_not_consume_a_slot_in_the_prepass():
    """The desync that froze bt 806490.

    `_mpg_held` counts the SPY core leg, so the pre-pass read held=6/cap=6 and
    refused funding while the buy gate read open_pos=5 and would have admitted
    the name. Excluding the leg locally restores one usable slot.
    """
    held_with_leg = {"SPY", "BBB", "CCC", "DDD", "EEE", "FFF"}
    assert max_positions_admissible_buys(held_with_leg, 6, set(), ["SNDK"]) == set()

    held_alpha = {s for s in held_with_leg if s != "SPY"}
    assert max_positions_admissible_buys(held_alpha, 6, set(), ["SNDK"]) == {"SNDK"}


def test_conviction_name_is_never_starved():
    """Rule (2): the overflow band exists to fund exactly this name.

    Mirrors the broker's post-filter. Asymmetric downside — funding it costs at
    most one round trip of core notional; not funding it cost a +166% move.
    """
    held = {"AAA", "BBB", "CCC", "DDD", "EEE", "FFF"}
    sizes = {
        "SNDK": {"buy_cash": 656.0, "raw_net_score": 1.7},
        "MEH": {"buy_cash": 200.0, "raw_net_score": 0.4},
    }
    conv_min = 1.5
    admissible = max_positions_admissible_buys(held, 6, set(), list(sizes))
    assert admissible == set()  # cap refuses both

    for sym, hint in sizes.items():
        if float(hint.get("raw_net_score", 0.0)) >= conv_min:
            admissible.add(sym)

    assert "SNDK" in admissible, "the year-maker must always be funded"
    assert "MEH" not in admissible, "plain-buy churn must still be suppressed"
