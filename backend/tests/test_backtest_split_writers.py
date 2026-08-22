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


# ---- the heartbeat writer (broker.py:12174) ------------------------------

def test_heartbeat_updates_only_the_hot_row_scalars(split_schema):
    brs.write_stub(dict(STUB))
    before = store.get("BacktestResults", 700001)
    brs.heartbeat(700001, last_active="2026-08-22T03:15:00+00:00",
                  elapsed_seconds=900)
    assert store.get("BacktestResults", 700001) == before   # metadata untouched
    prog = brs.read_progress(700001)
    assert prog["time_elapsed_seconds"] == 900
    assert prog["_last_active"] == "2026-08-22T03:15:00+00:00"


def test_heartbeat_appends_only_new_log_lines(split_schema):
    brs.write_stub(dict(STUB))
    mark = brs.heartbeat(700001, last_active="t1", new_log_lines=["a", "b"],
                         log_seq=0)
    assert mark == 2
    mark = brs.heartbeat(700001, last_active="t2", new_log_lines=["c"],
                         log_seq=mark)
    assert mark == 3
    assert brs.assemble(700001)["logs"] == ["a", "b", "c"]


def test_heartbeat_never_rewrites_the_whole_log_list(split_schema):
    """The legacy heartbeat re-sent 500 lines every 15s. Appending 1 line
    must write exactly 1 step row."""
    brs.write_stub(dict(STUB))
    brs.heartbeat(700001, last_active="t1",
                  new_log_lines=["l%d" % i for i in range(500)], log_seq=0)
    n_before = store.sql('SELECT count(*) AS n FROM "BacktestSteps" '
                         "WHERE backtest_id='700001' AND kind='log'")[0]["n"]
    brs.heartbeat(700001, last_active="t2", new_log_lines=["l500"], log_seq=500)
    n_after = store.sql('SELECT count(*) AS n FROM "BacktestSteps" '
                        "WHERE backtest_id='700001' AND kind='log'")[0]["n"]
    assert n_after - n_before == 1


def test_watermarks_let_a_reconnecting_writer_continue(split_schema):
    """R6: the broker re-seeds _steps_written from watermarks() itself; there
    is no resume_watermarks alias."""
    brs.write_stub(dict(STUB))
    brs.heartbeat(700001, last_active="t1", new_log_lines=["a", "b", "c"],
                  log_seq=0)
    # Simulate the writer losing its in-process watermark on reconnect.
    marks = brs.watermarks(700001)
    assert marks["log"] == 3
    brs.heartbeat(700001, last_active="t2", new_log_lines=["d"],
                  log_seq=marks["log"])
    assert brs.assemble(700001)["logs"] == ["a", "b", "c", "d"]


def test_the_read_still_tails_500_lines(split_schema):
    brs.write_stub(dict(STUB))
    lines = ["l%d" % i for i in range(900)]
    brs.heartbeat(700001, last_active="t1", new_log_lines=lines, log_seq=0)
    assert brs.assemble(700001)["logs"] == lines[-500:]


def test_heartbeat_with_no_new_lines_returns_the_watermark_unchanged(split_schema):
    brs.write_stub(dict(STUB))
    assert brs.heartbeat(700001, last_active="t1", log_seq=7) == 7


def test_the_logger_counts_lines_it_has_trimmed_away():
    """intellistock_logger.py:210-215 trims the buffer FIFO to max_lines, so
    len(buffer) saturates at 500 and cannot be a watermark. The emitted
    counter must keep climbing."""
    from intellistock_logger import intellistock_logger as logger
    buf = []
    logger.set_backtest_log_buffer(buf, max_lines=5)
    try:
        for i in range(12):
            logger.log("line %d" % i, "white")
        assert len(buf) == 5
        assert logger.context_log_lines_emitted("backtest") == 12
    finally:
        logger.clear_backtest_log_buffer()
    assert logger.context_log_lines_emitted("backtest") == 0


