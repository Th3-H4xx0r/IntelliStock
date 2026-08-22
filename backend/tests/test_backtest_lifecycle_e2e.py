"""One backtest, driven through its whole life, checked at every step."""
import os

import pytest

import backtest_result_store as brs
from broker_snapshot_helpers import downsample_history
from db import schema, store
from db.json import canonical

pytestmark = pytest.mark.skipif(
    not os.environ.get("PG_TEST_DSN"),
    reason="PG_TEST_DSN not set (run: ./scripts/dev_pg.sh up)")

BID = 830001


@pytest.fixture
def lifecycle(pg_schema):
    schema.ensure_schema(tables=["BacktestResults", "BacktestSteps",
                                 "BacktestProgress"])
    return pg_schema


def _stub():
    return {
        "id": BID, "backtest_id": BID, "status": "running", "progress": 0,
        "timestamp": "2026-08-22T03:00:00Z", "instance_id": None,
        "strategy_id": None, "pnl": None, "pnl_percent": None,
        "start_date": "2026-03-01 00:00:00", "end_date": "2026-04-01 00:00:00",
        "tickers": ["AACI", "AA"], "time_elapsed_seconds": None,
        "portfolio_value_history": [], "backtest_trades": [],
        "backtest_prices": [], "logs": [],
    }


def test_a_backtest_reads_correctly_at_every_step_of_its_life(lifecycle):
    # --- stub -----------------------------------------------------------
    brs.write_stub(_stub())
    assert canonical(brs.assemble(BID)) == canonical(dict(sorted(_stub().items())))

    # --- three progress ticks + five heartbeats --------------------------
    seqs, log_seq = {}, 0
    decisions, trades, history, logs = [], [], [], []
    for tick in range(1, 4):
        decisions += [{"d": tick * 100 + i} for i in range(400)]
        trades += [{"t": tick * 100 + i} for i in range(600)]
        history += [{"v": float(tick * 1000 + i)} for i in range(2000)]
        brs.write_progress_tick(
            BID,
            hot={"status": "running", "progress": tick * 25.0,
                 "time_elapsed_seconds": tick * 100,
                 "_last_active": "2026-08-22T03:%02d:00+00:00" % tick},
            metadata={"pnl": 1.0 * tick, "pnl_percent": 0.1 * tick,
                      "timestamp": "2026-08-22T03:%02d:00" % tick,
                      "tickers": ["AACI", "AA"]},
            appended={"decision": decisions, "trade": trades, "pv": history},
            seqs=seqs)
        for beat in range(5):
            new = ["t%d-b%d" % (tick, beat)]
            logs += new
            log_seq = brs.heartbeat(BID, last_active="hb", new_log_lines=new,
                                    log_seq=log_seq)
        got = brs.assemble(BID)
        assert got["backtest_decisions"] == decisions
        assert got["backtest_trades"] == trades[-1000:]
        assert got["portfolio_value_history"] == list(downsample_history(history, 3000))
        assert got["logs"] == logs[-500:]
        assert got["progress"] == tick * 25.0
        assert got["pnl"] == 1.0 * tick

    # --- critical pause and resume ---------------------------------------
    brs.set_status(BID, "paused_llm_critical",
                   extra_metadata={"pause_reason": "llm", "pause_attempts": 3})
    assert brs.read_status(BID) == "paused_llm_critical"
    assert brs.assemble(BID)["status"] == "paused_llm_critical"
    assert brs.resume_from_critical_pause(BID, {"pause_reason": None,
                                                "pause_attempts": None}) is True
    assert brs.read_status(BID) == "running"

    # The steps survived the pause untouched.
    assert brs.assemble(BID)["backtest_decisions"] == decisions

    # --- terminal write ---------------------------------------------------
    result = dict(_stub())
    result.update({
        "status": "finished", "progress": 100, "pnl": 42.0,
        "pnl_percent": 4.2, "time_elapsed_seconds": 412.5,
        "backtest_decisions": decisions, "backtest_refusals": [],
        "backtest_trades": trades, "portfolio_value_history": history,
        "logs": logs, "backtest_prices": [{"p": i} for i in range(2597)],
        "evidence": {"fixture": "sealed"},
        "pause_reason": None, "pause_attempts": None,
        "resumed_at": brs.assemble(BID)["resumed_at"],
        "timestamp": "2026-08-22T03:03:00",
    })
    brs.write_terminal(BID, result)

    # The terminal write is a MERGE, exactly as the legacy ``.update(result)``
    # was, so the last heartbeat's ``_last_active`` survives it -- broker.py's
    # terminal dict never carries that key (broker.py:12925-12931) and
    # interactive_utils.py:5959 reads it back off the finished document to show
    # how stale the progress data is. So the document the legacy writers would
    # have produced is ``result`` PLUS the heartbeat's marker.
    expected = dict(result, _last_active="hb")
    got = brs.assemble(BID)
    assert got["_last_active"] == "hb"
    assert set(got) == set(expected)
    assert canonical(got) == canonical(dict(sorted(expected.items())))


def test_the_list_and_detail_endpoints_agree_throughout(lifecycle):
    brs.write_stub(_stub())
    for progress in (0.0, 33.0, 66.0, 100.0):
        brs.write_progress_tick(BID, hot={"status": "running",
                                          "progress": progress},
                                metadata={}, appended={}, seqs={})
        _active, page, _total = brs.list_rows(
            instance_filter=None, page=1, per_page=50, sort_order="desc",
            ticker_preview=4)
        listed = [r for r in page if r["id"] == BID][0]
        detail = brs.assemble(BID)
        assert listed["status"] == detail["status"]
        assert listed["progress"] == detail["progress"]
        assert listed["tickers_total"] == len(detail["tickers"])


def test_a_stopped_run_reads_as_a_stopped_run(lifecycle):
    brs.write_stub(_stub())
    brs.append_steps(BID, "decision", [{"d": 1}], start_seq=0)
    brs.set_status(BID, "stopped", timestamp="2026-08-22T04:00:00Z")
    got = brs.assemble(BID)
    assert got["status"] == "stopped"
    assert got["timestamp"] == "2026-08-22T04:00:00Z"
    assert got["backtest_decisions"] == [{"d": 1}]     # not lost by the stop
    assert got["logs"] == []                           # always-present, empty


def test_deleting_mid_run_leaves_nothing_behind(lifecycle):
    brs.write_stub(_stub())
    brs.write_progress_tick(BID, hot={"status": "running", "progress": 5},
                            metadata={}, appended={"decision": [{"d": 1}]},
                            seqs={})
    assert brs.delete_backtest(BID) is True
    assert brs.assemble(BID) is None
    assert store.sql('SELECT count(*) AS n FROM "BacktestSteps" '
                     "WHERE backtest_id = %s", (str(BID),))[0]["n"] == 0
    assert store.sql('SELECT count(*) AS n FROM "BacktestProgress" '
                     "WHERE id = %s", (str(BID),))[0]["n"] == 0
