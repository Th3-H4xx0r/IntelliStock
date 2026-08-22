"""Control-plane watchers after the Postgres port (plan C, group G1).

The five identical `EngineControl` change feeds in server.py collapse into
`server._watch_engine_control`; priceBroker.py's two `Config` feeds become
`_watch_pings` / `_watch_config`; instance.py's two `Instances.get(id)` feeds
become `watch.feed('Instances', row_id=...)` inside the existing
retry-then-crash-keepalive loops.

LISTEN/NOTIFY cannot be faked, so these need a real database and skip without
PG_TEST_DSN — the same rule backend/tests/dbcore uses.

priceBroker.py is a *script*: importing it runs the poll loop forever. Its two
watch helpers are therefore exercised through `db.watch` directly, with the
same arguments the module passes.
"""
from __future__ import annotations

import os
import sys
import threading
import time
import uuid
from unittest.mock import MagicMock

import pytest

PG_TEST_DSN = os.environ.get("PG_TEST_DSN")
requires_pg = pytest.mark.skipif(
    not PG_TEST_DSN, reason="PG_TEST_DSN not set (run: ./scripts/dev_pg.sh up)")

# server.py / instance.py pull in optional runtime deps at import time.
for _mod in ("socketio", "waitress", "docker"):
    sys.modules.setdefault(_mod, MagicMock())


@pytest.fixture
def pg_schema():
    """A throwaway Postgres schema with the control-plane tables present."""
    if not PG_TEST_DSN:
        pytest.skip("PG_TEST_DSN not set")
    from db import pool as dbpool
    from db import schema as dbschema
    name = "t_" + uuid.uuid4().hex[:16]
    dbpool.close_pool()
    os.environ["PG_DSN"] = PG_TEST_DSN
    os.environ.pop("PG_SEARCH_PATH", None)
    with dbpool.connection(autocommit=True) as conn:
        conn.execute('CREATE SCHEMA IF NOT EXISTS "%s"' % name)
    dbpool.close_pool()
    os.environ["PG_SEARCH_PATH"] = name
    try:
        dbschema.ensure_schema(tables=["Config", "EngineControl", "Instances"])
        yield name
    finally:
        os.environ.pop("PG_SEARCH_PATH", None)
        dbpool.close_pool()
        with dbpool.connection(autocommit=True) as conn:
            conn.execute('DROP SCHEMA IF EXISTS "%s" CASCADE' % name)
        dbpool.close_pool()


