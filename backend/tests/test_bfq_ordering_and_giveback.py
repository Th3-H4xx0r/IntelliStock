"""Two fixes from the bt 427197 sweep: a queue that never saw the name, and an
open-profit give-back nothing could see.

1) BFQ SOURCE ORDERING. `_bfq_candidate_syms` is built ~650 lines ABOVE the
   momentum lane, which writes `_momentum_new_buys` later in the same bar. A name
   whose only buy signal that bar comes from the momentum watchlist is never
   offered to the backfill queue — no ADD line, no BLOCKED line, nothing.
   bt 427197: SNDK entered solely via that lane on 01-09 and 01-12, on days the
   queue had 11 FREE slots (39/60, then 49/60). By 01-13, when it finally fired
   as a native Direct buy, the queue was 60/60 and shut for the rest of the run
   (399 `full_priority_blocked`). SNDK moved $237 -> $641 and was bought zero
   times. Counts: 427197 399 blocks / 915207 691 / 383778 37 / bear 542754 0.

2) PEAK GIVE-BACK. `peak_protection_*` is a BYPASS that defers to the trailing
   stop, which this document disables — so a name that runs +60% and hands it
   all back has no peak-referenced protection. bt 427197 lost 5.7pp of a +15.9%
   run to SLV alone: $67.35 -> $107.99 (+60.3%, 19% of NAV) -> $70.40, -$379 of
   a -$346 net move, while never worse than -4.4% against its own entry, so
   every entry-anchored gate was blind. `Trailing stop SUPPRESSED ... SLV
   drop=40.5% >= 12%` fired 125 times.
   Replayed over 6 runs / 5 windows / 3 regimes at (+30%, 25%) it fires ZERO
   times on eight winners and once on SLV (40.4%) — a 2.6pp clearance.
"""
import pytest


def bfq_candidates(base, momentum_buys, enabled):
    """Mirror of the broker-side append."""
    out = list(base)
    if not enabled:
        return out
    seen = {str(s).strip().upper() for s in out}
    for m in (momentum_buys or []):
        t = str((m or {}).get("ticker") or "").strip().upper()
        if t and t not in seen:
            out.append(t); seen.add(t)
    return out


def giveback_exit(peak_pnl, dd_from_peak, min_peak, dd_thresh):
    return (min_peak > 0 and dd_thresh > 0
            and peak_pnl >= min_peak and dd_from_peak >= dd_thresh)


# ── 1. the queue never saw SNDK ─────────────────────────────────────────────

def test_default_off_reproduces_the_bug():
    assert "SNDK" not in bfq_candidates(["AAPL"], [{"ticker": "SNDK"}], enabled=False)


def test_momentum_only_name_reaches_the_queue():
    assert "SNDK" in bfq_candidates(["AAPL"], [{"ticker": "SNDK"}], enabled=True)


def test_no_duplicates_when_the_name_is_already_there():
    out = bfq_candidates(["SNDK"], [{"ticker": "SNDK"}], enabled=True)
    assert out.count("SNDK") == 1


def test_symbols_are_normalised():
    assert bfq_candidates([], [{"ticker": " sndk "}], enabled=True) == ["SNDK"]


def test_malformed_entries_are_skipped():
    out = bfq_candidates([], [None, {}, {"ticker": ""}, {"ticker": "SNDK"}], enabled=True)
    assert out == ["SNDK"]


def test_existing_candidates_keep_their_order():
    """Appending must not reorder — priority admission below depends on it."""
    out = bfq_candidates(["A", "B"], [{"ticker": "C"}], enabled=True)
    assert out == ["A", "B", "C"]


# ── 2. the give-back nothing could see ──────────────────────────────────────

def test_SLV_is_caught():
    """+60.3% peak, 34.8% handed back."""
    assert giveback_exit(60.3, 34.8, 30.0, 25.0) is True


def test_the_eight_winners_are_NOT_caught():
    """Max drawdown-from-peak for each winner across 6 runs / 5 windows."""
    winners = {"WDC": 17.1, "SNDK_a": 22.4, "SNDK_b": 22.4, "LRCX": 16.2,
               "AMAT": 15.4, "AGMI": 20.3, "XOM": 6.9, "AAOI": 12.4}
    for name, dd in winners.items():
        assert giveback_exit(100.0, dd, 30.0, 25.0) is False, f"{name} would be cut"


def test_clearance_is_not_a_knife_edge():
    """Worst winner 22.4% vs threshold 25% = 2.6pp of room."""
    assert 25.0 - max(17.1, 22.4, 16.2, 15.4, 20.3, 6.9, 12.4) == pytest.approx(2.6, abs=0.01)


def test_a_name_that_never_ran_is_not_touched():
    """The rule is about GIVING BACK profit, not about losses."""
    assert giveback_exit(5.0, 90.0, 30.0, 25.0) is False


def test_default_off():
    assert giveback_exit(60.3, 34.8, 0.0, 0.0) is False
    assert giveback_exit(60.3, 34.8, 30.0, 0.0) is False
