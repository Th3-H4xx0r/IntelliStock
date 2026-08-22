"""Regressions for the whole-branch merge-gate review of Plan A.

One test per finding in
``.superpowers/sdd/2026-08-22-postgres-port-A-db-core/final-review.md``.
Each of these fails on the pre-fix tree.
"""
import datetime as dt

import pytest

from db import pool as dbpool
from db import schema
from db import store
from db.errors import StoreError, UnavailableError

from .conftest import requires_pg


# ---------------------------------------------------------------- C3 ------
@requires_pg
def test_a_mid_query_connection_loss_raises_unavailable_not_runtimeerror(pg_schema):
    """A server-side disconnect during the caller's ``with`` body used to come
    back as ``RuntimeError: generator didn't stop after throw()``."""
    import psycopg
    with pytest.raises(UnavailableError):
        with dbpool.connection():
            raise psycopg.OperationalError(
                "server closed the connection unexpectedly")


@requires_pg
def test_a_query_level_error_is_not_retried_and_keeps_its_class(pg_schema):
    import psycopg
    with pytest.raises(psycopg.errors.UndefinedFunction):
        with dbpool.connection() as conn:
            conn.execute("SELECT nonexistent_function_xyz()")


@requires_pg
def test_the_connection_still_commits_on_the_happy_path(pg_schema):
    schema.ensure_schema(tables=["DiscordOutbox"])
    with dbpool.connection() as conn:
        conn.execute('INSERT INTO "DiscordOutbox" (id, doc) '
                     "VALUES ('a', '{\"id\":\"a\"}'::jsonb)")
    assert store.get("DiscordOutbox", "a") == {"id": "a"}


# ---------------------------------------------------------------- C2 ------
@requires_pg
def test_a_numeric_dict_filter_survives_a_mixed_type_column(pg_schema):
    """BacktestResults really holds instance_id as a NUMBER on 592 rows and a
    STRING on 833. ``(doc->'k')::numeric`` is evaluated for every scanned row,
    so one string row used to poison the whole query with
    ``cannot cast jsonb string to type numeric``."""
    schema.ensure_schema(tables=["BacktestResults"])
    store.insert("BacktestResults", [{"id": 1, "instance_id": 5},
                                     {"id": 2, "instance_id": "five"},
                                     {"id": 3, "instance_id": 7},
                                     {"id": 4}])
    rows = store.run(store.filter("BacktestResults", {"instance_id": 5}))
    assert [r["id"] for r in rows] == [1]


@requires_pg
def test_a_numeric_order_by_survives_a_mixed_type_column(pg_schema):
    schema.ensure_schema(tables=["BacktestResults"])
    store.insert("BacktestResults", [{"id": 1, "n": 5}, {"id": 2, "n": "x"},
                                     {"id": 3, "n": 1}])
    rows = store.run(store.order_by(store.Selection("BacktestResults"),
                                    fields=[store.asc("n", numeric=True)]))
    assert [r["id"] for r in rows][:2] == [3, 1]


# ---------------------------------------------------------------- C1 ------
@requires_pg
def test_ensure_schema_creates_the_partitions_it_declares(pg_schema):
    schema.ensure_schema(tables=["PriceHistory"])
    with dbpool.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM pg_class c "
                    "JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE n.nspname = %s AND c.relkind = 'r' "
                    "AND c.relname LIKE 'PriceHistory\\_p%%'", (pg_schema,))
        assert cur.fetchone()["n"] >= 2      # this month + the premake horizon


@requires_pg
def test_price_history_is_writable_through_store_insert(pg_schema):
    """priceBroker.py:186 inserts {ticker, price, timestamp, type} with no id.
    Every part of that has to work: the generated id, the ticker/ts columns,
    and a partition to route into."""
    schema.ensure_schema(tables=["PriceHistory"])
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    res = store.insert("PriceHistory", {"ticker": "T.AAPL", "price": 1.5,
                                        "timestamp": now, "type": "minute"})
    assert (res.inserted, res.errors) == (1, 0)
    assert len(res.generated_keys) == 1
    row = store.get("PriceHistory", res.generated_keys[0])
    assert row["ticker"] == "T.AAPL" and row["price"] == 1.5
    cols = store.sql('SELECT ticker, ts FROM "PriceHistory"')
    assert cols[0]["ticker"] == "T.AAPL"
    assert cols[0]["ts"] == dt.datetime.fromisoformat(now)


