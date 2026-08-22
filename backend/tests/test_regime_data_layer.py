"""Overlay-bars cache hardening (2026-07-19 regime-safety spec, Phase 1b).

The shared per-symbol GraphNexusOverlayBarsCache must never serve rows that
blind the regime detector: empty rows are misses, and rows that do not cover
the requested range START are refetched (the old code only checked the end).
"""
import os
import sys
import types
from datetime import datetime, timedelta

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

import strategies.graph_nexus_analysis as g


class _FakeStore:
    """The db.store surface `_overlay_bars_cache_{get,set}` uses, over a dict."""

    def __init__(self, rows):
        self._rows = rows

    def get(self, _table, key):
        return self._rows.get(key)

    def insert(self, _table, doc, *, conflict=None, durability="hard"):
        self._rows[doc["id"]] = doc
        return {"inserted": 1}

    def table_list(self):
        return [g.NEXUS_OVERLAY_BARS_TABLE]


def _bars(start: str, n: int, close: float = 100.0):
    base = datetime.strptime(start, "%Y-%m-%d")
    return [
        {"t": (base + timedelta(days=i)).strftime("%Y-%m-%dT05:00:00Z"), "c": close}
        for i in range(n)
    ]


def _with_fake_rdb(monkeypatch, store):
    monkeypatch.setattr(g, "store", _FakeStore(store))
    monkeypatch.setattr(g, "_overlay_bars_table_ensured", True)


def test_cache_set_skips_empty_bars(monkeypatch):
    store = {}
    _with_fake_rdb(monkeypatch, store)
    g._overlay_bars_cache_set(object(), "SPY", [])
    assert store == {}, "empty bars must never be persisted"


def test_cache_set_stores_coverage(monkeypatch):
    store = {}
    _with_fake_rdb(monkeypatch, store)
    bars = _bars("2025-12-14", 30)
    g._overlay_bars_cache_set(object(), "SPY", bars,
                              fetch_start="2025-12-14", fetch_end="2026-07-19")
    doc = store["SPY"]
    assert doc["fetch_start"] == "2025-12-14"
    assert doc["fetch_end"] == "2026-07-19"
    assert len(doc["bars"]) == 30


def test_cache_get_returns_doc(monkeypatch):
    store = {"SPY": {"id": "SPY", "bars": _bars("2025-12-14", 5), "adjustment": "split",
                     "fetch_start": "2025-12-14", "fetch_end": "2026-01-01"}}
    _with_fake_rdb(monkeypatch, store)
    doc = g._overlay_bars_cache_get(object(), "SPY")
    assert isinstance(doc, dict) and len(doc["bars"]) == 5
    assert g._overlay_bars_cache_get(object(), "QQQ") is None


def _run_ensure(monkeypatch, store, cache, symbols, fetch_range,
                fetched_bars=None):
    """Drive _ensure_overlay_bars_cached with fakes; return fetch batches."""
    _with_fake_rdb(monkeypatch, store)
    monkeypatch.setattr(g, "_get_nexus_db_conn", lambda: object())
    batches = []

    def _fake_fetch(batch, start, end, key=None, secret=None, timeframe=None):
        batches.append(list(batch))
        return {s: (fetched_bars or _bars("2025-12-14", 60)) for s in batch}

    fake_broker = types.SimpleNamespace(fetch_alpaca_historical_bars=_fake_fetch)
    monkeypatch.setitem(sys.modules, "broker", fake_broker)
    cache["_overlay_bars_range"] = fetch_range
    g._ensure_overlay_bars_cached(symbols, "key", "secret", cache,
                                  {}, fetch_range[0])
    return batches


def test_ensure_treats_empty_row_as_miss(monkeypatch):
    store = {"SPY": {"id": "SPY", "bars": [], "adjustment": "split"}}  # poisoned row (old writer)
    cache = {}
    batches = _run_ensure(monkeypatch, store, cache, ["SPY"],
                          ("2025-12-14", "2026-07-19"))
    assert batches and "SPY" in batches[0], "empty row must trigger refetch"
    assert cache["_overlay_bars_raw"]["SPY"], "cache must hold refetched bars"


def test_ensure_refetches_when_row_starts_too_late(monkeypatch):
    # Row covers 2026-04-30.. but the run needs 2025-12-14.. → refetch.
    store = {"QQQ": {"id": "QQQ", "bars": _bars("2026-04-30", 53), "adjustment": "split",
                     "fetch_start": "2026-04-30", "fetch_end": "2026-07-19"}}
    cache = {}
    batches = _run_ensure(monkeypatch, store, cache, ["QQQ"],
                          ("2025-12-14", "2026-07-19"))
    assert batches and "QQQ" in batches[0], "late-start row must refetch"


def test_ensure_reuses_covering_row(monkeypatch):
    # Row starts within 7d grace of the requested start and ends near the
    # requested end → reused without any fetch.
    store = {"VOO": {"id": "VOO", "bars": _bars("2025-12-16", 220), "adjustment": "split",
                     "fetch_start": "2025-12-16", "fetch_end": "2026-07-19"}}
    cache = {}
    batches = _run_ensure(monkeypatch, store, cache, ["VOO"],
                          ("2025-12-14", "2026-07-19"))
    assert not batches, "covering row must be reused without refetch"
    assert len(cache["_overlay_bars_raw"]["VOO"]) == 220


def test_ensure_legacy_row_uses_first_bar_for_start(monkeypatch):
    # Legacy rows (no fetch_start) fall back to the first bar's date.
    store = {"XLF": {"id": "XLF", "bars": _bars("2026-04-30", 53), "adjustment": "split"}}
    cache = {}
    batches = _run_ensure(monkeypatch, store, cache, ["XLF"],
                          ("2025-12-14", "2026-07-19"))
    assert batches and "XLF" in batches[0]


def test_legacy_row_without_adjustment_is_a_miss(monkeypatch):
    """A row written before bars moved to adjustment="split" holds RAW closes.

    Serving it as if it were split-adjusted is exactly the corruption the
    fetch-side change removes (VGT's 8-for-1 read as an -87% crash), and the
    row is keyed by symbol alone so nothing else distinguishes the two. Treat
    it as a miss: the row is refetched and overwritten, no purge required.
    """
    store = {"SPY": {"id": "SPY", "bars": _bars("2025-12-14", 5)}}  # no adjustment
    _with_fake_rdb(monkeypatch, store)
    assert g._overlay_bars_cache_get(object(), "SPY") is None


def test_row_with_mismatched_adjustment_is_a_miss(monkeypatch):
    store = {"SPY": {"id": "SPY", "bars": _bars("2025-12-14", 5),
                     "adjustment": "raw"}}
    _with_fake_rdb(monkeypatch, store)
    assert g._overlay_bars_cache_get(object(), "SPY") is None


def test_cache_set_stamps_the_adjustment(monkeypatch):
    written = {}
    _with_fake_rdb(monkeypatch, written)
    g._overlay_bars_cache_set(object(), "SPY", _bars("2025-12-14", 5))
    assert written["SPY"]["adjustment"] == "split"
