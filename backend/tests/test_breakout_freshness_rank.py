"""Rank on breakout FRESHNESS, not on accumulated trailing return.

Ranking on trailing return is structurally a late-entry machine: a name
qualifies only after it has moved, so the strongest trailing number is by
construction the most-elapsed move. Measured across 21 filled positions in three
runs, fraction-of-move-elapsed at fill vs capture is r = -0.895 (p < 0.0001) with
PERFECT separation — every position filled at <=55% elapsed made money
(+$2,093.70), every one filled at >100% lost (-$234.81).

bt 201039 made it concrete: the names that moved MOST were the ones we did WORST
on. SNDK +166.1% -> -4.4%, PLRZ +61.8% -> -17.6%, HL +29.7% -> -18.5%, against
XOM +26.8% -> +26.9% and NTR +21.6% -> +21.2%.

Freshness is a TIE-BREAK on top of the 60d rank, never a replacement: trailing
return still says which names are strong (IC +0.201 vs -0.003 for the old
max(20d,60d) key), and freshness says which of those is early. Replacing the
rank outright was measured to COST the names we already get right, because they
enter at ~0% elapsed via the news lane and need no trigger.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "strategies"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from strategies.graph_nexus_analysis import _extension_above_anchor  # noqa: E402


def _rank(candidates, hist, band, lb=20):
    """Mirror of the discovery sort with the freshness tie-break."""
    def fresh(sym):
        if band <= 0:
            return 1
        ext, _ = _extension_above_anchor(sym, hist, lb)
        return 0 if (ext is not None and 0.0 <= ext <= band) else 1
    return sorted(candidates,
                  key=lambda x: (fresh(x[0]), -(x[2] if x[2] > float("-inf") else x[1]),
                                 -x[1], x[0]))


def _bars(*closes):
    return [{"close": c} for c in closes]


# LATE: huge trailing return, but far above its base (already ran).
# FRESH: smaller trailing return, sitting just above its prior high.
HIST = {
    "LATE": _bars(100, 150, 200, 250, 300, 420),
    "FRESH": _bars(100, 104, 103, 105, 106, 108),
    "BELOW": _bars(100, 180, 190, 200, 150, 140),
}
CANDS = [("LATE", 40.0, 320.0), ("FRESH", 12.0, 60.0), ("BELOW", 5.0, 40.0)]


def test_default_off_ranks_purely_on_trailing_return():
    order = [c[0] for c in _rank(CANDS, HIST, band=0.0)]
    assert order[0] == "LATE", "unchanged until a document opts in"


def test_freshness_promotes_the_early_name_over_the_extended_one():
    order = [c[0] for c in _rank(CANDS, HIST, band=10.0)]
    assert order[0] == "FRESH"
    assert order.index("FRESH") < order.index("LATE")


def test_a_name_far_above_its_base_is_not_fresh():
    ext, _ = _extension_above_anchor("LATE", HIST, 20)
    assert ext == pytest.approx((420 - 300) / 300 * 100, abs=0.01)
    assert ext > 10.0


def test_a_name_BELOW_its_prior_high_is_not_fresh_either():
    """Freshness means just-broke-out, not merely cheap. A name that ran and
    pulled back is late, not early."""
    ext, _ = _extension_above_anchor("BELOW", HIST, 20)
    assert ext < 0
    order = [c[0] for c in _rank(CANDS, HIST, band=10.0)]
    assert order.index("BELOW") > order.index("FRESH")


def test_trailing_strength_still_orders_within_the_fresh_group():
    """Freshness says WHICH of the strong names is early; it does not replace
    the strength ranking."""
    hist = {"F1": _bars(100, 101, 102, 103, 104, 105),
            "F2": _bars(100, 101, 102, 103, 104, 105)}
    cands = [("F1", 10.0, 50.0), ("F2", 10.0, 90.0)]
    assert [c[0] for c in _rank(cands, hist, band=10.0)][0] == "F2"


def test_missing_history_is_not_treated_as_fresh():
    order = [c[0] for c in _rank(CANDS + [("NOHIST", 99.0, 99.0)], HIST, band=10.0)]
    assert order[0] == "FRESH"


def test_ordering_is_deterministic():
    hist = {"AAA": _bars(100, 101, 102), "ZZZ": _bars(100, 101, 102)}
    cands = [("ZZZ", 10.0, 50.0), ("AAA", 10.0, 50.0)]
    assert _rank(cands, hist, 10.0) == _rank(list(reversed(cands)), hist, 10.0)


def test_band_width_controls_how_far_past_the_base_still_counts():
    hist = {"X": _bars(100, 100, 100, 100, 100, 108)}
    cands = [("X", 5.0, 20.0), ("OTHER", 50.0, 300.0)]
    assert [c[0] for c in _rank(cands, hist, band=5.0)][0] == "OTHER"   # 8% > 5% band
    assert [c[0] for c in _rank(cands, hist, band=10.0)][0] == "X"      # 8% <= 10%