def test_the_heartbeat_slice_survives_a_buffer_that_overflowed(split_schema):
    """The broker's slicing arithmetic (broker.py:_backtest_heartbeat), run
    against a real FIFO-trimmed buffer. More lines were emitted since the last
    tick than the buffer holds, so the overflow is gone; what survives must be
    appended once, at seqs aligned with the emitted index."""
    from intellistock_logger import intellistock_logger as logger
    brs.write_stub(dict(STUB))
    buf, steps = [], {}

    def tick():
        start_seq = steps.get("log", 0)
        buffered = list(buf)
        emitted = logger.context_log_lines_emitted("backtest")
        n_new = min(max(emitted - start_seq, 0), len(buffered))
        new_lines = []
        if n_new:
            new_lines = buffered[len(buffered) - n_new:]
            start_seq = emitted - n_new
        steps["log"] = brs.heartbeat(700001, last_active="t",
                                     new_log_lines=new_lines, log_seq=start_seq)

    logger.set_backtest_log_buffer(buf, max_lines=5)
    try:
        for i in range(3):
            logger.log("a%d" % i, "white")
        tick()
        for i in range(20):                     # 20 emitted, only 5 survive
            logger.log("b%d" % i, "white")
        tick()
        logger.log("c0", "white")
        tick()
    finally:
        logger.clear_backtest_log_buffer()

    assert steps["log"] == 24                   # aligned with emitted, not row count
    logs = brs.assemble(700001)["logs"]
    assert len(logs) == len(set(logs))          # nothing duplicated
    assert [l.split("] ")[-1] for l in logs] == (
        ["a0", "a1", "a2"] + ["b%d" % i for i in range(15, 20)] + ["c0"])


# ---- the progress writer (broker.py:17823) -------------------------------

def test_progress_tick_writes_scalars_to_the_hot_row(split_schema):
    brs.write_stub(dict(STUB))
    seqs = {}
    brs.write_progress_tick(
        700001,
        hot={"status": "running", "progress": 42.5,
             "time_elapsed_seconds": 812, "_last_active": "t"},
        metadata={"pnl": 123.45, "pnl_percent": 1.2345,
                  "timestamp": "2026-08-22T03:37:00", "tickers": ["AACI"]},
        appended={}, seqs=seqs)
    assert brs.read_progress(700001)["progress"] == 42.5
    row = store.get("BacktestResults", 700001)
    assert row["pnl"] == 123.45 and row["tickers"] == ["AACI"]


def test_progress_tick_appends_only_the_new_entries(split_schema):
    brs.write_stub(dict(STUB))
    seqs = {}
    decisions = [{"n": i} for i in range(10)]
    brs.write_progress_tick(700001, hot={"status": "running", "progress": 10},
                            metadata={}, appended={"decision": decisions[:4]},
                            seqs=seqs)
    assert seqs["decision"] == 4
    brs.write_progress_tick(700001, hot={"status": "running", "progress": 20},
                            metadata={}, appended={"decision": decisions},
                            seqs=seqs)
    assert seqs["decision"] == 10
    n = store.sql('SELECT count(*) AS n FROM "BacktestSteps" '
                  "WHERE backtest_id='700001' AND kind='decision'")[0]["n"]
    assert n == 10          # 10 rows written, not 14
    assert brs.assemble(700001)["backtest_decisions"] == decisions


def test_progress_tick_metadata_is_a_deep_merge_not_a_replace(split_schema):
    stub = dict(STUB)
    stub["strategy_schema"] = {"name": "gna", "config": {"a": 1, "b": 2}}
    brs.write_stub(stub)
    brs.write_progress_tick(700001, hot={}, metadata={"pnl": 1.0},
                            appended={}, seqs={})
    row = store.get("BacktestResults", 700001)
    assert row["strategy_schema"]["config"] == {"a": 1, "b": 2}
    assert row["pnl"] == 1.0


def test_progress_tick_refuses_to_write_the_log_kind(split_schema):
    """Only the heartbeat writes logs: the log buffer is trimmed FIFO, so
    exactly one writer may own that watermark."""
    brs.write_stub(dict(STUB))
    with pytest.raises(Exception) as excinfo:
        brs.write_progress_tick(700001, hot={}, metadata={},
                                appended={"log": ["a"]}, seqs={})
    assert "log" in str(excinfo.value)
    assert brs.assemble(700001)["logs"] == []