@requires_pg
def test_price_history_creates_a_partition_for_a_historical_row(pg_schema):
    schema.ensure_schema(tables=["PriceHistory"])
    res = store.insert("PriceHistory",
                       {"ticker": "T.X", "timestamp": "2019-04-17T10:00:00+00:00"})
    assert (res.inserted, res.errors) == (1, 0)
    assert store.sql("SELECT count(*) AS n FROM \"PriceHistory_p2019_04\""
                     )[0]["n"] == 1


@requires_pg
def test_an_unparseable_price_history_timestamp_raises_it_is_never_dropped(pg_schema):
    """design 3.4: 'a row whose timestamp will not parse is rejected with a
    StoreError rather than silently dropped'."""
    schema.ensure_schema(tables=["PriceHistory"])
    with pytest.raises(StoreError):
        store.insert("PriceHistory", {"ticker": "T.X", "timestamp": "not a date"})
    assert store.count("PriceHistory") == 0


@requires_pg
def test_price_history_has_an_id_index(pg_schema):
    schema.ensure_schema(tables=["PriceHistory"])
    rows = store.sql("SELECT indexname FROM pg_indexes "
                     "WHERE schemaname = %s AND tablename = 'PriceHistory'",
                     (pg_schema,))
    assert "PriceHistory_id_idx" in {r["indexname"] for r in rows}


# ---------------------------------------------------------------- I4 ------
@requires_pg
def test_a_missing_primary_key_is_generated_like_rethinkdb(pg_schema):
    schema.ensure_schema(tables=["kalshi_markets"])
    res = store.insert("kalshi_markets", {"yes_bid": 1})
    assert res.inserted == 1 and len(res.generated_keys) == 1
    key = res.generated_keys[0]
    assert store.get("kalshi_markets", key)["market_ticker"] == key


@requires_pg
def test_an_int_keyed_table_still_refuses_a_missing_primary_key(pg_schema):
    """A uuid in an id_type='int' table is exactly the shadow row coerce_id
    exists to forbid, so generation stops at the registry."""
    schema.ensure_schema(tables=["Instances"])
    with pytest.raises(StoreError):
        store.insert("Instances", {"name": "x"})


# ---------------------------------------------------------------- I1 ------
def test_field_ref_ordering_comparisons_carry_collate_c():
    frag, _ = store.P.field("k").lt("m").to_sql()
    assert 'COLLATE "C"' in frag
    for op in ("le", "gt", "ge"):
        assert 'COLLATE "C"' in getattr(store.P.field("k"), op)("m").to_sql()[0]


# ---------------------------------------------------------------- I3 ------
@requires_pg
def test_a_client_side_rejection_writes_no_part_of_the_batch(pg_schema):
    """A NaN is a CLIENT-side rejection in RethinkDB too -- it raises before
    anything reaches the server, so no row of the batch lands. Encoding inside
    the write loop half-wrote the batch instead: earlier chunks committed, and
    every good row of the failing chunk was rolled back."""
    schema.ensure_schema(tables=["DiscordOutbox"])
    docs = ([{"id": "pre%d" % i} for i in range(store.WRITE_CHUNK)]
            + [{"id": "b", "v": float("nan")}, {"id": "c"}])
    with pytest.raises(ValueError):
        store.insert("DiscordOutbox", docs)
    assert store.count("DiscordOutbox") == 0


# ---------------------------------------------------------------- I5 ------
@requires_pg
def test_a_noop_update_reports_unchanged_not_replaced(pg_schema):
    schema.ensure_schema(tables=["DiscordOutbox"])
    store.insert("DiscordOutbox", {"id": "a", "state": "queued"})
    res = store.update("DiscordOutbox", "a", {"state": "queued"})
    assert (res.replaced, res.unchanged, res.skipped) == (0, 1, 0)
    res = store.update("DiscordOutbox", "a", {"state": "sent"})
    assert (res.replaced, res.unchanged) == (1, 0)


@requires_pg
def test_a_noop_replace_reports_unchanged(pg_schema):
    schema.ensure_schema(tables=["DiscordOutbox"])
    store.insert("DiscordOutbox", {"id": "a", "v": 1})
    assert store.replace("DiscordOutbox", "a", {"id": "a", "v": 1}).unchanged == 1
    assert store.replace("DiscordOutbox", "a", {"id": "a", "v": 2}).replaced == 1


