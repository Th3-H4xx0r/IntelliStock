"""Task 2: dry-run-first purge of historical BacktestResults secrets."""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.purge_backtest_secrets import main, sanitize_backtest_row


def test_sanitize_backtest_row_returns_secret_free_patch():
    patch, count = sanitize_backtest_row({
        "id": "r1",
        "strategy_schema": {"strategies": [{"config": {
            "benzinga_api_key": "CANARY_BENZINGA_VALUE",
            "max_positions": 8,
        }}]},
    })
    assert count == 1
    assert patch["strategy_schema"]["strategies"][0]["config"]["max_positions"] == 8
    assert "CANARY_BENZINGA_VALUE" not in repr(patch)


def test_sanitize_backtest_row_without_schema_is_a_noop():
    patch, count = sanitize_backtest_row({"id": "r2", "pnl": 1.5})
    assert patch == {}
    assert count == 0


class FakeBackend:
    """In-memory stand-in for the RethinkDB BacktestResults table."""

    def __init__(self, rows):
        self.rows = {row["id"]: row for row in rows}
        self.updates = []

    def iter_rows(self, batch_size):
        assert batch_size > 0
        ordered = sorted(self.rows, key=str)
        for start in range(0, len(ordered), batch_size):
            for row_id in ordered[start:start + batch_size]:
                yield self.rows[row_id]

    def update_row(self, row_id, patch, *, durability):
        assert durability == "hard"
        self.updates.append((row_id, patch))
        self.rows[row_id] = {**self.rows[row_id], **patch}

    def get_row(self, row_id):
        return self.rows.get(row_id)


def _rows():
    return [
        {"id": "a1", "strategy_schema": {"strategies": [{"config": {
            "alpaca_secret": "CANARY_SECRET_ONE", "max_positions": 8}}]}},
        {"id": "b2", "strategy_schema": {"strategies": [{"config": {
            "max_positions": 4}}]}},
        {"id": "c3", "pnl": 2.0},
    ]


def test_dry_run_default_performs_zero_updates(capsys):
    backend = FakeBackend(_rows())
    rc = main([], backend=backend)
    assert rc == 0
    assert backend.updates == []
    out = capsys.readouterr().out
    assert "a1" in out
    assert "CANARY_SECRET_ONE" not in out


def test_apply_without_confirmation_string_exits_2():
    backend = FakeBackend(_rows())
    rc = main(["--apply"], backend=backend)
    assert rc == 2
    assert backend.updates == []


def test_apply_with_wrong_confirmation_table_exits_2():
    backend = FakeBackend(_rows())
    rc = main(["--apply", "--confirm-table", "SomeOtherTable"], backend=backend)
    assert rc == 2
    assert backend.updates == []


def test_apply_updates_only_secret_bearing_rows_and_verifies(capsys):
    backend = FakeBackend(_rows())
    rc = main(["--apply", "--confirm-table", "BacktestResults"], backend=backend)
    assert rc == 0
    assert [row_id for row_id, _ in backend.updates] == ["a1"]
    patched = backend.rows["a1"]["strategy_schema"]
    assert "CANARY_SECRET_ONE" not in json.dumps(patched)
    assert patched["strategies"][0]["config"]["max_positions"] == 8
    out = capsys.readouterr().out
    assert "CANARY_SECRET_ONE" not in out


def test_output_never_renders_secret_payloads(capsys):
    backend = FakeBackend(_rows())
    main([], backend=backend)
    main(["--apply", "--confirm-table", "BacktestResults"], backend=backend)
    combined = capsys.readouterr().out
    assert "CANARY_SECRET_ONE" not in combined
    assert "alpaca_secret" not in combined


def test_default_database_is_intellistock():
    """Audit: the script defaulted to RethinkDB db 'test' while the real
    BacktestResults lives in 'IntelliStock'."""
    import inspect
    from scripts import purge_backtest_secrets as pbs
    assert 'RETHINKDB_DB", "IntelliStock"' in inspect.getsource(pbs.RethinkBackend)


def test_already_redacted_rows_are_idempotent_no_ops():
    """Audit: a previously-purged row re-flagged dirty forever."""
    from persistence_safety import REDACTION_MARKER
    patch, count = sanitize_backtest_row({
        "id": "r9",
        "strategy_schema": {"strategies": [{"config": {
            "alpaca_key": dict(REDACTION_MARKER), "max_positions": 8}}]},
    })
    assert count == 0
    assert patch == {}