def test_the_assembled_document_matches_a_legacy_progress_write(split_schema):
    """What the legacy writer would have stored, from the same sources."""
    from broker_snapshot_helpers import downsample_history
    from db.json import canonical
    brs.write_stub(dict(STUB))
    trades = [{"n": i} for i in range(1500)]
    history = [{"t": i, "v": float(i)} for i in range(5000)]
    decisions = [{"d": i} for i in range(300)]
    refusals = [{"r": i} for i in range(5)]
    seqs = {}
    brs.write_progress_tick(
        700001,
        hot={"status": "running", "progress": 50.0,
             "time_elapsed_seconds": 100, "_last_active": "t"},
        metadata={"backtest_id": 700001, "pnl": 1.0, "pnl_percent": 0.01,
                  "timestamp": "2026-08-22T03:37:00", "tickers": ["AACI"]},
        appended={"trade": trades, "pv": history,
                  "decision": decisions, "refusal": refusals},
        seqs=seqs)
    got = brs.assemble(700001)
    legacy = dict(STUB)
    legacy.update({
        "backtest_id": 700001, "progress": 50.0, "pnl": 1.0,
        "pnl_percent": 0.01, "status": "running",
        "timestamp": "2026-08-22T03:37:00", "_last_active": "t",
        "tickers": ["AACI"], "time_elapsed_seconds": 100,
        "backtest_trades": trades[-1000:],
        "portfolio_value_history": list(downsample_history(history, 3000)),
        # logs stay [] here: the heartbeat owns the log stream, so a progress
        # tick never touches them.
        "backtest_decisions": decisions, "backtest_refusals": refusals,
    })
    assert canonical(got) == canonical(dict(sorted(legacy.items())))


def test_progress_tick_with_a_shrinking_source_does_not_rewind(split_schema):
    brs.write_stub(dict(STUB))
    seqs = {"decision": 4}
    brs.write_progress_tick(700001, hot={}, metadata={},
                            appended={"decision": [{"n": 0}]}, seqs=seqs)
    assert seqs["decision"] == 4
    assert brs.watermarks(700001) == {}


# ---- the difficulty seam (backtest_engine.py:1135 -> broker.py:12037) ----

def test_difficulty_written_by_the_engine_is_readable_by_the_broker(split_schema):
    """The engine computes difficulty once, right after the stub write; the
    broker reads it back with store.get to carry it onto the running stub
    (broker.py:12037/12100) and into the Discord embed (broker.py:17909).
    One writer, two readers -- all three on Postgres."""
    brs.write_stub(dict(STUB))
    brs.write_difficulty(700001, 7.5)
    existing_result = store.get("BacktestResults", 700001)   # broker.py:12037
    assert existing_result["difficulty"] == 7.5


def test_writing_difficulty_does_not_clobber_the_stub(split_schema):
    """It is a deep-merge patch: the stub the engine wrote a line earlier has
    to survive it."""
    brs.write_stub(dict(STUB))
    brs.write_difficulty(700001, 3.25)
    row = store.get("BacktestResults", 700001)
    assert row["tickers"] == ["AACI", "AA"]
    assert row["start_date"] == "2026-03-01 00:00:00"
    assert brs.read_progress(700001)["status"] == "running"
    from db.json import canonical
    expected = dict(STUB)
    expected["difficulty"] = 3.25
    assert canonical(brs.assemble(700001)) == canonical(expected)


def test_a_null_difficulty_round_trips_as_none(split_schema):
    """_backtest_avg_difficulty returns None when nothing scores; the readers
    test `is not None`, so None must not become a missing key or a 0."""
    brs.write_stub(dict(STUB))
    brs.write_difficulty(700001, None)
    assert store.get("BacktestResults", 700001)["difficulty"] is None


def test_the_three_table_bootstrap_self_heals_an_empty_database(pg_schema):
    """What the deleted table_create('BacktestResults') blocks used to do.
    broker.py and backtest_engine.py call ensure_schema with exactly this
    table list at startup; on a fresh deploy the store.get that follows it
    would otherwise raise UndefinedTable inside a try/finally with no except.
    """
    tables = ["BacktestResults", "BacktestSteps", "BacktestProgress"]
    assert schema.ensure_schema(tables=tables)      # ran real DDL
    brs.write_stub(dict(STUB))
    assert brs.assemble(700001)["tickers"] == ["AACI", "AA"]
    assert schema.ensure_schema(tables=tables) == []   # idempotent: a no-op


# ---- Task 6: the terminal writer ----------------------------------------

def test_terminal_write_finalizes_every_present_array(split_schema):
    from db.json import canonical
    brs.write_stub(dict(STUB))
    result = dict(STUB)
    result.update({
        "status": "finished", "progress": 100, "pnl": 500.0,
        "pnl_percent": 5.0, "time_elapsed_seconds": 1234.5,
        "backtest_decisions": [{"d": i} for i in range(1200)],
        "backtest_refusals": [],
        "backtest_trades": [{"t": i} for i in range(1500)],
        "portfolio_value_history": [{"v": i} for i in range(4000)],
        "logs": ["l%d" % i for i in range(900)],
        "backtest_prices": [{"p": i} for i in range(2597)],
        "evidence": {"fixture": "sealed"},
    })
    brs.write_terminal(700001, result)
    got = brs.assemble(700001)
    assert canonical(got) == canonical(dict(sorted(result.items())))


