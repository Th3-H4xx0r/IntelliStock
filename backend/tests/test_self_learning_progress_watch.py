import os
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
