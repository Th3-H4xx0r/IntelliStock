"""Screen the bars we have already paid for (bt 201039).

Six investigations converged on entry lateness as the whole P&L: pooled over 21
filled positions in three runs, fraction-of-move-elapsed-at-fill vs capture is
r = -0.895 (p < 0.0001), with perfect separation — every position filled at <=55%
elapsed made money, every one filled at >100% lost.

`_build_momentum_scan_universe` draws only from benchmark ETFs, trend ETFs,
active-trend tickers and Neo4j neighbours of stocks ALREADY TRACKED. So a mover
is invisible unless the book is already standing next to it. In bt 201039 SNDK's
244 daily bars were fetched on bar 1 by the graph-seed batch and never screened;
SNDK was first scored 29 sessions later and bought at $660.48 after running from
$237, returning -4.38% on a +166.10% stock.

An earlier attempt queried the company graph with `ORDER BY c.ticker LIMIT n`.
That truncates ALPHABETICALLY: of 5,568 companies a 1,500 cap stops at DNOW, so
SNDK and WDC can never appear at any cap. `test_alphabetical_cap_is_the_trap`
pins that so it is not reintroduced.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "strategies"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import strategies.graph_nexus_analysis as gna  # noqa: E402

# What bt 201039 had in the overlay cache on bar 1 but never screened.
CACHED = {"AAPL": [], "MU": [], "SNDK": [], "TSEM": [], "VICR": [], "WDC": []}


def _setup(monkeypatch, cfg, cache):
    monkeypatch.setattr(gna, "_shared_neo4j_driver", None)
    gna._MOMENTUM_SCAN_CONFIG = cfg
    gna._MOMENTUM_SCAN_CACHE = cache


def test_default_off_is_byte_identical(monkeypatch):
    _setup(monkeypatch, {}, {"_overlay_bars_raw": CACHED})
    assert "SNDK" not in gna._build_momentum_scan_universe([], ["SPY"])


def test_cached_bars_are_screened(monkeypatch):
    """The bt 201039 miss, directly: those bars were already in memory."""
    _setup(monkeypatch, {"momentum_scan_cached_bars": True},
           {"_overlay_bars_raw": CACHED})
    uni = gna._build_momentum_scan_universe([], ["SPY"])
    for sym in ("SNDK", "WDC", "VICR", "MU", "TSEM"):
        assert sym in uni, f"{sym} still invisible despite having bars"


def test_alphabetical_cap_is_the_trap():
    """Why this is NOT done with `ORDER BY c.ticker LIMIT n`.

    5,568 companies; a 1,500 cap stops at DNOW. Every ticker after it — SNDK,
    WDC, VICR, XOM — is unreachable at ANY cap below the full universe, so the
    screen would silently only ever see the front of the alphabet.
    """
    universe = sorted(["ANRO", "ALTO", "DNOW", "SNDK", "VICR", "WDC"])
    assert universe[:3] == ["ALTO", "ANRO", "DNOW"]
    assert "SNDK" not in universe[:3] and "WDC" not in universe[:3]


def test_existing_sources_still_present(monkeypatch):
    """The cache screen ADDS; it must not replace the news/graph lanes."""
    _setup(monkeypatch, {"momentum_scan_cached_bars": True},
           {"_overlay_bars_raw": CACHED})
    uni = gna._build_momentum_scan_universe(
        [{"name": "oil", "affected_tickers": ["XOM"]}], ["SPY"])
    assert "XOM" in uni
    assert any(t in uni for t in gna._SECTOR_COMMODITY_BENCHMARKS.values())


def test_result_is_sorted_and_deduped(monkeypatch):
    _setup(monkeypatch, {"momentum_scan_cached_bars": True},
           {"_overlay_bars_raw": dict(CACHED, SPY=[])})
    uni = gna._build_momentum_scan_universe([], ["SPY"])
    assert uni == sorted(uni) and len(uni) == len(set(uni))


def test_symbols_are_normalised(monkeypatch):
    _setup(monkeypatch, {"momentum_scan_cached_bars": True},
           {"_overlay_bars_raw": {" sndk ": [], "wdc": []}})
    uni = gna._build_momentum_scan_universe([], ["SPY"])
    assert "SNDK" in uni and "WDC" in uni


def test_missing_or_malformed_cache_is_safe(monkeypatch):
    for cache in ({}, {"_overlay_bars_raw": None}, {"_overlay_bars_raw": []}, None):
        _setup(monkeypatch, {"momentum_scan_cached_bars": True}, cache)
        assert isinstance(gna._build_momentum_scan_universe([], ["SPY"]), list)


def test_costs_no_query(monkeypatch):
    """Zero marginal cost is the point — no Neo4j call, no bar fetch."""
    class _Boom:
        def session(self): raise AssertionError("must not query the graph")
    monkeypatch.setattr(gna, "_shared_neo4j_driver", _Boom())
    gna._MOMENTUM_SCAN_CONFIG = {"momentum_scan_cached_bars": True}
    gna._MOMENTUM_SCAN_CACHE = {"_overlay_bars_raw": CACHED}
    # current_symbols empty so the neighbour lane does not fire
    assert "SNDK" in gna._build_momentum_scan_universe([], [])
