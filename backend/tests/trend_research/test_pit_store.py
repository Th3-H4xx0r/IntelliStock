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


# -- ingest completion markers (ruling R14) -------------------------------------

def test_progress_table_is_registered_and_not_a_pit_table():
    assert "TrendIngestProgress" in schema.ALL_TABLES
    assert schema.TABLES["TrendIngestProgress"].prefix_fields == ("id",)
    assert "TrendIngestProgress" not in pit_store.PIT_TABLES


def test_mark_done_is_done_and_done_keys(store):
    assert not pit_store.is_done("alfred", "PAYEMS|2022-05-29", store=store)
    pit_store.mark_done("alfred", "PAYEMS|2022-05-29", {"rows": 3}, store=store)
    pit_store.mark_done("alfred", "PAYEMS|2022-06-10", store=store)
    pit_store.mark_done("alfred", "PAYEMS2|2022-05-29", store=store)
    pit_store.mark_done("gdelt_titles", "inflation|2022-06-20", store=store)
    pit_store.mark_done("alfred", "PAYEMS|2022-05-29", {"rows": 4}, store=store)  # replace
    assert pit_store.is_done("alfred", "PAYEMS|2022-05-29", store=store)
    assert pit_store.done_keys("alfred", "PAYEMS|", store=store) == {
        "PAYEMS|2022-05-29", "PAYEMS|2022-06-10"}
    assert pit_store.done_keys("gdelt_titles", store=store) == {"inflation|2022-06-20"}
    row = store.get("TrendIngestProgress", "alfred|PAYEMS|2022-05-29")
    assert row["meta"] == {"rows": 4} and row["source"] == "alfred"
    assert "available_at" not in row and row["updated_at"].endswith("Z")


def test_progress_rejects_bad_source_or_key(store):
    with pytest.raises(ValueError):
        pit_store.mark_done("al|fred", "x", store=store)
    with pytest.raises(ValueError):
        pit_store.mark_done("alfred", "", store=store)
    with pytest.raises(ValueError):
        pit_store.done_keys("", store=store)


def test_mark_done_surfaces_a_rejected_write(store, monkeypatch):
    monkeypatch.setattr(store, "insert", lambda table, rows, **kw: InsertResult(
        inserted=0, replaced=0, errors=1, first_error="boom"))
    with pytest.raises(StoreError, match="boom"):
        pit_store.mark_done("alfred", "PAYEMS|2022-05-29", store=store)


# -- fix wave A1: the tables are ensured on the real store -----------------------------

class _Ensured(Exception):
    pass


@pytest.fixture
def ensure_spy(monkeypatch):
    """``schema.ensure_schema`` recorded and short-circuited before any SQL:
    proves the real-store code path asks for the TR tables first."""
    calls = []

    def fake_ensure_schema(*, tables=None):
        calls.append(list(tables))
        raise _Ensured()
    monkeypatch.setattr(pit_store, "_ENSURED", False)
    monkeypatch.setattr(schema, "ensure_schema", fake_ensure_schema)
    return calls


def test_ensure_tables_creates_every_tr_table_once(monkeypatch):
    calls = []
    monkeypatch.setattr(pit_store, "_ENSURED", False)
    monkeypatch.setattr(schema, "ensure_schema", lambda *, tables=None: calls.append(tables))
    pit_store.ensure_tables(FakeStore())                    # a fake needs none
    assert calls == []
    pit_store.ensure_tables()
    pit_store.ensure_tables()
    from db import store as real_store
    pit_store.ensure_tables(real_store)
    assert calls == [list(pit_store.TR_TABLES)]
    assert set(pit_store.TR_TABLES) == {"TrendNewsPIT", "TrendThemesPIT", "TrendMacroPIT",
                                        "TrendFilingsPIT", "TrendResearchLedger",
                                        "TrendIngestProgress", "TrendResearchJobs"}
    for name in pit_store.TR_TABLES:
        assert name in schema.ALL_TABLES


@pytest.mark.parametrize("entry", [
    "put_rows", "read_available", "mark_done", "done_keys", "ledger_get_week",
    "jobs_submit", "jobs_status", "jobs_run", "news_missing_days", "macro_coverage",
    "has_operating_filings"])
def test_every_entry_point_ensures_the_tables_before_touching_the_real_store(ensure_spy,
                                                                             entry):
    from datetime import date
    from trend_research import filings, ingest_alpaca_news, ingest_macro, jobs, ledger
    calls = {
        "put_rows": lambda: pit_store.put_rows("TrendNewsPIT", [news(T, "1")]),
        "read_available": lambda: read_week(None),
        "mark_done": lambda: pit_store.mark_done("alfred", "X|2025-01-01"),
        "done_keys": lambda: pit_store.done_keys("alfred"),
        "ledger_get_week": lambda: ledger.get_week("c", date(2025, 8, 4), "m", "tr-v1"),
        "jobs_submit": lambda: jobs.submit("ingest_macro", {"start": "2025-08-04",
                                                            "end": "2025-08-10"}),
        "jobs_status": lambda: jobs.status("tr-job|x|ingest_macro"),
        "jobs_run": lambda: jobs.run("tr-job|x|ingest_macro"),
        "news_missing_days": lambda: ingest_alpaca_news.missing_days(date(2025, 8, 4),
                                                                     date(2025, 8, 5)),
        "macro_coverage": lambda: ingest_macro.missing_coverage(date(2025, 8, 4),
                                                                date(2025, 8, 5)),
        "has_operating_filings": lambda: filings.has_operating_filings("AAPL", T),
    }
    with pytest.raises(_Ensured):
        calls[entry]()
    assert ensure_spy == [list(pit_store.TR_TABLES)]


def test_tr_tables_are_created_on_an_empty_postgres_schema(pg_schema, monkeypatch):
    """Real Postgres (PG_TEST_DSN): a schema with no tables at all, as in
    production before the first job -- the first write and read work."""
    from db import store as real_store
    from trend_research import jobs
    monkeypatch.setattr(pit_store, "_ENSURED", False)
    assert pit_store.put_rows("TrendNewsPIT", [news(T - timedelta(hours=1), "1")]) == 1
    assert [r["source_id"] for r in read_week(None)] == ["1"]
    pit_store.mark_done("alfred", "X|2025-01-01")
    assert jobs._rows(real_store) == []
    assert real_store.get("TrendResearchLedger", "missing") is None
