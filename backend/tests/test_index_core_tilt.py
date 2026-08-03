"""Tests for IntelliStock v2 — index core with a bounded conviction tilt.

Pins the properties the design depends on: default-off, the buy/hold spread
(stricter to enter than to exit), equal weighting, the core-as-residual rule,
and — most importantly — that an ALL-ZERO conviction ranking degrades to
holding the index rather than inventing an ordering. That last one is the
failure this whole module was written in response to: the graph produced
raw_score=0.000 in 677/677 evaluations and nothing noticed.
"""
import os
import sys
from datetime import datetime, timedelta, timezone

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)
_strats = os.path.join(_backend, "strategies")
if _strats not in sys.path:
    sys.path.insert(0, _strats)

from index_core_tilt import (  # noqa: E402
    IndexCoreTilt,
    plan_targets,
)

NOW = datetime(2026, 8, 3, 16, 0, tzinfo=timezone.utc)
BASE = dict(nav=6000.0, core_symbol="SPY", core_target_pct=0.85,
            core_band_pct=0.05, days_since_rebalance=999,
            rebalance_min_days=90, satellite_max_names=6, exit_rank=12)


class _Emu:
    def __init__(self, positions=None, nav=6000.0):
        self._p = positions or {}
        self._nav = nav

    def get_portfolio_value(self, prices=None):
        return self._nav

    def get_positions(self):
        return dict(self._p)


# ── default-off ───────────────────────────────────────────────────────────

def test_disabled_by_default_emits_nothing():
    out = IndexCoreTilt().run_once(
        ["AAPL"], {"SPY": 600.0, "AAPL": 100.0}, NOW, {}, {},
        portfolio_emulator=_Emu())
    assert out == {}


def test_explicitly_disabled_emits_nothing():
    out = IndexCoreTilt().run_once(
        ["AAPL"], {"SPY": 600.0}, NOW, {"enabled": False}, {},
        portfolio_emulator=_Emu())
    assert out == {}


# ── the graph-inert case, which is the whole reason this exists ───────────

def test_an_all_zero_ranking_holds_the_INDEX_and_invents_nothing():
    """raw_score=0.000 for every name must mean "buy the index", never
    "pick some names anyway"."""
    targets, notes = plan_targets(
        core_value=0.0, ranked_symbols=[], held_symbols=[], **BASE)
    assert targets == {"SPY": 1.0}, targets
    assert any("no satellite" in n for n in notes)


def test_a_dead_signal_does_not_produce_an_arbitrary_ordering():
    strat = IndexCoreTilt()
    ranked = strat._ranked_symbols(
        {"satellite_min_score": 0.0}, ["AAPL", "MSFT", "NVDA"],
        {"conviction_scores": {"AAPL": 0.0, "MSFT": 0.0, "NVDA": 0.0}},
        {}, "SPY")
    assert ranked == [], ranked


def test_missing_conviction_data_is_empty_not_alphabetical():
    strat = IndexCoreTilt()
    assert strat._ranked_symbols({}, ["AAPL", "MSFT"], None, {}, "SPY") == []
    assert strat._ranked_symbols({}, ["AAPL"], {"other": 1}, {}, "SPY") == []


# ── the buy/hold spread (Novy-Marx & Velikov sS rule) ─────────────────────

def test_a_held_name_survives_outside_the_entry_cut():
    """Entry needs top-6; a HELD name is kept until it leaves top-12. That gap
    is the single most effective turnover mitigation in the taxonomy."""
    ranked = [f"S{i}" for i in range(20)]
    targets, _ = plan_targets(
        core_value=900.0, ranked_symbols=ranked,
        held_symbols=["S8"],           # rank 8: outside entry, inside exit
        **BASE)
    assert "S8" in targets, "a held name inside exit_rank must be retained"


def test_a_held_name_outside_the_exit_rank_is_sold():
    ranked = [f"S{i}" for i in range(20)]
    targets, notes = plan_targets(
        core_value=900.0, ranked_symbols=ranked,
        held_symbols=["S15"],          # rank 15: outside exit_rank=12
        **BASE)
    assert "S15" not in targets
    assert any("exit S15" in n for n in notes)


def test_a_name_that_vanishes_from_the_ranking_is_sold():
    targets, notes = plan_targets(
        core_value=900.0, ranked_symbols=["A", "B"], held_symbols=["GONE"],
        **BASE)
    assert "GONE" not in targets
    assert any("GONE" in n for n in notes)