def test_terminal_arrays_are_returned_uncapped(split_schema):
    brs.write_stub(dict(STUB))
    result = dict(STUB)
    result.update({"status": "finished",
                   "backtest_trades": [{"t": i} for i in range(1500)],
                   "logs": ["l%d" % i for i in range(900)]})
    brs.write_terminal(700001, result)
    got = brs.assemble(700001)
    assert len(got["backtest_trades"]) == 1500      # not tail-1000
    assert len(got["logs"]) == 900                  # not tail-500


def test_terminal_write_supersedes_the_live_rows(split_schema):
    brs.write_stub(dict(STUB))
    brs.append_steps(700001, "decision", [{"d": "live"}], start_seq=0)
    result = dict(STUB)
    result.update({"status": "finished", "backtest_decisions": [{"d": "final"}]})
    brs.write_terminal(700001, result)
    assert brs.assemble(700001)["backtest_decisions"] == [{"d": "final"}]


def test_terminal_write_sets_the_hot_row_to_the_terminal_status(split_schema):
    brs.write_stub(dict(STUB))
    result = dict(STUB)
    result.update({"status": "finished", "progress": 100,
                   "time_elapsed_seconds": 1234.5})
    brs.write_terminal(700001, result)
    prog = brs.read_progress(700001)
    assert prog["status"] == "finished" and prog["progress"] == 100
    assert prog["time_elapsed_seconds"] == 1234.5      # a float, preserved


def test_terminal_write_deep_merges_metadata(split_schema):
    stub = dict(STUB)
    stub["strategy_schema"] = {"name": "gna", "config": {"a": 1}}
    brs.write_stub(stub)
    result = {"id": 700001, "status": "finished",
              "strategy_schema": {"version": 9}}
    brs.write_terminal(700001, result)
    row = store.get("BacktestResults", 700001)
    assert row["strategy_schema"] == {"name": "gna", "config": {"a": 1},
                                      "version": 9}



# ---- Task 7: stop, pause, and side-channel writers ----------------------

def test_set_status_writes_the_hot_row_and_the_doc_timestamp(split_schema):
    brs.write_stub(dict(STUB))
    brs.set_status(700001, "stopped", timestamp="2026-08-22T04:00:00Z")
    assert brs.read_progress(700001)["status"] == "stopped"
    assert store.get("BacktestResults", 700001)["timestamp"] == "2026-08-22T04:00:00Z"
    assert brs.assemble(700001)["status"] == "stopped"


def test_patch_metadata_touches_neither_the_hot_row_nor_the_steps(split_schema):
    brs.write_stub(dict(STUB))
    brs.append_steps(700001, "decision", [{"n": 1}], start_seq=0)
    before = brs.read_progress(700001)
    brs.patch_metadata(700001, {"nexus_lookback": {"current": 5, "total": 10}})
    assert brs.read_progress(700001) == before
    assert brs.watermarks(700001) == {"decision": 1}
    assert store.get("BacktestResults", 700001)["nexus_lookback"]["current"] == 5


def test_patch_metadata_can_null_a_field(split_schema):
    """broker.py's nexus_lookback clear sends {"nexus_lookback": None}."""
    brs.write_stub(dict(STUB))
    brs.patch_metadata(700001, {"nexus_lookback": {"current": 5}})
    brs.patch_metadata(700001, {"nexus_lookback": None})
    row = store.get("BacktestResults", 700001)
    assert "nexus_lookback" in row and row["nexus_lookback"] is None


def test_resume_from_critical_pause_only_fires_on_the_critical_status(split_schema):
    brs.write_stub(dict(STUB))
    brs.write_progress(700001, {"status": "paused_llm_critical"})
    brs.patch_metadata(700001, {"pause_reason": "llm", "pause_attempts": 3})
    cleared = {"pause_reason": None, "pause_attempts": None}
    assert brs.resume_from_critical_pause(700001, cleared) is True
    assert brs.read_progress(700001)["status"] == "running"
    assert store.get("BacktestResults", 700001)["pause_reason"] is None


def test_resume_from_critical_pause_does_not_stomp_a_manual_pause(split_schema):
    brs.write_stub(dict(STUB))
    brs.write_progress(700001, {"status": "paused"})     # manual
    assert brs.resume_from_critical_pause(700001, {"pause_reason": None}) is False
    assert brs.read_progress(700001)["status"] == "paused"


