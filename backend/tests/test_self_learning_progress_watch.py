import os
import threading
import time

import pytest

import backtest_result_store as brs
from db import schema

pytestmark = pytest.mark.skipif(
    not os.environ.get("PG_TEST_DSN"),
    reason="PG_TEST_DSN not set (run: ./scripts/dev_pg.sh up)")


def _wait_for(predicate, timeout=15.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


@pytest.fixture
def progress_schema(pg_schema):
    schema.ensure_schema(tables=["BacktestResults", "BacktestSteps",
                                 "BacktestProgress"])
    return pg_schema


def test_watch_progress_rows_delivers_id_and_status(progress_schema):
    import self_learning_progress as slp        # the new adapter module
    seen = []
    w = slp.watch_progress_rows(lambda c: seen.append(c), label="test")
    w.start()
    try:
        brs.write_progress(820001, {"status": "running", "progress": 0})
        assert _wait_for(lambda: any(
            (c["new_val"] or {}).get("id") == "820001" for c in seen))
        change = [c for c in seen if (c["new_val"] or {}).get("id") == "820001"][0]
        assert set(change["new_val"]) == {"id", "status"}
        assert change["new_val"]["status"] == "running"
    finally:
        w.stop()


def test_watch_progress_rows_never_carries_the_document(progress_schema):
    """The whole point of the old server-side pluck: new_val must not be a
    5-13 MB document."""
    import self_learning_progress as slp
    brs.write_split({"id": 820002, "status": "running", "progress": 0,
                     "timestamp": "2026-08-22T00:00:00",
                     "strategy_schema": {"blob": "z" * 50000}}, final=False)
    seen = []
    w = slp.watch_progress_rows(lambda c: seen.append(c), label="test")
    w.start()
    try:
        assert _wait_for(lambda: seen)
        for change in seen:
            assert "strategy_schema" not in (change["new_val"] or {})
    finally:
        w.stop()


def test_include_initial_replays_existing_rows(progress_schema):
    import self_learning_progress as slp
    brs.write_progress(820003, {"status": "finished", "progress": 100})
    seen = []
    w = slp.watch_progress_rows(lambda c: seen.append(c), label="test")
    w.start()
    try:
        assert _wait_for(lambda: any(
            (c["new_val"] or {}).get("id") == "820003" for c in seen))
    finally:
        w.stop()


def test_progress_feed_delivers_the_two_key_change_the_engine_consumes(
        progress_schema):
    """The path self_learning_engine.py's _open_feed actually runs.

    watch_progress_rows is the Watcher form; the engine consumes the generator
    form, because run_reconnecting_changefeed's open_feed(conn) wants an
    iterator. Nothing exercised it until this test, so a composition break
    between watch.feed and the projection would have shipped unnoticed.
    """
    import self_learning_progress as slp
    seen, stop = [], threading.Event()

    def _drain():
        for change in slp.progress_feed(
                should_continue=lambda: not stop.is_set()):
            seen.append(change)

    t = threading.Thread(target=_drain, daemon=True)
    t.start()
    try:
        brs.write_progress(820006, {"status": "running", "progress": 0})
        assert _wait_for(lambda: any(
            (c["new_val"] or {}).get("id") == "820006" for c in seen))
        change = [c for c in seen
                  if (c["new_val"] or {}).get("id") == "820006"][0]
        assert set(change["new_val"]) == {"id", "status"}
        assert change["new_val"]["status"] == "running"
        # The projection is what keeps the row off the handler. fields= was
        # dropped from progress_feed, so _id_and_status is now the ONLY thing
        # standing between the handler and the whole row.
        assert "payload" not in change["new_val"]
    finally:
        stop.set()
        t.join(timeout=15)
        assert not t.is_alive(), "the feed generator must shut its watcher down"
