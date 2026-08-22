import os

import pytest

import backtest_result_store as brs
from db import schema, store

pytestmark = pytest.mark.skipif(
    not os.environ.get("PG_TEST_DSN"),
    reason="PG_TEST_DSN not set (run: ./scripts/dev_pg.sh up)")


@pytest.fixture
def seeded(pg_schema):
    schema.ensure_schema(tables=["BacktestResults", "BacktestSteps",
                                 "BacktestProgress", "Instances"])
    brs.write_split({
        "id": 810001, "backtest_id": 810001, "instance_id": "main",
        "status": "finished", "progress": 100, "pnl": 55.0, "pnl_percent": 5.5,
        "timestamp": "2026-08-20T00:00:00", "tickers": ["A"],
        "time_elapsed_seconds": 12,
        "portfolio_value_history": [{"v": i} for i in range(20)],
        "backtest_trades": [], "backtest_prices": [], "logs": ["x"],
        "strategy_schema": {"blob": "y" * 1000},
    }, final=True)
    brs.write_split({
        "id": 810002, "backtest_id": 810002, "instance_id": "alt",
        "status": "running", "progress": 10.0, "pnl": -3.0, "pnl_percent": -0.3,
        "timestamp": "2026-08-21T00:00:00", "tickers": [],
        "time_elapsed_seconds": 4,
        "portfolio_value_history": [], "backtest_trades": [],
        "backtest_prices": [], "logs": [],
    }, final=False)
    return pg_schema


def test_read_status_returns_the_hot_row_status(seeded):
    assert brs.read_status(810001) == "finished"
    assert brs.read_status(810002) == "running"


def test_read_status_of_a_missing_backtest_is_none(seeded):
    assert brs.read_status(999999) is None


def test_read_status_follows_the_hot_row_not_a_stale_doc(seeded):
    brs.write_progress(810001, {"status": "stopped"})
    assert brs.read_status(810001) == "stopped"
    assert store.get("BacktestResults", 810001)["status"] == "finished"  # stale


def test_assemble_field_reads_the_history_without_the_document(seeded):
    got = brs.assemble_field(810001, "portfolio_value_history")
    assert got == [{"v": i} for i in range(20)]


def test_assemble_field_of_a_missing_backtest_is_none_or_empty(seeded):
    assert brs.assemble_field(999999, "portfolio_value_history") == []


def test_assemble_returns_the_whole_document_for_playback(seeded):
    doc = brs.assemble(810001)
    assert doc["strategy_schema"]["blob"].startswith("y")
    assert doc["portfolio_value_history"] and doc["logs"] == ["x"]


def test_best_by_strategy_rows_reads_only_the_summary_fields(seeded):
    rows = brs.best_by_strategy_rows()
    assert len(rows) == 2
    for row in rows:
        assert set(row) == {"id", "instance_id", "pnl", "pnl_percent", "status"}


def test_best_by_strategy_rows_reports_the_hot_row_status(seeded):
    rows = {r["id"]: r for r in brs.best_by_strategy_rows()}
    assert rows[810002]["status"] == "running"


def test_best_by_strategy_rows_omits_absent_keys_the_way_pluck_did(seeded):
    brs.write_split({"id": 810003, "status": "finished", "progress": 100,
                     "timestamp": "2026-08-22T00:00:00"}, final=True)
    row = [r for r in brs.best_by_strategy_rows() if r["id"] == 810003][0]
    assert "pnl" not in row and "instance_id" not in row


# --- The endpoints themselves ---------------------------------------------
#
# The helpers being right does not prove each site reads the fields its
# response is built from. Three of these functions have no ReQL left, so they
# run as-is; best-per-strategy still reads Instances over ReQL and is caged.

def _no_reql(monkeypatch, iu):
    """There is no ReQL left in this path: Instances is an ordinary registry
    table, so the reads go to the same Postgres the results half uses."""
    from db import schema as dbschema
    dbschema.ensure_schema(tables=["Instances"])
    return None