@requires_pg
def test_deleting_a_missing_row_is_skipped_not_deleted_zero(pg_schema):
    schema.ensure_schema(tables=["DiscordOutbox"])
    res = store.delete("DiscordOutbox", "nope")
    assert (res.deleted, res.skipped) == (0, 1)
    # A Selection that matches nothing is deleted=0, as in ReQL.
    res = store.delete("DiscordOutbox", store.filter("DiscordOutbox", {"k": "v"}))
    assert (res.deleted, res.skipped) == (0, 0)


# ---------------------------------------------------------------- I6 ------
@requires_pg
def test_count_honours_limit_like_reql(pg_schema):
    schema.ensure_schema(tables=["DiscordOutbox"])
    store.insert("DiscordOutbox", [{"id": "a"}, {"id": "b"}, {"id": "c"}])
    sel = store.Selection("DiscordOutbox")
    assert store.count(sel) == 3
    assert store.count(store.limit(sel, 2)) == 2
    assert store.count(store.limit(sel, 9)) == 3
    assert store.count(store.slice(sel, 1, 3)) == 2


# ---------------------------------------------------------------- I7 ------
@requires_pg
def test_the_watcher_snapshot_is_not_capped_by_pg_max_rows(pg_schema, monkeypatch):
    from db import watch as dbwatch
    schema.ensure_schema(tables=["DiscordOutbox"])
    store.insert("DiscordOutbox", [{"id": str(i)} for i in range(12)])
    monkeypatch.setattr(store, "PG_MAX_ROWS", 3)
    w = dbwatch.Watcher("DiscordOutbox", lambda c: None, label="t")
    assert len(w._snapshot()) == 12


# ---------------------------------------------------------------- I8 ------
def test_the_fake_mirrors_the_whole_store_surface():
    from db.fake import FakeStore
    f = FakeStore()
    for name in ("Literal", "deep_merge", "encode_patch", "Order", "WRITE_CHUNK"):
        assert hasattr(f, name), name
    assert f.Literal is store.Literal
    assert f.WRITE_CHUNK == store.WRITE_CHUNK


# ---------------------------------------------------------------- I2 ------
@requires_pg
def test_price_history_retention_deletes_by_column_not_by_doc_key(pg_schema):
    """spec("PriceHistory").retention.field is "ts", a real COLUMN. The sweep
    filtered on doc->>'ts', which is always NULL, so it reported deleted:0
    forever."""
    from .test_partitions_retention import _load_retention_module
    mod = _load_retention_module()
    schema.ensure_schema(tables=["PriceHistory"])
    old = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=400)).isoformat()
    new = dt.datetime.now(dt.timezone.utc).isoformat()
    store.insert("PriceHistory", [{"ticker": "T.A", "timestamp": old},
                                  {"ticker": "T.A", "timestamp": new}])
    os_env = {"RETAIN_PRICE_HISTORY_DAYS": "30"}
    import os
    for k, v in os_env.items():
        os.environ[k] = v
    try:
        assert mod.sweep("PriceHistory", batch=10)["deleted"] == 1
    finally:
        for k in os_env:
            os.environ.pop(k, None)
    assert store.count("PriceHistory") == 1


@requires_pg
def test_the_retention_sweep_batches_safely_across_partitions(pg_schema):
    """ctid is unique only WITHIN a partition, so a ctid subquery could name a
    tuple that belongs to a sibling partition. Batch size 1 forces many
    subquery rounds across two partitions holding identical documents."""
    import os
    from .test_partitions_retention import _load_retention_module
    mod = _load_retention_module()
    schema.ensure_schema(tables=["PriceHistory"])
    old_rows = [{"ticker": "T.A", "timestamp": "2019-0%d-01T00:00:00+00:00" % m}
                for m in (1, 2, 3)]
    keep = [{"ticker": "T.A", "timestamp":
             dt.datetime.now(dt.timezone.utc).isoformat()} for _ in range(3)]
    store.insert("PriceHistory", old_rows + keep)
    os.environ["RETAIN_PRICE_HISTORY_DAYS"] = "30"
    try:
        assert mod.sweep("PriceHistory", batch=1)["deleted"] == 3
    finally:
        os.environ.pop("RETAIN_PRICE_HISTORY_DAYS", None)
    assert store.count("PriceHistory") == 3


# ------------------------------------------------------------- minors -----
def test_all_tables_creates_the_split_tables_too():
    assert "BacktestSteps" in schema.ALL_TABLES
    assert "BacktestProgress" in schema.ALL_TABLES


def test_store_iter_cursor_names_are_unique():
    import inspect
    assert "id(sel)" not in inspect.getsource(store.iter)