def test_resume_from_critical_pause_leaves_the_doc_untouched_when_it_does_not_fire(split_schema):
    brs.write_stub(dict(STUB))
    brs.write_progress(700001, {"status": "paused"})
    brs.patch_metadata(700001, {"pause_reason": "manual"})
    assert brs.resume_from_critical_pause(700001, {"pause_reason": None}) is False
    row = store.get("BacktestResults", 700001)
    assert row["pause_reason"] == "manual" and "resumed_at" not in row


def test_set_status_accepts_an_int_or_string_backtest_id(split_schema):
    brs.write_stub(dict(STUB))
    brs.set_status("700001", "error")
    assert brs.read_progress(700001)["status"] == "error"


def test_read_status_prefers_the_hot_row_over_the_stale_doc(split_schema):
    brs.write_stub(dict(STUB))                      # doc + hot row: running
    brs.write_progress(700001, {"status": "paused_llm_critical"})
    assert store.get("BacktestResults", 700001)["status"] == "running"
    assert brs.read_status(700001) == "paused_llm_critical"


def test_read_status_is_none_for_an_unknown_backtest(split_schema):
    assert brs.read_status(700099) is None


def test_stop_running_marks_only_the_running_hot_rows(split_schema):
    for bid, status in ((700001, "running"), (700002, "finished"),
                        (700003, "running")):
        stub = dict(STUB, id=bid, backtest_id=bid, status=status)
        brs.write_stub(stub)
    assert brs.stop_running() == 2
    assert brs.read_status(700001) == "stopped"
    assert brs.read_status(700002) == "finished"
    assert brs.read_status(700003) == "stopped"


def test_delete_removes_the_row_from_all_three_tables(split_schema):
    brs.write_stub(dict(STUB))
    brs.append_steps(700001, "decision", [{"n": 1}], start_seq=0)
    assert brs.delete_backtest(700001) is True
    assert store.sql('SELECT count(*) AS n FROM "BacktestSteps" '
                     "WHERE backtest_id='700001'")[0]["n"] == 0


# ---- R17: the ported paths must not be gated on the RethinkDB table list --

def _seed_queue(instance_row, queue_rows=()):
    """Seed the real queue table. There is no ReQL left to stub: the endpoint
    state of this port is that BacktestInstances is an ordinary registry table
    and BacktestResults lives in its own split tables."""
    from db import schema as dbschema
    from db import store as dbstore
    dbschema.ensure_schema(tables=["BacktestInstances"])
    rows = list(queue_rows) or ([instance_row] if instance_row else [])
    for row in rows:
        dbstore.insert("BacktestInstances", dict(row), conflict="replace")
    return None


def test_stop_writes_postgres_when_the_reql_table_list_lacks_backtestresults(
        split_schema, monkeypatch):
    import interactive_utils as iu
    brs.write_stub(dict(STUB))
    _seed_queue({"id": 700001, "status": "running"})
    monkeypatch.setattr(iu, "ensure_backtest_instances_table", lambda conn: None)
    assert iu.action_stop_backtest(object(), 700001) == {"stop_requested": True,
                                                         "id": 700001}
    assert brs.read_status(700001) == "stopped"


def test_stop_all_sweeps_postgres_when_the_reql_table_list_lacks_backtestresults(
        split_schema, monkeypatch):
    import interactive_utils as iu
    brs.write_stub(dict(STUB))
    _seed_queue({"id": 700001, "status": "running"}, queue_rows=[{"id": 700001}])
    monkeypatch.setattr(iu, "ensure_backtest_instances_table", lambda conn: None)
    monkeypatch.setattr(iu, "_stop_all_backtest_containers", lambda: 0)
    iu.action_stop_all_backtests(object())
    assert brs.read_status(700001) == "stopped"


def test_delete_clears_postgres_when_the_reql_table_list_lacks_backtestresults(
        split_schema, monkeypatch):
    import interactive_utils as iu
    brs.write_stub(dict(STUB))
    brs.append_steps(700001, "decision", [{"n": 1}], start_seq=0)
    _seed_queue({"id": 700001, "status": "running"})
    assert iu.action_delete_backtest(object(), 700001) == {"deleted": True,
                                                           "id": 700001}
    assert store.get("BacktestResults", 700001) is None
    assert brs.read_progress(700001) is None
    assert store.sql('SELECT count(*) AS n FROM "BacktestSteps" '
                     "WHERE backtest_id='700001'")[0]["n"] == 0