# ── sizing ────────────────────────────────────────────────────────────────

def test_satellite_is_equal_weighted_not_score_weighted():
    """With no demonstrated selection skill, score-proportional weighting just
    concentrates capital into the least reliable estimates."""
    targets, _ = plan_targets(
        core_value=900.0, ranked_symbols=["A", "B", "C"], held_symbols=[],
        **BASE)
    sats = {k: v for k, v in targets.items() if k != "SPY"}
    assert len(sats) == 3
    assert len(set(round(v, 10) for v in sats.values())) == 1, sats


def test_core_is_the_residual_so_a_buy_is_an_overweight_not_a_new_long():
    targets, _ = plan_targets(
        core_value=900.0, ranked_symbols=["A", "B", "C"], held_symbols=[],
        **BASE)
    assert abs(sum(targets.values()) - 1.0) < 1e-9, targets
    assert abs(targets["SPY"] - 0.85) < 1e-9


def test_satellite_is_capped_at_max_names():
    ranked = [f"S{i}" for i in range(30)]
    targets, _ = plan_targets(
        core_value=900.0, ranked_symbols=ranked, held_symbols=[], **BASE)
    assert len([k for k in targets if k != "SPY"]) == BASE["satellite_max_names"]


def test_zero_nav_is_a_no_op():
    targets, notes = plan_targets(
        **{**BASE, "nav": 0.0}, core_value=0.0, ranked_symbols=["A"],
        held_symbols=[])
    assert targets == {} and notes == ["no_nav"]


# ── the two turnover gates ────────────────────────────────────────────────

def test_inside_the_band_with_no_membership_change_does_nothing():
    # With an EMPTY satellite the core target is 1.0, not core_target_pct — the
    # core absorbs the unused sleeve, because holding cash on no opinion is
    # exactly the drag this design exists to remove. So "inside the band" here
    # means near 100%, and 0.97 is 3pp inside the 5pp band.
    targets, notes = plan_targets(
        core_value=0.97 * 6000.0, ranked_symbols=[], held_symbols=[], **BASE)
    assert targets == {}, targets
    assert any("within band" in n for n in notes)


def test_an_empty_satellite_sends_the_core_to_fully_invested():
    """The complement of the test above: 86% core with no satellite is a 14pp
    drift and MUST rebalance, rather than parking 14% in cash."""
    targets, _ = plan_targets(
        core_value=0.86 * 6000.0, ranked_symbols=[], held_symbols=[], **BASE)
    assert targets == {"SPY": 1.0}, targets


def test_cadence_holds_a_drifted_core_when_membership_is_unchanged():
    kw = {**BASE, "days_since_rebalance": 10}
    targets, notes = plan_targets(
        core_value=0.60 * 6000.0, ranked_symbols=[], held_symbols=[], **kw)
    assert targets == {}
    assert any("cadence holds" in n for n in notes)


def test_a_membership_change_overrides_the_cadence():
    """A name leaving the portfolio must not wait 90 days to be sold — the
    cadence throttles REBALANCING, not exits."""
    kw = {**BASE, "days_since_rebalance": 1}
    targets, notes = plan_targets(
        core_value=0.85 * 6000.0, ranked_symbols=["A", "B"],
        held_symbols=["GONE"], **kw)
    assert "SPY" in targets, notes


# ── end to end ────────────────────────────────────────────────────────────

def test_end_to_end_emits_a_buy_for_the_core_on_an_empty_book():
    out = IndexCoreTilt().run_once(
        [], {"SPY": 600.0}, NOW,
        {"enabled": True, "core_target_pct": 0.85}, {},
        portfolio_emulator=_Emu())
    assert out.get("SPY") == 1, out


def test_end_to_end_returns_only_ints_in_the_run_once_contract():
    out = IndexCoreTilt().run_once(
        ["A"], {"SPY": 600.0, "A": 50.0}, NOW,
        {"enabled": True}, {},
        data={"conviction_scores": {"A": 0.9}},
        portfolio_emulator=_Emu(positions={"SPY": 10.0}))
    assert all(v in (1, 0, -1) for v in out.values()), out


def test_no_core_price_is_a_no_op_not_a_guess():
    out = IndexCoreTilt().run_once(
        ["A"], {"A": 50.0}, NOW, {"enabled": True}, {},
        portfolio_emulator=_Emu(positions={"SPY": 10.0}))
    assert out == {}
