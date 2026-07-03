"""Task 10 — GraphNexusTradeOutcomes rows must track forward returns.

Root cause (verified READ-ONLY against prod instance
alpaca-main|e3fdb8e1c5f7045112e44b26, backtest 586767):

1. Frozen outcomes (853/877 rows had latest_observation_date == entry_date and
   latest_return == 0): `_update_indefinite_outcomes` resolved the current
   price SOLELY via `prices.get(symbol)`. `prices` is the held-only broker
   positions dict, so any outcome symbol not currently held had no entry and
   hit `continue` — the row never advanced. Only ~24 symbols still held on a
   later bar advanced. Fix: fall back to the overlay-bars cache (same source
   `_fill_outcome_prices` already uses) so non-held symbols resolve a price.

2. action_intent 'unknown' (870/877 rows): `_infer_action_intent` returns
   "add_buy"/"initial_buy"/"sell_override", none of which were in
   `_VALID_ACTION_INTENTS`, so `_normalize_action_intent` coerced them to
   "unknown" at persist time. `_update_indefinite_outcomes` even signs sell
   returns off `action_intent == "sell_override"`, so the coercion also
   dropped the sell sign. Fix: add those three intents to the valid set.

Tests target the extracted pure helpers so no DB is touched.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from strategies.graph_nexus_analysis import (
    _outcome_price_now,
    _compute_outcome_progress,
    _normalize_action_intent,
)

_DATE = "2026-06-05"
_IID = "alpaca-main|e3fdb8e1"


def _doc(**over):
    d = {
        "id": f"{_IID}|2026-06-02|AAPL",
        "symbol": "AAPL",
        "entry_date": "2026-06-02",
        "entry_price": 100.0,
        "action_intent": "initial_buy",
    }
    d.update(over)
    return d


# ── price resolution ─────────────────────────────────────────────────────
def test_prices_dict_wins():
    assert _outcome_price_now("AAPL", {"AAPL": 110.0}, None, _DATE) == 110.0


def test_overlay_bars_fallback_when_not_held():
    # The held-only prices dict lacks MSFT (root cause of frozen rows); the
    # overlay-bars cache still has it -> row can advance.
    bars = {"MSFT": [{"t": "2026-06-04", "c": 100.0}, {"t": "2026-06-05", "c": 130.0}]}
    assert _outcome_price_now("MSFT", {}, bars, _DATE) == 130.0


def test_overlay_bars_respects_asof_date():
    # A bar dated AFTER date_key must never be used (no forward-looking).
    bars = {"MSFT": [{"t": "2026-06-05", "c": 130.0}, {"t": "2026-06-06", "c": 999.0}]}
    assert _outcome_price_now("MSFT", {}, bars, _DATE) == 130.0


def test_missing_price_returns_none():
    assert _outcome_price_now("NADA", {}, {}, _DATE) is None
    assert _outcome_price_now("NADA", {"NADA": 0}, None, _DATE) is None


# ── progress computation ─────────────────────────────────────────────────
def test_progress_advances_return_and_date():
    update, series = _compute_outcome_progress(_doc(), "AAPL", 110.0, _DATE, _IID)
    assert update["latest_return"] == 10.0
    assert update["latest_observation_date"] == _DATE
    assert update["max_return_so_far"] == 10.0
    assert update["min_return_so_far"] == 10.0
    assert series["days_since_entry"] == 3
    assert series["observation_date"] == _DATE


def test_progress_missing_price_untouched():
    # No price / non-positive entry -> None means the row is left untouched
    # (no regression back to zeros).
    assert _compute_outcome_progress(_doc(), "AAPL", None, _DATE, _IID) is None
    assert _compute_outcome_progress(_doc(entry_price=0), "AAPL", 110.0, _DATE, _IID) is None


def test_sell_override_flips_sign():
    update, _ = _compute_outcome_progress(
        _doc(action_intent="sell_override"), "AAPL", 110.0, _DATE, _IID
    )
    # price rose 10% but it was a sell -> signed return is negative
    assert update["latest_return"] == -10.0
    assert update["latest_price_return"] == 10.0


def test_max_min_carry_existing_extremes():
    update, _ = _compute_outcome_progress(
        _doc(max_return_so_far=25.0, min_return_so_far=-8.0), "AAPL", 110.0, _DATE, _IID
    )
    assert update["max_return_so_far"] == 25.0   # prior peak retained
    assert update["min_return_so_far"] == -8.0   # prior trough retained
    assert update["latest_return"] == 10.0


# ── action_intent persistence ────────────────────────────────────────────
def test_infer_intents_are_now_valid():
    for intent in ("add_buy", "initial_buy", "sell_override"):
        assert _normalize_action_intent(intent) == intent


def test_unknown_still_coerced():
    assert _normalize_action_intent("bogus_typo") == "unknown"
    assert _normalize_action_intent(None) == "hold"


# ── integration: the held-only regression is actually fixed ──────────────
def test_non_held_symbol_advances_end_to_end():
    bars = {"MSFT": [{"t": "2026-06-05", "c": 120.0}]}
    doc = _doc(symbol="MSFT", entry_price=100.0,
               id=f"{_IID}|2026-06-02|MSFT", action_intent="initial_buy")
    px = _outcome_price_now("MSFT", {}, bars, _DATE)  # not in held prices
    assert px == 120.0
    progress = _compute_outcome_progress(doc, "MSFT", px, _DATE, _IID)
    assert progress is not None
    update, _ = progress
    assert update["latest_return"] == 20.0
    assert update["latest_observation_date"] == _DATE
