"""2026-07-23: _open_run_entry_map — the entry of the CURRENT open holding run,
tracked by running net qty so a PARTIAL profit-take trim does NOT reset it.

Bug it fixes: the legacy full-cycle entry map used "earliest buy after the most
recent sell", so after tier-1 profit-take sold part of CAR, CAR's original buy
was "before the last sell" -> entry_buy=None -> entry_key="" -> the tiered
profit-take (which requires a truthy entry key) silently STOPPED after tier 1,
and held_days reset. Verified: CAR fired only the +20% tier in bt#288005.
"""
import os
import sys

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

import strategies.graph_nexus_analysis as g


def _t(ticker, action, shares, ts):
    return {"ticker": ticker, "action": action, "shares": shares, "timestamp": ts}


def test_single_buy_entry_is_the_buy():
    h = [_t("CAR", "buy", 2.68, "2026-04-13")]
    assert g._open_run_entry_map(h) == {"CAR": "2026-04-13"}


def test_partial_sell_keeps_original_entry():
    # THE FIX: 40% trim leaves the position open -> entry stays the 4/13 buy.
    h = [
        _t("CAR", "buy", 2.68, "2026-04-13"),
        _t("CAR", "sell", 1.07, "2026-04-16"),   # partial (60% still held)
    ]
    assert g._open_run_entry_map(h) == {"CAR": "2026-04-13"}


def test_two_partial_sells_keep_original_entry():
    h = [
        _t("CAR", "buy", 2.68, "2026-04-13"),
        _t("CAR", "sell", 1.07, "2026-04-16"),
        _t("CAR", "sell", 0.80, "2026-04-17"),
    ]
    assert g._open_run_entry_map(h) == {"CAR": "2026-04-13"}


def test_full_close_then_rebuy_uses_rebuy_entry():
    # Legacy intent preserved: sell-all then rebuy -> entry is the rebuy.
    h = [
        _t("AAPL", "buy", 10.0, "2026-01-01"),
        _t("AAPL", "sell", 10.0, "2026-01-20"),   # full close
        _t("AAPL", "buy", 10.0, "2026-03-01"),    # re-open
    ]
    assert g._open_run_entry_map(h) == {"AAPL": "2026-03-01"}


def test_full_close_has_no_entry():
    h = [
        _t("X", "buy", 5.0, "2026-01-01"),
        _t("X", "sell", 5.0, "2026-01-05"),
    ]
    assert g._open_run_entry_map(h) == {}


def test_partial_then_full_then_rebuy():
    h = [
        _t("CAR", "buy", 2.68, "2026-04-13"),
        _t("CAR", "sell", 1.07, "2026-04-16"),    # partial -> entry stays 4/13
        _t("CAR", "sell", 1.61, "2026-04-23"),    # full close -> entry cleared
        _t("CAR", "buy", 1.0, "2026-04-25"),      # re-open
    ]
    assert g._open_run_entry_map(h) == {"CAR": "2026-04-25"}


def test_multiple_tickers_independent():
    h = [
        _t("CAR", "buy", 2.68, "2026-04-13"),
        _t("AAOI", "buy", 6.0, "2026-04-03"),
        _t("CAR", "sell", 1.07, "2026-04-16"),   # CAR partial
        _t("AAOI", "sell", 6.0, "2026-04-24"),   # AAOI full close
    ]
    assert g._open_run_entry_map(h) == {"CAR": "2026-04-13"}


def test_empty_and_malformed_are_safe():
    assert g._open_run_entry_map([]) == {}
    assert g._open_run_entry_map(None) == {}
    assert g._open_run_entry_map([{"action": "buy"}]) == {}  # no ticker -> skipped
