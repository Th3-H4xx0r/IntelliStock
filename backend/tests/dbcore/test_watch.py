"""LISTEN/NOTIFY watchers (spec 5.1).

Every clause of the behaviour contract gets a test: re-read on start, re-read
on EVERY reconnect (the fix for the 8 sites that lose events today and the 3
with no reconnect at all), old_val out of the per-id cache, deletion as
new_val=None, squash coalescing, the poll backstop, handler-error survival,
field projection, filtering, feed(), and stop().

Timing rule for this file: never assert on a bare sleep -- poll with a
deadline. The whole file must stay well under 90s.
"""
import threading
import time

import pytest

from db import schema, store, watch

from .conftest import requires_pg


def _collect():
    changes, lock = [], threading.Lock()

    def on_change(c):
        with lock:
            changes.append(c)

    return changes, on_change


def _wait_for(predicate, timeout=15.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def _wait_seeded(w, timeout=15.0):
    """Block until the watcher has taken its first snapshot.

    An include_initial=False watcher swallows whatever it finds in its first
    read, so a write that races the seed is invisible BY DESIGN. Every test
    below that writes after start() must wait for the seed first, or it is
    testing the race and not the contract.
    """
    assert _wait_for(lambda: w._seeded, timeout), "watcher never seeded"
    return True


@pytest.fixture
def watched(pg_schema):
    schema.ensure_schema(tables=["Instances", "EngineControl", "BacktestInstances"])
    return pg_schema


@requires_pg
def test_watch_row_emits_current_state_on_start(watched):
    store.insert("Instances", {"id": 1, "strategy_id": 179})
    changes, on_change = _collect()
    w = watch.watch_row("Instances", 1, on_change, label="t")
    w.start()
    try:
        assert _wait_for(lambda: len(changes) >= 1)
        assert changes[0] == {"old_val": None,
                              "new_val": {"id": 1, "strategy_id": 179}}
    finally:
        w.stop()


@requires_pg
def test_watch_row_include_initial_false_seeds_the_cache_silently(watched):
    store.insert("Instances", {"id": 1, "n": 1})
    changes, on_change = _collect()
    w = watch.watch_row("Instances", 1, on_change, label="t",
                        include_initial=False, poll_interval=0.5)
    w.start()
    try:
        # The seed must be silent: give the watcher a couple of poll ticks and
        # assert nothing was delivered.
        assert not _wait_for(lambda: bool(changes), timeout=2.0)
        store.update("Instances", 1, {"n": 2})
        assert _wait_for(lambda: len(changes) >= 1)
        assert changes[0]["old_val"]["n"] == 1
        assert changes[0]["new_val"]["n"] == 2
    finally:
        w.stop()


@requires_pg
def test_old_val_comes_from_the_cache(watched):
    """broker.py:5730-5732 diffs strategy_id and crypto_config across
    old_val/new_val -- the BUG #6 class."""
    store.insert("Instances", {"id": 1, "strategy_id": 1,
                               "crypto_config": {"strategy": "meanrev"}})
    changes, on_change = _collect()
    w = watch.watch_row("Instances", 1, on_change, label="t",
                        include_initial=False, poll_interval=0.5)
    w.start()
    try:
        _wait_seeded(w)
        store.update("Instances", 1, {"crypto_config": {"strategy": "adaptive"}})
        assert _wait_for(lambda: len(changes) >= 1)
        c = changes[-1]
        assert c["old_val"]["crypto_config"]["strategy"] == "meanrev"
        assert c["new_val"]["crypto_config"]["strategy"] == "adaptive"
    finally:
        w.stop()


@requires_pg
def test_deletion_emits_new_val_none(watched):
    """broker.py:5833 reads new_val is None as 'queue row deleted'."""
    store.insert("BacktestInstances", {"id": 5, "run": True})
    changes, on_change = _collect()
    w = watch.watch_row("BacktestInstances", 5, on_change, label="t",
                        include_initial=False, poll_interval=0.5)
    w.start()
    try:
        _wait_seeded(w)
        store.delete("BacktestInstances", 5)
        assert _wait_for(lambda: any(c["new_val"] is None for c in changes))
        c = [c for c in changes if c["new_val"] is None][0]
        assert c["old_val"]["run"] is True
    finally:
        w.stop()


@requires_pg
def test_reconnect_re_reads_and_emits_what_moved_while_blind(watched):
    """The strict upgrade over today's behaviour: 8 sites lose events on
    reconnect and 3 have no reconnect at all.

    The watcher's LISTEN backend is terminated from another session and the row
    changes while the watcher is blind; the change must still be delivered
    after the reconnect, because reconnect re-reads.
    """
    store.insert("Instances", {"id": 1, "n": 1})
    changes, on_change = _collect()
    # A LONG poll interval on purpose: the poll backstop re-reads through the
    # pool, so with a short one it -- not the reconnect -- would deliver this
    # change and the test would prove nothing. 30s is longer than the assert
    # window below, so only a re-read on reconnect can pass this.
    w = watch.watch_row("Instances", 1, on_change, label="t",
                        include_initial=False, poll_interval=30.0)
    w.start()
    try:
        _wait_seeded(w)
        # Let it seed and settle into LISTEN before pulling the plug.
        assert _wait_for(lambda: bool(store.sql(
            "SELECT 1 AS ok FROM pg_stat_activity "
            "WHERE query LIKE 'LISTEN %%' AND pid <> pg_backend_pid()")),
            timeout=10)
        store.sql("SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                  "WHERE query LIKE 'LISTEN %%' AND pid <> pg_backend_pid()")
        store.update("Instances", 1, {"n": 99})
        assert _wait_for(lambda: any((c["new_val"] or {}).get("n") == 99
                                     for c in changes), timeout=20)
    finally:
        w.stop()


@requires_pg
def test_poll_backstop_catches_a_change_with_notify_suppressed(watched):
    store.sql('DROP TRIGGER IF EXISTS "Instances_notify" ON "Instances"')
    store.insert("Instances", {"id": 1, "n": 1})
    changes, on_change = _collect()
    w = watch.watch_row("Instances", 1, on_change, label="t",
                        include_initial=False, poll_interval=0.5)
    w.start()
    try:
        _wait_seeded(w)
        store.update("Instances", 1, {"n": 2})
        # No NOTIFY can fire -- only the poll can deliver this.
        assert _wait_for(lambda: any((c["new_val"] or {}).get("n") == 2
                                     for c in changes), timeout=10)
    finally:
        w.stop()


@requires_pg
def test_squash_coalesces_same_id_changes(watched):
    store.insert("Instances", {"id": 1, "n": 0})
    changes, on_change = _collect()
    w = watch.watch_row("Instances", 1, on_change, label="t",
                        include_initial=False, squash=True, squash_window=1.0,
                        poll_interval=0.5)
    w.start()
    try:
        _wait_seeded(w)
        for n in range(1, 6):
            store.update("Instances", 1, {"n": n})
        assert _wait_for(lambda: any((c["new_val"] or {}).get("n") == 5
                                     for c in changes), timeout=15)
        assert len(changes) < 5, "squash must coalesce: %r" % changes
        assert changes[-1]["old_val"]["n"] == 0     # oldest old_val kept
    finally:
        w.stop()


@requires_pg
def test_watch_table_emits_every_row_on_start(watched):
    for i in (1, 2):
        store.insert("EngineControl", {"id": "e%d" % i, "running": False})
    changes, on_change = _collect()
    w = watch.watch_table("EngineControl", on_change, label="t")
    w.start()
    try:
        assert _wait_for(lambda: len(changes) >= 2)
        assert {c["new_val"]["id"] for c in changes[:2]} == {"e1", "e2"}
        assert all(c["old_val"] is None for c in changes[:2])
    finally:
        w.stop()


@requires_pg
def test_watch_table_fields_projects_old_and_new(watched):
    """self_learning_engine.py:534's server-side pluck replacement.

    BacktestProgress is the one table with no ``doc`` column (payload jsonb
    plus generated columns), so this also covers watch.py's SELECT * path.
    """
    schema.ensure_schema(tables=["BacktestProgress"])
    store.sql('INSERT INTO "BacktestProgress" (id, payload) '
              """VALUES ('1', '{"status":"running","progress":0.5}'::jsonb)""")
    changes, on_change = _collect()
    w = watch.watch_table("BacktestProgress", on_change, label="t",
                          fields=("id", "status"), poll_interval=0.5)
    w.start()
    try:
        assert _wait_for(lambda: len(changes) >= 1)
        assert set(changes[0]["new_val"]) == {"id", "status"}
        assert changes[0]["new_val"]["status"] == "running"
        assert changes[0]["old_val"] is None
        store.sql("""UPDATE "BacktestProgress" SET payload = """
                  """'{"status":"finished"}'::jsonb WHERE id = '1'""")
        assert _wait_for(lambda: len(changes) >= 2, timeout=10)
        latest = changes[-1]
        assert set(latest["old_val"]) == {"id", "status"}
        assert latest["old_val"]["status"] == "running"
        assert latest["new_val"]["status"] == "finished"
    finally:
        w.stop()


@requires_pg
def test_watch_filter_only_delivers_matching_rows(watched):
    schema.ensure_schema(tables=["KalshiBacktests"])
    changes, on_change = _collect()
    w = watch.watch_filter("KalshiBacktests", {"status": "pending"}, on_change,
                           label="t", include_initial=False, poll_interval=0.5)
    w.start()
    try:
        _wait_seeded(w)
        store.insert("KalshiBacktests", {"id": "k1", "status": "running"})
        store.insert("KalshiBacktests", {"id": "k2", "status": "pending"})
        assert _wait_for(lambda: len(changes) >= 1)
        time.sleep(1.0)                      # a non-matching row must not land
        assert [c["new_val"]["id"] for c in changes] == ["k2"]
    finally:
        w.stop()


@requires_pg
def test_a_raising_handler_never_kills_the_watcher(watched):
    store.insert("Instances", {"id": 1, "n": 0})
    seen, lock = [], threading.Lock()

    def boom(change):
        with lock:
            seen.append(change)
        raise RuntimeError("handler blew up")

    w = watch.watch_row("Instances", 1, boom, label="t", include_initial=False,
                        poll_interval=0.5)
    w.start()
    try:
        _wait_seeded(w)
        store.update("Instances", 1, {"n": 1})
        assert _wait_for(lambda: len(seen) >= 1)
        store.update("Instances", 1, {"n": 2})
        assert _wait_for(lambda: len(seen) >= 2)
        assert w.is_alive()
    finally:
        w.stop()


@requires_pg
def test_feed_yields_the_same_change_dicts(watched):
    store.insert("Instances", {"id": 1, "n": 1})
    gen = watch.feed("Instances", row_id=1, include_initial=True,
                     poll_interval=0.5)
    try:
        first = next(gen)
        assert first == {"old_val": None, "new_val": {"id": 1, "n": 1}}
    finally:
        gen.close()


@requires_pg
def test_stop_joins_the_thread(watched):
    w = watch.watch_table("EngineControl", lambda c: None, label="t",
                          poll_interval=0.5)
    w.start()
    assert w.is_alive()
    w.stop()
    assert not w.is_alive()
