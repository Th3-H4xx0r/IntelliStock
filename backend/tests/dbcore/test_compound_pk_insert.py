"""store.insert writes the one table whose primary key is not ``id``.

PriceHistory's PK is (ticker, ts, id) -- the partition key has to be part of
it -- and for a while store.insert only spoke (id, doc), which is why
priceBroker.py carried hand-written SQL. c0532c4 gave the store
``column_sources`` (real columns filled from named document keys), a conflict
target taken from the registry's real PK, and a client-generated id for a
document that has none. These are the regressions for that, written from
priceBroker's exact document.
"""
import datetime as _dt

import pytest

from db import schema
from db import store
from db.errors import StoreError

from .conftest import requires_pg

_TS = "2026-08-22T14:30:00.000Z"


def _doc(ticker="T.AAPL", price=1.25, ts=_TS):
    """priceBroker's document, id deliberately absent."""
    return {"ticker": ticker, "price": price, "timestamp": ts, "type": "minute"}


@pytest.fixture
def price_history(pg_schema):
    schema.ensure_schema(tables=["PriceHistory"])
    return pg_schema


@requires_pg
def test_insert_generates_the_id_and_carries_it_in_the_document(price_history):
    result = store.insert("PriceHistory", _doc())
    assert result["inserted"] == 1 and result["errors"] == 0
    rid = result["generated_keys"][0]
    rows = store.sql('SELECT doc FROM "PriceHistory" WHERE id = %s', (rid,))
    assert rows[0]["doc"] == dict(_doc(), id=rid)


@requires_pg
def test_insert_fills_the_partition_key_columns_from_the_document(price_history):
    rid = store.insert("PriceHistory", _doc())["generated_keys"][0]
    rows = store.sql('SELECT ticker, ts FROM "PriceHistory" WHERE id = %s', (rid,))
    assert rows[0]["ticker"] == "T.AAPL"
    assert rows[0]["ts"] == _dt.datetime(2026, 8, 22, 14, 30,
                                         tzinfo=_dt.timezone.utc)


@requires_pg
def test_insert_creates_the_month_partition_it_needs(price_history):
    """No default partition exists by design, and pg_partman only keeps the
    rolling window ahead -- a historical timestamp has to make its own."""
    rid = store.insert("PriceHistory",
                       _doc(ts="2019-04-07T09:00:00.000Z"))["generated_keys"][0]
    rows = store.sql("SELECT tableoid::regclass::text AS part "
                     'FROM "PriceHistory" WHERE id = %s', (rid,))
    assert rows[0]["part"].strip('"') == "PriceHistory_p2019_04"


@requires_pg
def test_the_conflict_target_is_the_real_primary_key(price_history):
    """ON CONFLICT (id) has no matching unique index on this table at all, so
    a re-insert of the same PK must be absorbed, not raise."""
    doc = dict(_doc(), id="fixed-id")
    assert store.insert("PriceHistory", doc)["inserted"] == 1
    again = store.insert("PriceHistory", doc)
    assert again["inserted"] == 0
    assert again["errors"] == 1          # ReQL's duplicate-key report
    assert store.count("PriceHistory") == 1


@requires_pg
def test_a_differing_id_on_the_same_ticker_and_ts_is_a_separate_row(price_history):
    store.insert("PriceHistory", [dict(_doc(), id="a"), dict(_doc(), id="b")])
    assert store.count("PriceHistory") == 2


@requires_pg
def test_an_unparseable_timestamp_is_rejected_before_anything_is_written(
        price_history):
    with pytest.raises(StoreError):
        store.insert("PriceHistory", [_doc(), _doc(ts="not-a-time")])
    assert store.count("PriceHistory") == 0


@requires_pg
def test_a_missing_ticker_is_rejected(price_history):
    doc = _doc()
    del doc["ticker"]
    with pytest.raises(StoreError):
        store.insert("PriceHistory", doc)
