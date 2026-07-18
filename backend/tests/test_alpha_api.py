"""Task 8: indexed legacy nexus reads, invalid-intent handling, alpha read
APIs, and ownership reconciliation."""
import inspect
import os
import sys
import types
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.modules.setdefault("socketio", types.ModuleType("socketio"))


# --- fake ReQL chain --------------------------------------------------------

class ChainRecorder:
    def __init__(self, log, rows=None):
        self.log = log
        self.rows = rows if rows is not None else []

    def table(self, name):
        self.log.append(("table", name))
        return self

    def get_all(self, *keys, index=None):
        self.log.append(("get_all", keys, index))
        return self

    def filter(self, predicate):
        self.log.append(("filter", "FULL_TABLE_FILTER"))
        raise AssertionError("full-table lambda filter is prohibited (Task 8)")

    def order_by(self, *a, **k):
        self.log.append(("order_by",))
        return self

    def limit(self, n):
        self.log.append(("limit", n))
        return self

    def run(self, conn, **k):
        self.log.append(("run",))
        return list(self.rows)


class FakeR:
    def __init__(self, log, rows=None):
        self._log = log
        self._rows = rows

    def db(self, name):
        return ChainRecorder(self._log, self._rows)

    def desc(self, field):
        return ("desc", field)


def test_legacy_context_and_outcome_reads_use_the_base_instance_index(monkeypatch):
    import interactive_utils as iu
    log = []
    monkeypatch.setattr(iu, "r", FakeR(log, rows=[]))
    monkeypatch.setattr(iu, "_ensure_nexus_trade_tables", lambda conn: None)
    iu.action_nexus_trade_contexts(object(), "alpaca-main", limit=5)
    assert ("get_all", ("alpaca-main",), "base_instance_id") in log
    assert all(op[0] != "filter" for op in log)
    log.clear()
    iu.action_nexus_outcome_stats(object(), "alpaca-main")
    assert ("get_all", ("alpaca-main",), "base_instance_id") in log
    assert all(op[0] != "filter" for op in log)


def test_outcome_scorecard_is_marked_legacy_untrusted():
    from nexus_telemetry import summarize_outcomes
    empty = summarize_outcomes([])
    assert empty["data_status"] == "legacy_untrusted"
    some = summarize_outcomes([
        {"symbol": "A", "action_intent": "buy", "latest_return": 5.0,
         "latest_observation_date": "2026-06-10", "entry_date": "2026-06-01"}])
    assert some["data_status"] == "legacy_untrusted"


def test_unknown_intent_can_never_create_a_directional_outcome():
    from strategies.graph_nexus_analysis import (
        _normalize_action_intent, _outcome_row_allowed)
    assert _normalize_action_intent("total-garbage") == "unknown"
    assert _outcome_row_allowed("unknown") is False
    assert _outcome_row_allowed("hold") is False
    assert _outcome_row_allowed("buy") is True
    assert _outcome_row_allowed("backfill_rotation_buy") is True
    assert _outcome_row_allowed(_normalize_action_intent(None)) is False  # hold
    assert _outcome_row_allowed(_normalize_action_intent("queued")) is False


# --- alpha read API ---------------------------------------------------------

def test_limit_is_clamped_between_1_and_500():
    from benchmark_alpha.api_reads import clamp_limit
    assert clamp_limit(None) == 100
    assert clamp_limit(0) == 1
    assert clamp_limit(-5) == 1
    assert clamp_limit(50) == 50
    assert clamp_limit(9999) == 500


def test_store_reads_are_index_scoped_with_opaque_cursor():
    from benchmark_alpha.api_reads import read_alpha_records

    class FakeBackend:
        def __init__(self):
            self.calls = []

        def read_by_index(self, table, index, key, *, limit, cursor):
            self.calls.append((table, index, key, limit, cursor))
            rows = [{"id": f"row-{i}", "as_of": f"2026-07-{10+i:02d}"}
                    for i in range(limit)]
            return rows, "opaque-next"

    backend = FakeBackend()
    rows, cursor = read_alpha_records(
        backend, "AlphaPredictions", instance_id="alpaca-main",
        origin="LIVE", run_id=None, limit=2, cursor=None)
    table, index, key, limit, cur = backend.calls[0]
    assert table == "AlphaPredictions"
    assert index == "instance_origin_asof"
    assert key == ("alpaca-main", "LIVE")
    assert limit == 2
    assert cursor == "opaque-next"
    # run-scoped query uses the run index
    read_alpha_records(backend, "AlphaAllocations", instance_id="alpaca-main",
                       origin=None, run_id="run-1", limit=1, cursor=None)
    assert backend.calls[1][1] == "run_asof"
    with pytest.raises(ValueError):
        read_alpha_records(backend, "AlphaPredictions",
                           instance_id="alpaca-main", origin=None,
                           run_id=None, limit=5, cursor=None)


def test_alpha_endpoints_require_authentication():
    """Direct-call test convention: authentication is enforced by the
    Depends(get_current_user) parameter on every alpha route."""
    import api.main as api_main
    for name in ("api_alpha_predictions", "api_alpha_allocations",
                 "api_alpha_performance", "api_alpha_readiness"):
        fn = getattr(api_main, name)
        params = inspect.signature(fn).parameters
        assert "current_user" in params, f"{name} missing auth dependency"
        assert "Depends" in repr(params["current_user"].default)


# --- ownership reconciliation (Step 9) --------------------------------------

def test_reconciliation_flags_lineage_gaps_and_passes_july18_baseline():
    from benchmark_alpha.reconciliation import reconcile_order_ownership

    wal_rows = [{"client_order_id": f"main-w{i}", "broker_order_id": f"b{i}",
                 "state": "filled"} for i in range(38)]
    fills = [{"broker_order_id": f"b{i}", "client_order_id": f"main-w{i}"}
             for i in range(38)]
    dashboard = [{"broker_order_id": f"dash{i}", "client_order_id": f"web-{i}"}
                 for i in range(8)]
    report = reconcile_order_ownership(
        broker_fills=fills + dashboard, wal_rows=wal_rows,
        strategy_cid_prefix="main-")
    assert report["strategy_owned"] == 38
    assert report["dashboard_or_manual"] == 8
    assert report["unresolved"] == []
    assert report["readiness_ok"] is True

    mystery = [{"broker_order_id": "zz-1", "client_order_id": ""}]
    bad = reconcile_order_ownership(
        broker_fills=fills + mystery, wal_rows=wal_rows,
        strategy_cid_prefix="main-")
    assert bad["unresolved"] and bad["readiness_ok"] is False

    # A WAL intent that never reached a terminal state is surfaced.
    stuck = reconcile_order_ownership(
        broker_fills=fills, wal_rows=wal_rows + [
            {"client_order_id": "main-stuck", "broker_order_id": None,
             "state": "submitted"}],
        strategy_cid_prefix="main-")
    assert "main-stuck" in stuck["non_terminal_intents"]
