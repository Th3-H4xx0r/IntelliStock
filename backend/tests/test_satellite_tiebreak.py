"""The satellite must not allocate by ticker spelling.

`raw_net_score` is saturated — 3 distinct values across 506,498 trade contexts —
so `sorted(key=(-score, ticker))` degenerates into alphabetical order. Observed
in production bt 331865: the sleeve bought AAL, IDAI, IPDN, PW, which is simply
the first four candidates in the alphabet, two of them sub-$100M microcaps.

These tests pin the two repairs: an informative tiebreak, and a price floor.
"""
import os
import sys
from datetime import datetime, timedelta, timezone

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from strategies.strategy_x import StrategyX  # noqa: E402

NOW = datetime(2026, 6, 1, 20, 0, tzinfo=timezone.utc)


def _bars(closes):
    n = len(closes)
    return [{"t": (NOW - timedelta(days=(n - 1 - i))).isoformat(), "c": float(c)}
            for i, c in enumerate(closes)]


def _flat_then(gain, n=80):
    """A price path that ends `gain` above where it was 60 bars ago."""
    return [100.0] * (n - 60) + [100.0 * (1 + gain * i / 60) for i in range(1, 61)]


def test_saturated_scores_rank_by_momentum_not_alphabet():
    """THE production defect: every score identical, so the ticker decides."""
    cfg = {"satellite_max_names": 2, "satellite_min_price": 0.0}
    data = {
        "conviction_scores": {"AAL": 1.0, "IDAI": 1.0, "ZZZ": 1.0},
        # AAL and IDAI are flat; ZZZ is the only one going up.
        "AAL": {"bars": _bars(_flat_then(0.00))},
        "IDAI": {"bars": _bars(_flat_then(0.00))},
        "ZZZ": {"bars": _bars(_flat_then(0.50))},
    }
    prices = {"AAL": 15.0, "IDAI": 15.0, "ZZZ": 15.0}
    ranked = StrategyX._ranked(cfg, data, prices=prices, as_of=NOW)
    assert ranked[0] == "ZZZ", (
        f"the sleeve ranked alphabetically, not by momentum: {ranked}")


def test_a_real_score_difference_still_dominates_momentum():
    """Momentum breaks TIES. It must never override a live signal."""
    cfg = {"satellite_max_names": 2, "satellite_min_price": 0.0}
    data = {
        "conviction_scores": {"AAA": 5.0, "ZZZ": 1.0},
        "AAA": {"bars": _bars(_flat_then(0.00))},
        "ZZZ": {"bars": _bars(_flat_then(0.50))},
    }
    prices = {"AAA": 15.0, "ZZZ": 15.0}
    ranked = StrategyX._ranked(cfg, data, prices=prices, as_of=NOW)
    assert ranked[0] == "AAA", f"momentum overrode a real score: {ranked}"


def test_penny_stocks_are_excluded_by_the_price_floor():
    cfg = {"satellite_max_names": 4, "satellite_min_price": 5.0}
    data = {
        "conviction_scores": {"IPDN": 2.0, "GOOD": 1.0},
        "IPDN": {"bars": _bars(_flat_then(0.90))},
        "GOOD": {"bars": _bars(_flat_then(0.10))},
    }
    prices = {"IPDN": 1.40, "GOOD": 55.0}
    ranked = StrategyX._ranked(cfg, data, prices=prices, as_of=NOW)
    assert "IPDN" not in ranked, f"a $1.40 stock cleared a $5 floor: {ranked}"
    assert "GOOD" in ranked


def test_a_name_with_no_bars_sorts_last_but_is_not_dropped():
    """No bars means no momentum — deterministic, not a crash, not a promotion."""
    cfg = {"satellite_max_names": 3, "satellite_min_price": 0.0}
    data = {
        "conviction_scores": {"AAA": 1.0, "ZZZ": 1.0},
        "ZZZ": {"bars": _bars(_flat_then(0.30))},
    }                                    # AAA has no bars at all
    prices = {"AAA": 15.0, "ZZZ": 15.0}
    ranked = StrategyX._ranked(cfg, data, prices=prices, as_of=NOW)
    assert ranked == ["ZZZ", "AAA"], f"unranked name was not sorted last: {ranked}"


def test_the_price_floor_is_OFF_by_default_because_it_was_measured_to_cost():
    """The floor defaults to 0.0 — measured, not assumed.

    A $5 floor costs 3,205pp of compounded return over 81 windows
    (+7,140.3% -> +3,935.2%), drops beat-SPY 68% -> 65%, and flips chop from
    +0.65 back to -1.01, buying only -5.74 -> -5.46 in bear. It stays available
    as a lever; it must not be on by default.
    """
    cfg = {"satellite_max_names": 4}          # no explicit floor
    data = {
        "conviction_scores": {"IPDN": 1.0, "GOOD": 1.0},
        "IPDN": {"bars": _bars(_flat_then(0.90))},   # best momentum, $1.40
        "GOOD": {"bars": _bars(_flat_then(0.10))},
    }
    ranked = StrategyX._ranked(cfg, data, prices={"IPDN": 1.40, "GOOD": 55.0},
                               as_of=NOW)
    assert ranked[0] == "IPDN", (
        f"a low-priced name was excluded by default; the floor should be off "
        f"and momentum should rank it first: {ranked}")


def test_the_floor_still_works_when_an_operator_turns_it_on():
    """Off by default is not the same as removed."""
    cfg = {"satellite_max_names": 4, "satellite_min_price": 5.0}
    data = {
        "conviction_scores": {"IPDN": 1.0, "GOOD": 1.0},
        "IPDN": {"bars": _bars(_flat_then(0.90))},
        "GOOD": {"bars": _bars(_flat_then(0.10))},
    }
    ranked = StrategyX._ranked(cfg, data, prices={"IPDN": 1.40, "GOOD": 55.0},
                               as_of=NOW)
    assert ranked == ["GOOD"], f"an explicit floor was not honoured: {ranked}"


def test_missing_price_does_not_silently_drop_a_name_when_floor_is_off():
    cfg = {"satellite_max_names": 3, "satellite_min_price": 0.0}
    data = {"conviction_scores": {"AAA": 1.0}, "AAA": {"bars": _bars(_flat_then(0.1))}}
    assert StrategyX._ranked(cfg, data, prices={}, as_of=NOW) == ["AAA"]
