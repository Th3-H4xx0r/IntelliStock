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


def test_an_unpriced_candidate_never_takes_a_sleeve_slot():
    """THE production blocker, found in bt 186584.

    Nexus discovers a name and publishes a conviction score for it in the SAME
    bar, but the broker's price map is built BEFORE run_once and never expanded
    with those discoveries — `nexus_discovered_syms` is collected after the fact
    and only gates execution. So the top-ranked candidates had no price, all
    four sleeve slots went to names that could not be bought, and the sleeve
    emitted 3 symbols a bar (core + 2 commodity) with 20% of NAV idle.

    A candidate with no price is not investable this bar. It must not consume a
    slot that a priced name could use.
    """
    cfg = {"satellite_max_names": 2, "satellite_min_price": 0.0}
    data = {
        "conviction_scores": {"APD": 1.0, "BBSI": 1.0, "REAL": 1.0},
        "APD": {"bars": _bars(_flat_then(0.9))},
        "BBSI": {"bars": _bars(_flat_then(0.8))},
        "REAL": {"bars": _bars(_flat_then(0.1))},
    }
    # APD and BBSI rank higher but are unpriced; only REAL can actually be bought.
    ranked = StrategyX._ranked(cfg, data, prices={"REAL": 40.0}, as_of=NOW)
    assert ranked == ["REAL"], (
        f"unpriced candidates consumed the sleeve's slots: {ranked}")


def test_a_discovered_name_with_bars_but_no_quote_is_still_tradable():
    """The complete repair for bt 186584.

    Dropping unpriced candidates stops them wasting slots, but on its own it
    just empties the sleeve — the broker's price map holds the 11 watchlist
    tickers, and every graph-discovered candidate is missing from it. Nexus DOES
    load bars for those names ("loaded 522 1Day bars for IDAI"), and they land in
    the shared `data` map, so a point-in-time last close is available and is the
    same number the quote would carry. Deriving it makes the name rankable and
    sizable; without it the 20% sleeve can never fill in production.
    """
    from datetime import timedelta
    from strategies.strategy_x import StrategyX as SX

    now = NOW
    cfg = {"strategy_x_enabled": True, "satellite_pct": 0.2,
           "satellite_max_names": 2, "core_weight": 0.8,
           "core_filter_symbol": "QQQ", "satellite_min_price": 0.0}

    def qqq_bars(n):
        return [{"t": (now - timedelta(days=(n - 1 - i))).isoformat(),
                 "c": 100.0 + i * 0.5} for i in range(n)]

    class Emu:
        def get_cash(self):
            return 10000.0

        def get_positions(self):
            return {}

        def get_portfolio_value(self, prices=None):
            return 10000.0

    data = {
        "QQQ": {"bars": qqq_bars(260)},
        # DISC has bars (Nexus loaded them) but is absent from `prices`.
        "DISC": {"bars": _bars(_flat_then(0.40))},
        "conviction_scores": {"DISC": 1.0},
    }
    out = SX().run_once(["TQQQ"], {"TQQQ": 50.0, "SPY": 500.0, "QQQ": 400.0},
                        now, cfg, {}, data=data, portfolio_emulator=Emu(),
                        strategy_cache={})
    assert out.get("DISC") == 1, (
        f"a discovered name with bars was never bought: "
        f"{ {k: v for k, v in out.items() if not k.startswith('_')} }")
    assert out["_nexus_position_sizes"]["DISC"]["buy_cash"] > 0


def test_an_empty_price_map_yields_no_candidates_rather_than_unbuyable_ones():
    """The degenerate case must be empty, not a book of names that cannot fill."""
    cfg = {"satellite_max_names": 3, "satellite_min_price": 0.0}
    data = {"conviction_scores": {"AAA": 1.0},
            "AAA": {"bars": _bars(_flat_then(0.1))}}
    assert StrategyX._ranked(cfg, data, prices={}, as_of=NOW) == []
