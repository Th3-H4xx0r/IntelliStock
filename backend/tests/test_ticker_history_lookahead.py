"""Ticker headline history must not hand a backtest news from its own future.

THE DEFECT. `GraphNexusTickerHistory` is keyed by the BARE TICKER
(`"id": ticker`, graph_nexus_analysis.py:16055) — no instance, no history scope,
no salt — and the restart cleanup never touches it ("Shared caches preserved").
So a run over a LATER window leaves headlines dated after THIS window under the
same ticker id.

The read then did:

    headlines = sorted(doc["headlines"], key=lambda x: x.get("d", ""), reverse=True)
    result[ticker] = headlines[:max_per_ticker]

Newest first, then truncate. So future-dated headlines were not merely present,
they were PREFERENTIALLY SELECTED — they crowd the real ones out of the top-10 —
and the result flows straight into the sentiment LLM prompt (`ticker_history=`
at :26931). A day-1 prompt could be reasoning about news from months ahead.

That is a LOOKAHEAD, not an A/B asymmetry: it biases both arms of a pair the
same way, so a paired comparison survives it, but no ABSOLUTE return from any
run in this project is clean while it is open.

Fixed on READ rather than by purging the table: no destructive operation on
shared production data, it fixes every future run rather than only the next one,
and it cannot be forgotten between arms.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from strategies.graph_nexus_analysis import _get_ticker_history  # noqa: E402


class _Doc(dict):
    pass


class _Table:
    def __init__(self, docs):
        self._docs = docs

    def get(self, key):
        self._key = key
        return self

    def run(self, _conn):
        return self._docs.get(self._key)


class _DB:
    def __init__(self, docs):
        self._docs = docs

    def table(self, _name):
        return _Table(self._docs)


class _R:
    def __init__(self, docs):
        self._docs = docs

    def db(self, _name):
        return _DB(self._docs)


# SNDK's real window is Jan-Feb 2026. The 2026-06 entries are what a later W3
# run leaves behind under the same bare ticker id.
DOCS = {
    "SNDK": {
        "id": "SNDK",
        "headlines": [
            {"d": "2026-01-05", "h": "SanDisk expands NAND capacity"},
            {"d": "2026-01-09", "h": "Memory pricing firms"},
            {"d": "2026-06-14", "h": "FUTURE: SanDisk doubles on AI memory demand"},
            {"d": "2026-06-20", "h": "FUTURE: analysts raise targets sharply"},
        ],
    }
}


def _patch(monkeypatch):
    import strategies.graph_nexus_analysis as gna
    monkeypatch.setattr(gna, "_r", _R(DOCS))
    monkeypatch.setattr(gna, "_ensure_ticker_history_table", lambda _c: None)


def test_without_the_bound_the_future_headlines_are_returned_FIRST(monkeypatch):
    """The pre-fix behaviour, pinned. This is the defect, not a nicety: the sort
    puts the future first, so a small max_per_ticker returns ONLY future news."""
    _patch(monkeypatch)
    out = _get_ticker_history(object(), ["SNDK"], max_per_ticker=2)
    dates = [e["d"] for e in out["SNDK"]]
    assert dates == ["2026-06-20", "2026-06-14"], dates
    assert all(d > "2026-03-01" for d in dates), (
        "both entries a January backtest would see are from June")


def test_the_as_of_bound_removes_every_future_headline(monkeypatch):
    _patch(monkeypatch)
    out = _get_ticker_history(object(), ["SNDK"], max_per_ticker=10,
                              as_of="2026-01-12")
    dates = [e["d"] for e in out["SNDK"]]
    assert dates == ["2026-01-09", "2026-01-05"], dates


def test_the_bound_is_inclusive_of_the_current_day(monkeypatch):
    """Today's news is not the future."""
    _patch(monkeypatch)
    out = _get_ticker_history(object(), ["SNDK"], max_per_ticker=10,
                              as_of="2026-01-09")
    assert [e["d"] for e in out["SNDK"]] == ["2026-01-09", "2026-01-05"]


def test_undated_entries_are_dropped_not_kept(monkeypatch):
    """Fail CLOSED. An entry whose date cannot be established cannot be shown to
    be in the past, and a guard that keeps it is not worth having."""
    import strategies.graph_nexus_analysis as gna
    docs = {"X": {"id": "X", "headlines": [
        {"d": "2026-01-05", "h": "dated"},
        {"h": "undated"},
        {"d": "", "h": "empty date"},
        {"d": "   ", "h": "whitespace date"},
    ]}}
    monkeypatch.setattr(gna, "_r", _R(docs))
    monkeypatch.setattr(gna, "_ensure_ticker_history_table", lambda _c: None)
    out = _get_ticker_history(object(), ["X"], max_per_ticker=10,
                              as_of="2026-01-12")
    assert [e["h"] for e in out["X"]] == ["dated"]


def test_absent_as_of_is_byte_identical_to_the_old_behaviour(monkeypatch):
    _patch(monkeypatch)
    a = _get_ticker_history(object(), ["SNDK"], max_per_ticker=10)
    b = _get_ticker_history(object(), ["SNDK"], max_per_ticker=10, as_of="")
    assert a == b
    assert len(a["SNDK"]) == 4, "no filtering when the bound is absent"


def test_a_bound_before_all_history_returns_nothing_rather_than_the_future(monkeypatch):
    _patch(monkeypatch)
    out = _get_ticker_history(object(), ["SNDK"], max_per_ticker=10,
                              as_of="2026-01-01")
    assert out.get("SNDK") == []
