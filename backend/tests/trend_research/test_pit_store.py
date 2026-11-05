"""TR point-in-time tables and store helpers (Task 1).

The availability gate is the whole contract: a record reaches a prompt only
when ``available_at < decision_time`` (strict), and a later re-fetch of the
same record (an ``updated_at`` edit) can never leak its newer content or push
its availability later. FakeStore by default, real Postgres under PG_TEST_DSN.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

_backend = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from db import schema  # noqa: E402
from db.errors import StoreError  # noqa: E402
from db.fake import FakeStore  # noqa: E402
from db.store import InsertResult  # noqa: E402
from trend_research import pit_store  # noqa: E402

T = datetime(2026, 3, 2, 14, 25, tzinfo=timezone.utc)   # a Monday, 09:25 ET


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def news(at: datetime, source_id: str, **extra) -> dict:
    row = {"id": f"{iso(at)}|alpaca|{source_id}", "source": "alpaca",
           "source_id": source_id, "published_at": iso(at),
           "available_at": iso(at), "headline": f"headline {source_id}"}
    row.update(extra)
    return row


def read_week(store, decision_time=T):
    return pit_store.read_available(
        "TrendNewsPIT", lo_id=iso(decision_time - timedelta(days=7)),
        hi_id=iso(decision_time + timedelta(days=1)),
        decision_time=decision_time, store=store)


# -- registration ------------------------------------------------------------

def test_the_five_tables_are_registered():
    for name in pit_store.PIT_TABLES:
        assert name in schema.ALL_TABLES, name
        assert schema.spec(name).id_type == "text", name
    for name in ("TrendNewsPIT", "TrendThemesPIT", "TrendMacroPIT",
                 "TrendFilingsPIT", "TrendResearchLedger"):
        assert schema.TABLES[name].prefix_fields == ("id",), name
    assert schema.TABLES["TrendResearchLedger"].indexed_fields == ("chain_id",)


# -- put_rows validation ------------------------------------------------------

def test_put_requires_available_at(store):
    good = news(T - timedelta(hours=1), "ok")
    bad = news(T - timedelta(hours=2), "missing")
    del bad["available_at"]
    with pytest.raises(ValueError, match="available_at"):
        pit_store.put_rows("TrendNewsPIT", [good, bad], store=store)
    # validated before anything is written: no partial batch
    assert store.get("TrendNewsPIT", good["id"]) is None


@pytest.mark.parametrize("value", [
    None, "", "not a time", "2026-03-02T09:25:00",          # naive
    "2026-03-02T09:25:00-05:00",                             # not UTC
    datetime(2026, 3, 2, 14, 25, tzinfo=timezone.utc),       # not a string
])
def test_put_rejects_a_non_utc_iso_available_at(store, value):
    row = news(T - timedelta(hours=1), "x")
    row["available_at"] = value
    with pytest.raises(ValueError, match="available_at"):
        pit_store.put_rows("TrendNewsPIT", [row], store=store)


def test_put_rejects_a_row_without_an_id(store):
    row = news(T - timedelta(hours=1), "x")
    del row["id"]
    with pytest.raises(ValueError, match="id"):
        pit_store.put_rows("TrendNewsPIT", [row], store=store)


def test_put_rejects_an_unregistered_table(store):
    with pytest.raises(ValueError, match="table"):
        pit_store.put_rows("NewsRaw", [news(T, "x")], store=store)


def test_put_accepts_plus_zero_offset_and_returns_the_written_count(store):
    a = news(T - timedelta(hours=1), "a")
    b = news(T - timedelta(hours=2), "b")
    b["available_at"] = (T - timedelta(hours=2)).isoformat()   # "+00:00"
    assert pit_store.put_rows("TrendNewsPIT", [a, b], store=store) == 2
    assert pit_store.put_rows("TrendNewsPIT", [], store=store) == 0


# -- read_available ------------------------------------------------------------

def test_read_available_is_strict(store):
    rows = [news(T - timedelta(seconds=1), "before"),
            news(T, "at"),
            news(T + timedelta(seconds=1), "after")]
    pit_store.put_rows("TrendNewsPIT", rows, store=store)
    got = read_week(store)
    assert [r["source_id"] for r in got] == ["before"]


def test_read_available_ignores_updated_at(store):
    original = news(T - timedelta(hours=3), "edited", summary="first draft")
    pit_store.put_rows("TrendNewsPIT", [original], store=store)
    # The same article re-fetched after the decision: same id, an updated_at
    # past T, rewritten content, and a later availability stamp.
    edited = dict(original, summary="rewritten after the fact",
                  updated_at=iso(T + timedelta(hours=2)),
                  available_at=iso(T + timedelta(hours=2)))
    assert pit_store.put_rows("TrendNewsPIT", [edited], store=store) == 0
    # Same availability stamp, newer content: still the original.
    same_stamp = dict(original, summary="rewritten, same stamp",
                      updated_at=iso(T + timedelta(hours=2)))
    assert pit_store.put_rows("TrendNewsPIT", [same_stamp], store=store) == 0

    got = read_week(store)
    assert got == [original]
    assert "updated_at" not in got[0]
    assert store.get("TrendNewsPIT", original["id"])["available_at"] == original["available_at"]


def test_a_row_carrying_a_later_updated_at_is_returned_unchanged(store):
    """updated_at plays no part in the gate: availability alone decides."""
    row = news(T - timedelta(hours=1), "upd", summary="as stored",
               updated_at=iso(T + timedelta(hours=1)))
    assert pit_store.put_rows("TrendNewsPIT", [row], store=store) == 1
    assert read_week(store) == [row]


class _RejectingStore(FakeStore):
    """A store double whose insert reports one server-side rejection, the way
    db.store.insert counts (not raises) a row that fails its savepoint."""

    def insert(self, table, doc_or_docs, *, conflict="error", durability="hard"):
        docs = list(doc_or_docs)
        ok = super().insert(table, docs[:-1], conflict=conflict)
        return InsertResult(inserted=ok.inserted, replaced=ok.replaced,
                            errors=1, first_error="value too long for type")


def test_put_raises_when_the_store_rejects_a_row():
    st = _RejectingStore()
    rows = [news(T - timedelta(hours=1), "a"), news(T - timedelta(hours=2), "b")]
    with pytest.raises(StoreError, match="1 of 2 rows rejected.*value too long"):
        pit_store.put_rows("TrendNewsPIT", rows, store=st)


def test_an_earlier_availability_stamp_replaces_the_stored_row(store):
    late = news(T + timedelta(hours=1), "restamp")
    pit_store.put_rows("TrendNewsPIT", [late], store=store)
    assert read_week(store) == []
    early = dict(late, available_at=iso(T - timedelta(hours=1)))
    assert pit_store.put_rows("TrendNewsPIT", [early], store=store) == 1
    assert [r["source_id"] for r in read_week(store)] == ["restamp"]


def test_duplicate_ids_in_one_batch_keep_the_earliest_availability(store):
    first = news(T - timedelta(hours=1), "dup", headline="earliest")
    later = dict(first, headline="later", available_at=iso(T + timedelta(hours=1)))
    assert pit_store.put_rows("TrendNewsPIT", [later, first], store=store) == 1
    assert [r["headline"] for r in read_week(store)] == ["earliest"]


def test_read_available_is_bounded_by_id_half_open(store):
    rows = [{"id": f"2026-02-2{d}|ai_capex", "theme": "ai_capex", "volume": d,
             "available_at": iso(datetime(2026, 2, 20 + d, 1, tzinfo=timezone.utc))}
            for d in (3, 4, 5)]
    pit_store.put_rows("TrendThemesPIT", rows, store=store)
    got = pit_store.read_available("TrendThemesPIT", lo_id="2026-02-23",
                                   hi_id="2026-02-25", decision_time=T,
                                   store=store)
    assert [r["id"] for r in got] == ["2026-02-23|ai_capex", "2026-02-24|ai_capex"]


def test_read_available_returns_oldest_first_by_availability(store):
    # TrendFilingsPIT ids sort by symbol first, so id order is not time order.
    rows = [{"id": "MSFT|2026-01-10T21:00:00Z|0001", "available_at": "2026-01-10T21:00:00Z"},
            {"id": "AAPL|2026-02-01T21:00:00Z|0002", "available_at": "2026-02-01T21:00:00Z"},
            {"id": "NVDA|2026-01-05T21:00:00Z|0003", "available_at": "2026-01-05T21:00:00Z"}]
    pit_store.put_rows("TrendFilingsPIT", rows, store=store)
    got = pit_store.read_available("TrendFilingsPIT", lo_id="A", hi_id="Z",
                                   decision_time=T, store=store)
    assert [r["id"][:4] for r in got] == ["NVDA", "MSFT", "AAPL"]


def test_read_available_requires_an_aware_decision_time(store):
    with pytest.raises(ValueError, match="decision_time"):
        pit_store.read_available("TrendNewsPIT", lo_id="a", hi_id="b",
                                 decision_time=datetime(2026, 3, 2, 14, 25),
                                 store=store)
