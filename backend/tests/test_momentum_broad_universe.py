"""Momentum discovery must be able to see a mover it does not already own.

Six independent investigations converged on one root cause: entry lateness, and
entry lateness comes from discovery latency. Pooled over 21 filled positions in
three runs, fraction-of-move-elapsed-at-fill vs capture is r = -0.895
(p < 0.0001), with PERFECT separation — every position filled at <=55% elapsed
made money (+$2,093.70), every one filled at >100% lost (-$234.81).

`_build_momentum_scan_universe` sources: benchmark ETFs, trend ETFs, ETFs for
active trends, tickers named by an active trend, and Neo4j COMPETES_WITH /
STRATEGIC_PARTNER neighbours of stocks ALREADY TRACKED (LIMIT 30). Every one is
reachable-from-what-we-already-have. There is no broad equity screen, so a mover
can only be found if the book is already standing next to it.

bt 201039 bar 1: the pool contained no SNDK, WDC, VICR, MU or TSEM, while SNDK's
244 daily bars were fetched on the same bar and never screened. SNDK was first
scored on 2026-01-30 and bought 02-02 at $660.48 after running from $237 — it
returned -4.38% on a +166.10% stock. bt 820236's pool DID contain them on bar 1;
WDC and SNDK paid +$551.43 of that run's +$739.61. Same code, different
neighbourhood.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "strategies"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import strategies.graph_nexus_analysis as gna  # noqa: E402


class _Res:
    def __init__(self, rows): self._rows = rows
    def __iter__(self): return iter(self._rows)


class _Sess:
    def __init__(self, rows): self._rows = rows
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def run(self, q, **kw):
        if "COMPETES_WITH" in q:
            return _Res([])
        cap = int(kw.get("cap", 0) or 0)
        return _Res([{"t": t} for t in self._rows[:cap]])


class _Driver:
    def __init__(self, rows): self._rows = rows
    def session(self): return _Sess(self._rows)


BROAD = ["AAPL", "MU", "SNDK", "TSEM", "VICR", "WDC"]


def _reset():
    gna._momentum_broad_cache = {}
    gna._momentum_neighbor_cache = {}


def test_default_off_leaves_the_universe_unchanged(monkeypatch):
    _reset()
    monkeypatch.setattr(gna, "_shared_neo4j_driver", _Driver(BROAD))
    gna._MOMENTUM_SCAN_CONFIG = {}
    uni = gna._build_momentum_scan_universe([], ["SPY"])
    assert "SNDK" not in uni, "must be byte-identical until a document opts in"


def test_broad_universe_surfaces_a_name_we_do_not_own(monkeypatch):
    """The bt 201039 failure, directly."""
    _reset()
    monkeypatch.setattr(gna, "_shared_neo4j_driver", _Driver(BROAD))
    gna._MOMENTUM_SCAN_CONFIG = {"momentum_scan_broad_universe_max": 500}
    uni = gna._build_momentum_scan_universe([], ["SPY"])
    for sym in ("SNDK", "WDC", "VICR", "MU", "TSEM"):
        assert sym in uni, f"{sym} still invisible to the momentum screen"


def test_cap_is_respected(monkeypatch):
    _reset()
    monkeypatch.setattr(gna, "_shared_neo4j_driver", _Driver(BROAD))
    gna._MOMENTUM_SCAN_CONFIG = {"momentum_scan_broad_universe_max": 2}
    uni = gna._build_momentum_scan_universe([], ["SPY"])
    assert len([s for s in BROAD if s in uni]) <= 2


def test_existing_sources_still_present(monkeypatch):
    """The broad screen ADDS; it must not replace the news/graph lanes."""
    _reset()
    monkeypatch.setattr(gna, "_shared_neo4j_driver", _Driver(BROAD))
    gna._MOMENTUM_SCAN_CONFIG = {"momentum_scan_broad_universe_max": 500}
    uni = gna._build_momentum_scan_universe(
        [{"name": "oil", "affected_tickers": ["XOM"]}], ["SPY"])
    assert "XOM" in uni
    assert any(t in uni for t in gna._SECTOR_COMMODITY_BENCHMARKS.values())


def test_result_is_sorted_and_deduped(monkeypatch):
    _reset()
    monkeypatch.setattr(gna, "_shared_neo4j_driver", _Driver(BROAD + BROAD))
    gna._MOMENTUM_SCAN_CONFIG = {"momentum_scan_broad_universe_max": 500}
    uni = gna._build_momentum_scan_universe([], ["SPY"])
    assert uni == sorted(uni) and len(uni) == len(set(uni))


def test_no_driver_is_safe(monkeypatch):
    _reset()
    monkeypatch.setattr(gna, "_shared_neo4j_driver", None)
    gna._MOMENTUM_SCAN_CONFIG = {"momentum_scan_broad_universe_max": 500}
    assert isinstance(gna._build_momentum_scan_universe([], ["SPY"]), list)


def test_query_failure_falls_back_to_the_old_universe(monkeypatch):
    class _Bad:
        def session(self): raise RuntimeError("neo4j down")
    _reset()
    monkeypatch.setattr(gna, "_shared_neo4j_driver", _Bad())
    gna._MOMENTUM_SCAN_CONFIG = {"momentum_scan_broad_universe_max": 500}
    uni = gna._build_momentum_scan_universe([], ["SPY"])
    assert isinstance(uni, list) and "SNDK" not in uni


def test_malformed_cap_is_off(monkeypatch):
    _reset()
    monkeypatch.setattr(gna, "_shared_neo4j_driver", _Driver(BROAD))
    for bad in ("x", None, -5):
        gna._momentum_broad_cache = {}
        gna._MOMENTUM_SCAN_CONFIG = {"momentum_scan_broad_universe_max": bad}
        assert "SNDK" not in gna._build_momentum_scan_universe([], ["SPY"])