def _wait_for(predicate, timeout=15.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


@requires_pg
def test_engine_control_watch_delivers_initial_then_change(pg_schema):
    import server
    from db import store

    store.insert("EngineControl", {"id": "price_engine", "command": "idle"})
    seen, lock = [], threading.Lock()

    def _capture(change, conn):
        assert conn is None          # handlers keep their (change, conn) arity
        with lock:
            seen.append(change)

    w = server._watch_engine_control(_capture, label="engine-control-test")
    w.start()
    try:
        assert _wait_for(lambda: len(seen) >= 1)
        assert seen[0] == {"old_val": None,
                           "new_val": {"id": "price_engine", "command": "idle"}}
        store.update("EngineControl", "price_engine", {"command": "stop"})
        assert _wait_for(lambda: len(seen) >= 2)
        assert seen[1]["old_val"]["command"] == "idle"
        assert seen[1]["new_val"]["command"] == "stop"
    finally:
        w.stop()


@requires_pg
def test_config_pings_row_watch_reports_deletion(pg_schema):
    """priceBroker._watch_pings' shape: a deleted row arrives as new_val=None
    with the cached document as old_val."""
    from db import store, watch

    store.insert("Config", {"id": "Pings", "ping": 1})
    seen, lock = [], threading.Lock()

    def _capture(change):
        with lock:
            seen.append(change)

    w = watch.watch_row("Config", "Pings", _capture,
                        label="pricebroker-pings", include_initial=True)
    w.start()
    try:
        assert _wait_for(lambda: len(seen) >= 1)
        assert seen[0]["old_val"] is None
        assert seen[0]["new_val"]["ping"] == 1
        store.delete("Config", "Pings")
        assert _wait_for(lambda: len(seen) >= 2)
        assert seen[-1]["new_val"] is None
        assert seen[-1]["old_val"]["ping"] == 1
    finally:
        w.stop()


@requires_pg
def test_instance_feed_delivers_runcommand_flip(pg_schema):
    """instance.terminate_thread_on_command iterates watch.feed('Instances',
    row_id=...); the change dicts keep the ReQL shape its body reads."""
    from db import store, watch

    store.insert("Instances", {"id": "42", "runCommand": True})
    seen, lock = [], threading.Lock()
    stop = threading.Event()

    def _drain():
        for change in watch.feed("Instances", row_id="42",
                                 poll_interval=0.25):
            with lock:
                seen.append(change)
            if stop.is_set():
                return

    t = threading.Thread(target=_drain, daemon=True)
    t.start()
    try:
        assert _wait_for(lambda: len(seen) >= 1)
        assert seen[0]["new_val"]["runCommand"] is True
        store.update("Instances", "42", {"runCommand": False})
        assert _wait_for(lambda: len(seen) >= 2)
        assert seen[-1]["new_val"]["runCommand"] is False
        assert seen[-1]["old_val"]["runCommand"] is True
    finally:
        stop.set()
        t.join(timeout=5)


@requires_pg
def test_engine_control_ensure_and_roundtrip(pg_schema):
    """engine_control's ensure/get/update trio off ReQL, `conn` ignored."""
    import engine_control as ec

    ec.ensure_engine_control_table(None)
    doc = ec.get_engine_doc(None, ec.ENGINE_ID_PRICE)
    assert doc is not None and doc["id"] == ec.ENGINE_ID_PRICE
    assert doc["running"] is True

    ec.update_engine_doc(None, ec.ENGINE_ID_PRICE, {"running": False})
    assert ec.get_engine_doc(None, ec.ENGINE_ID_PRICE)["running"] is False
    # deep merge, not replace: sibling keys survive
    assert ec.get_engine_doc(None, ec.ENGINE_ID_PRICE)["run_price_service"] is True


@requires_pg
def test_server_ensure_db_and_tables_seeds_config(pg_schema):
    import server
    from db import store

    server.ensure_db_and_tables(None)
    assert store.get("Config", "Pings") == {
        "id": "Pings", "corePing": None, "coreResponse": None}
    assert store.get("Config", "Config")["runPriceService"] is True
    # idempotent: a second call neither duplicates nor overwrites
    store.update("Config", "Pings", {"corePing": "x"})
    server.ensure_db_and_tables(None)
    assert store.get("Config", "Pings")["corePing"] == "x"


@requires_pg
def test_price_history_insert_lands_in_its_partition(pg_schema):
    """priceBroker._insert_price_history writes the compound-PK PriceHistory
    row through hand-written SQL (store.insert only speaks (id, doc))."""
    from db import schema as dbschema
    from db import store

    dbschema.ensure_schema(tables=["PriceHistory"])
    import datetime as _dt
    dbschema.ensure_partitions(
        "PriceHistory",
        lo=_dt.datetime(2026, 8, 1, tzinfo=_dt.timezone.utc),
        hi=_dt.datetime(2026, 9, 1, tzinfo=_dt.timezone.utc))
    # Import the helper without executing priceBroker's module-level poll loop.
    import json as _json

    def _insert_price_history(ticker_id, price, storage_ts):
        row_id = str(uuid.uuid4())
        doc = {"id": row_id, "ticker": ticker_id, "price": price,
               "timestamp": storage_ts, "type": "minute"}
        store.sql(
            'INSERT INTO "PriceHistory" (ticker, ts, id, doc) '
            'VALUES (%s, %s::timestamptz, %s, %s::jsonb) ON CONFLICT DO NOTHING',
            (ticker_id, storage_ts, row_id, _json.dumps(doc)))
        return row_id

    src = open(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "priceBroker.py")).read()
    assert 'INSERT INTO "PriceHistory" (ticker, ts, id, doc)' in src

    ts = "2026-08-22T14:30:00.000Z"
    rid = _insert_price_history("T.AAPL", 1.25, ts)
    rows = store.sql('SELECT doc FROM "PriceHistory" WHERE id = %s', (rid,))
    assert len(rows) == 1
    assert rows[0]["doc"]["ticker"] == "T.AAPL"
    assert rows[0]["doc"]["price"] == 1.25
    assert rows[0]["doc"]["timestamp"] == ts
    assert rows[0]["doc"]["type"] == "minute"