def test_status_endpoint_keeps_progress_last_active_and_nexus_lookback(
        seeded, monkeypatch):
    """read_status() alone would have dropped every field but `status`."""
    import interactive_utils as iu
    _no_reql(monkeypatch, iu)
    brs.patch_metadata(810002, {"nexus_lookback": 30,
                                "started_at": "2026-08-21T00:00:00"})
    brs.write_progress(810002, {"_last_active": "2026-08-21T00:05:00"})
    out = iu.action_get_backtest_status(None, 810002)
    assert out["status"] == "running"
    assert out["progress"] == 10.0
    assert out["nexus_lookback"] == 30
    assert out["_last_active"] == "2026-08-21T00:05:00"
    assert out["time_elapsed_seconds"] is not None


def test_status_endpoint_prefers_the_hot_row_over_the_stale_doc(
        seeded, monkeypatch):
    import interactive_utils as iu
    _no_reql(monkeypatch, iu)
    brs.write_progress(810001, {"status": "stopped"})
    assert iu.action_get_backtest_status(None, 810001)["status"] == "stopped"


def test_graph_endpoint_returns_all_four_arrays(seeded, monkeypatch):
    """assemble_field(pv) alone would have emptied trades/prices/decisions."""
    import interactive_utils as iu
    _no_reql(monkeypatch, iu)
    brs.finalize_steps(810001, "trade", [{"ticker": "A", "action": "buy"}])
    brs.finalize_steps(810001, "price", [{"ticker": "A", "price": 1.0}])
    brs.finalize_steps(810001, "decision", [{"ticker": "A", "signal": 1}])
    out = iu.action_graph_backtest_data(None, 810001)
    assert len(out["portfolio_value_history"]) == 20
    assert out["backtest_trades"] == [{"ticker": "A", "action": "buy"}]
    assert out["backtest_prices"] == [{"ticker": "A", "price": 1.0}]
    assert out["backtest_decisions"] == [{"ticker": "A", "signal": 1}]


def test_graph_endpoint_still_raises_for_a_missing_backtest(seeded, monkeypatch):
    import interactive_utils as iu
    _no_reql(monkeypatch, iu)
    with pytest.raises(ValueError, match="Backtest result not found"):
        iu.action_graph_backtest_data(None, 999999)


def test_playback_endpoint_reads_the_whole_document(seeded, monkeypatch):
    """It needs the metadata (strategy_schema, pnl) AND the step arrays."""
    import interactive_utils as iu
    _no_reql(monkeypatch, iu)
    brs.finalize_steps(810001, "pv", [
        {"timestamp": "2026-08-20T09:30:00", "portfolio_value": 100.0},
        {"timestamp": "2026-08-20T16:00:00", "portfolio_value": 155.0}])
    brs.finalize_steps(810001, "trade", [
        {"timestamp": "2026-08-20T10:00:00", "ticker": "A", "action": "buy",
         "shares": 1, "price": 10.0, "total": 10.0}])
    out = iu.action_get_backtest_playback_data(None, 810001)
    assert out["metadata"]["strategy_schema"]["blob"].startswith("y")
    assert out["metadata"]["pnl"] == 55.0
    kinds = {e["type"] for e in out["events"]}
    assert "date" in kinds and "decision" in kinds


def test_playback_status_guard_reads_the_hot_row(seeded, monkeypatch):
    import interactive_utils as iu
    _no_reql(monkeypatch, iu)
    brs.write_progress(810001, {"status": "queued"})
    with pytest.raises(ValueError, match="has not started yet"):
        iu.action_get_backtest_playback_data(None, 810001)


def test_logs_endpoint_reads_the_step_rows(seeded, monkeypatch):
    import interactive_utils as iu
    _no_reql(monkeypatch, iu)
    monkeypatch.setenv("BACKTEST_LOG_DIR", str(seeded) + "-nonexistent")
    out = iu.action_backtest_logs(None, 810001)
    assert out["logs"] == ["x"] and out["status"] == "finished"


def test_best_per_strategy_joins_instances_and_skips_running(seeded, monkeypatch):
    import interactive_utils as iu
    _no_reql(monkeypatch, iu)
    for row in ({"id": "main", "strategy_id": 7},
                {"id": "alt", "strategy_id": 9}):
        iu.store.insert("Instances", dict(row), conflict="replace")
    out = iu.action_backtest_best_per_strategy(None)["by_strategy"]
    assert out == {"7": {"best_pnl": 55.0, "best_pct": 5.5,
                         "backtest_id": 810001}}
