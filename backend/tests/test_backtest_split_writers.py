"""The four BacktestResults writers, against a real split schema."""
import datetime as dt
import os

import pytest

import backtest_result_store as brs
from db import schema, store

pytestmark = pytest.mark.skipif(
    not os.environ.get("PG_TEST_DSN"),
    reason="PG_TEST_DSN not set (run: ./scripts/dev_pg.sh up)")


@pytest.fixture
def split_schema(pg_schema):
    schema.ensure_schema(tables=["BacktestResults", "BacktestSteps",
                                 "BacktestProgress", "BacktestInstances"])
    return pg_schema


STUB = {
    "id": 700001, "backtest_id": 700001, "status": "running", "progress": 0,
    "timestamp": "2026-08-22T03:00:00Z", "instance_id": None,
    "strategy_id": None, "pnl": None, "pnl_percent": None,
    "start_date": "2026-03-01 00:00:00", "end_date": "2026-04-01 00:00:00",
    "tickers": ["AACI", "AA"], "time_elapsed_seconds": None,
    "portfolio_value_history": [], "backtest_trades": [],
    "backtest_prices": [], "logs": [],
}


def test_write_stub_stores_no_arrays_in_the_metadata_row(split_schema):
    brs.write_stub(dict(STUB))
    row = store.get("BacktestResults", 700001)
    for key in ("portfolio_value_history", "backtest_trades",
                "backtest_prices", "logs"):
        assert key not in row


def test_write_stub_populates_the_hot_progress_row(split_schema):
    brs.write_stub(dict(STUB))
    assert brs.read_progress(700001) == {"status": "running", "progress": 0,
                                         "time_elapsed_seconds": None}


def test_the_assembled_stub_matches_the_legacy_document(split_schema):
    from db.json import canonical
    brs.write_stub(dict(STUB))
    assert canonical(brs.assemble(700001)) == canonical(STUB)


def test_write_stub_is_idempotent(split_schema):
    brs.write_stub(dict(STUB))
    brs.write_stub(dict(STUB))
    assert store.count("BacktestResults") == 1


def test_write_stub_twice_does_not_reset_steps_a_broker_already_wrote(split_schema):
    brs.write_stub(dict(STUB))
    brs.append_steps(700001, "decision", [{"n": 1}, {"n": 2}], start_seq=0)
    brs.write_stub(dict(STUB))          # backtest_engine.py:1126-1127
    assert brs.watermarks(700001) == {"decision": 2}
    assert brs.assemble(700001)["backtest_decisions"] == [{"n": 1}, {"n": 2}]


def test_write_stub_with_an_error_status_keeps_the_error_in_the_metadata(split_schema):
    stub = dict(STUB)
    stub.update({"status": "error", "progress": 100.0,
                 "error": "no strategy linked to instance"})
    brs.write_stub(stub)
    assert store.get("BacktestResults", 700001)["error"].startswith("no strategy")
    assert brs.read_progress(700001)["status"] == "error"


# ---- the duplicate-run guard (broker.py:12029) ---------------------------
#
# The guard used to read status and _last_active off the multi-MB document.
# Both now live on the hot row, so it reads brs.active_run_age().

def _iso_ago(seconds):
    return (dt.datetime.now(dt.timezone.utc)
            - dt.timedelta(seconds=seconds)).isoformat()


def test_active_run_age_is_none_when_there_is_no_row(split_schema):
    assert brs.active_run_age(700001) is None


def test_active_run_age_is_none_when_the_run_is_not_running(split_schema):
    stub = dict(STUB)
    stub["status"] = "finished"
    brs.write_stub(stub)
    brs.write_progress(700001, {"_last_active": _iso_ago(5)})
    assert brs.active_run_age(700001) is None


def test_active_run_age_is_none_when_running_without_a_heartbeat(split_schema):
    """No _last_active means no live-run evidence: the legacy guard fell
    through and overwrote the row rather than exiting."""
    brs.write_stub(dict(STUB))
    assert brs.active_run_age(700001) is None


def test_active_run_age_sees_a_fresh_heartbeat(split_schema):
    brs.write_stub(dict(STUB))
    brs.write_progress(700001, {"_last_active": _iso_ago(30)})
    age = brs.active_run_age(700001)
    assert age is not None and 25 <= age <= 90


def test_active_run_age_reports_a_stale_heartbeat_rather_than_hiding_it(split_schema):
    brs.write_stub(dict(STUB))
    brs.write_progress(700001, {"_last_active": _iso_ago(600)})
    assert brs.active_run_age(700001) > 120


def test_active_run_age_treats_an_unparseable_heartbeat_as_stale(split_schema):
    """The legacy guard used 9999 on a parse failure, i.e. 'stale, overwrite'
    -- never 'a duplicate is live'."""
    brs.write_stub(dict(STUB))
    brs.write_progress(700001, {"_last_active": "not-a-timestamp"})
    assert brs.active_run_age(700001) == 9999


def test_active_run_age_accepts_the_legacy_z_suffixed_heartbeat(split_schema):
    brs.write_stub(dict(STUB))
    brs.write_progress(
        700001,
        {"_last_active": dt.datetime.now(dt.timezone.utc).replace(
            tzinfo=None).isoformat() + "Z"})
    age = brs.active_run_age(700001)
    assert age is not None and age < 120


def test_active_run_age_ignores_a_stale_running_status_in_the_document(split_schema):
    """R4: the doc's status is advisory after the split; the hot row decides."""
    brs.write_stub(dict(STUB))                      # doc status == running
    brs.write_progress(700001, {"status": "finished",
                                "_last_active": _iso_ago(5)})
    assert brs.active_run_age(700001) is None
