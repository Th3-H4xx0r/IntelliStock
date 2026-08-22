"""Regression coverage for backtest queue/container reconciliation."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field


_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)


class _Expr:
    def eq(self, _value):
        return self

    def __and__(self, _other):
        return self


class _Row:
    def __getitem__(self, _key):
        return _Expr()


@dataclass
class _State:
    queue_rows: list[dict]
    result_rows: dict[int, dict] = field(default_factory=dict)
    queue_deletes: list[int] = field(default_factory=list)
    result_updates: list[tuple[int, dict]] = field(default_factory=list)


class _Query:
    def __init__(self, state, table=None, operation="scan", row_id=None):
        self.state = state
        self.table = table
        self.operation = operation
        self.row_id = row_id
        self.fields = None
        self.patch = None

    def filter(self, _predicate):
        return self

    def pluck(self, *fields):
        self.fields = fields
        return self

    def get(self, row_id):
        self.row_id = row_id
        self.operation = "get"
        return self

    def delete(self):
        self.operation = "delete"
        return self

    def update(self, patch):
        self.operation = "update"
        self.patch = patch
        return self

    def run(self, _conn):
        if self.operation == "table_list":
            return getattr(self, "tables_override", None) or [
                "BacktestInstances", "BacktestResults"]
        if self.operation == "delete":
            self.state.queue_deletes.append(self.row_id)
            return {"deleted": 1}
        if self.operation == "update":
            self.state.result_updates.append((self.row_id, self.patch))
            return {"replaced": 1}
        if self.operation == "get":
            row = dict(self.state.result_rows.get(self.row_id) or {})
            if self.fields:
                row = {field: row[field] for field in self.fields if field in row}
            return row

        rows = [dict(row) for row in self.state.queue_rows]
        if self.fields:
            rows = [
                {field: row[field] for field in self.fields if field in row}
                for row in rows
            ]
        return rows


class _Db:
    def __init__(self, state):
        self.state = state

    def table(self, name):
        return _Query(self.state, table=name)

    def table_list(self):
        return _Query(self.state, operation="table_list")


class _Rethink:
    def __init__(self, state):
        self.state = state
        self.row = _Row()

    def db(self, _name):
        return _Db(self.state)


class _Containers:
    def list(self):
        return []


class _DockerClient:
    containers = _Containers()

    def close(self):
        return None


class _Connection:
    def close(self):
        return None


def test_container_health_check_preserves_pending_row_without_container(
        pg_schema, monkeypatch):
    """A queued job is not dead merely because its container has not launched."""
    from db import schema as dbschema
    from db import store as dbstore
    dbschema.ensure_schema(tables=["BacktestInstances"])
    dbstore.insert("BacktestInstances", {
        "id": 276226, "instance": "alpaca-main", "status": "pending",
        "run": True,
    }, conflict="replace")

    original_cwd = os.getcwd()
    try:
        from engines import backtest_engine as engine
    finally:
        os.chdir(original_cwd)

    monkeypatch.setattr(engine, "_get_docker_client", lambda: _DockerClient())
    monkeypatch.setattr(engine, "get_conn", lambda: None)
    monkeypatch.setattr(engine, "ensure_table", lambda _conn=None: None)
    monkeypatch.setattr(engine, "_container_launch_times", {})
    monkeypatch.setattr(engine, "_queued_or_active_ids", {276226})

    engine._check_dead_backtest_containers()

    # The row is still queued: not deleted, and not flipped to run=False.
    row = dbstore.get("BacktestInstances", 276226)
    assert row is not None, "the pending queue row was deleted"
    assert row.get("run") is True
    assert row.get("status") == "pending"


def test_paused_run_survives_when_backtestresults_lives_in_postgres(
        pg_schema, monkeypatch):
    """R17 regression: the queue row of a PAUSED run must be KEPT (run=False),
    not deleted. The status lives in Postgres, so the old RethinkDB table-list
    gate that used to guard this read is gone entirely."""
    from db import schema as dbschema
    from db import store as dbstore
    dbschema.ensure_schema(tables=["BacktestResults", "BacktestSteps",
                                   "BacktestProgress", "BacktestInstances"])
    import backtest_result_store as brs
    brs.write_stub({"id": 700123, "backtest_id": 700123, "status": "running",
                    "progress": 0})
    brs.write_progress(700123, {"status": "paused_llm_critical"})
    dbstore.insert("BacktestInstances", {
        "id": 700123, "instance": "alpaca-main", "status": "running",
        "run": True,
    }, conflict="replace")

    original_cwd = os.getcwd()
    try:
        from engines import backtest_engine as engine
    finally:
        os.chdir(original_cwd)

    monkeypatch.setattr(engine, "_get_docker_client", lambda: _DockerClient())
    monkeypatch.setattr(engine, "get_conn", lambda: None)
    monkeypatch.setattr(engine, "ensure_table", lambda _conn=None: None)
    monkeypatch.setattr(engine, "_container_launch_times", {})
    monkeypatch.setattr(engine, "_queued_or_active_ids", {700123})

    engine._check_dead_backtest_containers()

    # Kept, not deleted: the paused row waits for the operator to resume it.
    row = dbstore.get("BacktestInstances", 700123)
    assert row is not None, "the paused run's queue row was deleted"
    assert row.get("run") is False
    assert brs.read_status(700123) == "paused_llm_critical"
