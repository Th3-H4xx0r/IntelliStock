"""Credential inventory and migration primitives never expose secret values."""
from __future__ import annotations

import hashlib
import json
import os
import stat
import sys
from types import ModuleType
from unittest.mock import Mock

import pytest

_backend = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _backend not in sys.path:
    sys.path.insert(0, _backend)


def test_inventory_never_returns_secret_values():
    """Replacing the stored credential with a canary must never expose it."""
    from credential_audit import scan_secret_fields

    findings = scan_secret_fields({
        "BrokerageAccounts": [{"id": "acct-1", "alpaca_secret": "CANARY"}],
    })

    assert "CANARY" not in repr(findings)
    assert findings[0].field == "alpaca_secret"
    assert findings[0].encrypted is False
    assert findings[0].row_id_hash == hashlib.sha256(b"acct-1").hexdigest()


def test_inventory_only_scans_schema_allowed_secret_fields():
    """An arbitrary string field must not become inventory output."""
    from credential_audit import scan_secret_fields

    findings = scan_secret_fields({
        "BrokerageAccounts": [{"id": "acct-2", "notes": "CANARY", "alpaca_key": "fernet:token"}],
        "Unknown": [{"id": "row-1", "api_key": "CANARY"}],
    })

    assert [(finding.table, finding.field, finding.encrypted) for finding in findings] == [
        ("BrokerageAccounts", "alpaca_key", True),
    ]


def test_inventory_finds_legacy_stock_and_nested_strategy_credentials_without_values():
    """Residual stock credential locations are visible, but their values never are."""
    from credential_audit import scan_secret_fields

    findings = scan_secret_fields({
        "Instances": [{"id": "stock-1", "key": "CANARY_INSTANCE"}],
        "BacktestInstances": [{"id": 123, "secret": "CANARY_BACKTEST"}],
        "Strategies": [{
            "id": 7,
            "strategies": [{
                "strategy": "graph_nexus_analysis",
                "config": {
                    "company_article_llm_api_key": "CANARY_STRATEGY",
                    "max_output_tokens": 500,
                },
            }],
        }],
    })

    encoded = repr(findings)
    assert "CANARY" not in encoded
    assert {(f.table, f.field, f.encrypted) for f in findings} == {
        ("Instances", "key", False),
        ("BacktestInstances", "secret", False),
        ("Strategies", "strategies[0].config.company_article_llm_api_key", False),
    }


def test_strategy_migration_patch_purges_inline_credentials():
    from scripts.migrate_encrypted_credentials import build_strategy_scrub_patch

    patch = build_strategy_scrub_patch({
        "id": 7,
        "strategies": [{
            "strategy": "graph_nexus_analysis",
            "config": {
                "llm_api_key": "CANARY_LLM",
                "max_positions": 8,
            },
        }],
    })

    assert patch == {
        "strategies": [{
            "strategy": "graph_nexus_analysis",
            "config": {"max_positions": 8},
        }],
    }
    assert "CANARY" not in repr(patch)


def test_encrypted_patch_copy_verify_switch_round_trips(with_key):
    """Migration patches encrypt plaintext but preserve existing ciphertext."""
    from scripts.migrate_encrypted_credentials import build_encrypted_patch, verify_patch
    from secret_store import encrypt

    existing = encrypt("already-encrypted")
    patch = build_encrypted_patch(
        {"alpaca_key": "plain-key", "alpaca_secret": existing, "notes": "CANARY"},
        fields=("alpaca_key", "alpaca_secret"),
    )

    assert patch["alpaca_key"].startswith("fernet:")
    assert patch["alpaca_secret"] == existing
    assert "notes" not in patch
    verify_patch(patch, fields=("alpaca_key", "alpaca_secret"))


def test_migration_apply_requires_backup_before_database_access(monkeypatch):
    """Removing the backup guard must never permit a mutation attempt."""
    from scripts import migrate_encrypted_credentials as migration

    db_loader = Mock(side_effect=AssertionError("database must not be opened"))
    monkeypatch.setattr(migration, "_rows_from_db", db_loader)

    with pytest.raises(SystemExit) as exc:
        migration.main(["--apply"])

    assert exc.value.code == 2
    db_loader.assert_not_called()


def test_migration_rejects_snapshot_apply_before_database_access(monkeypatch, tmp_path):
    """A snapshot can only be audited, never used as an apply source."""
    from scripts import migrate_encrypted_credentials as migration

    snapshot = tmp_path / "rows.json"
    snapshot.write_text(json.dumps({"BrokerageAccounts": []}), encoding="utf-8")
    db_loader = Mock(side_effect=AssertionError("database must not be opened"))
    monkeypatch.setattr(migration, "_rows_from_db", db_loader)

    with pytest.raises(SystemExit) as exc:
        migration.main(["--apply", "--snapshot", str(snapshot), "--backup-file", str(tmp_path / "backup.json")])

    assert exc.value.code == 2
    db_loader.assert_not_called()


def test_migration_default_dry_run_never_updates_database(monkeypatch, capsys):
    """Default execution inventories rows but cannot enter the switch step."""
    from scripts import migrate_encrypted_credentials as migration

    query = Mock()
    query.update.side_effect = AssertionError("dry-run must not update")
    table = Mock()
    table.get.return_value = query
    database = Mock()
    database.table.return_value = table
    fake_r = Mock()
    fake_r.db.return_value = database
    fake_conn = Mock()
    monkeypatch.setattr(migration, "_rows_from_db", lambda: (
        fake_r, fake_conn,
        {"BrokerageAccounts": [{"id": "acct-1", "alpaca_secret": "CANARY"}]},
    ))

    assert migration.main([]) == 0

    output = capsys.readouterr().out
    assert '"mode": "dry-run"' in output
    assert "CANARY" not in output
    query.update.assert_not_called()
    fake_conn.close.assert_called_once_with()


def test_database_inventory_tolerates_optional_tables_not_yet_created(monkeypatch):
    """Adding an audited schema table must not make read-only inventory crash."""
    from scripts import migrate_encrypted_credentials as migration

    class _Query:
        def __init__(self, value):
            self.value = value

        def run(self, _conn):
            return self.value

    class _Database:
        def table_list(self):
            return _Query(["BrokerageAccounts"])

        def table(self, name):
            if name != "BrokerageAccounts":
                raise AssertionError(f"missing table must not be queried: {name}")
            return _Query([{"id": "acct-1"}])

    class _Rethink:
        def __init__(self):
            self.connection = Mock()

        def connect(self, **_kwargs):
            return self.connection

        def db(self, name):
            assert name == "IntelliStock"
            return _Database()

    fake_module = ModuleType("rethinkdb")
    fake_module.RethinkDB = _Rethink
    monkeypatch.setitem(sys.modules, "rethinkdb", fake_module)

    _r, _conn, rows = migration._rows_from_db()

    assert rows["BrokerageAccounts"] == [{"id": "acct-1"}]
    assert rows["Strategies"] == []
    assert rows["Models"] == []


def test_migration_apply_backs_up_before_switching_with_private_mode(monkeypatch, tmp_path, with_key):
    """The switch step occurs only after a mode-0600 backup has been created."""
    from scripts import migrate_encrypted_credentials as migration

    backup = tmp_path / "credentials-backup.json"
    events = []

    class _Update:
        def run(self, _conn):
            assert backup.exists()
            assert stat.S_IMODE(backup.stat().st_mode) == 0o600
            events.append("update")
            return {"replaced": 1}

    class _Row:
        def update(self, patch):
            assert patch["alpaca_secret"].startswith("fernet:")
            events.append("backup-before-update" if backup.exists() else "missing-backup")
            return _Update()

    class _Table:
        def get(self, row_id):
            assert row_id == "acct-1"
            return _Row()

    class _Database:
        def table(self, table):
            assert table == "BrokerageAccounts"
            return _Table()

    class _Rethink:
        def db(self, database):
            assert database == "IntelliStock"
            return _Database()

    fake_conn = Mock()
    monkeypatch.setattr(migration, "_rows_from_db", lambda: (
        _Rethink(), fake_conn,
        {"BrokerageAccounts": [{"id": "acct-1", "alpaca_secret": "plain-secret"}]},
    ))

    assert migration.main(["--apply", "--backup-file", str(backup)]) == 0

    assert events == ["backup-before-update", "update"]
    assert stat.S_IMODE(backup.stat().st_mode) == 0o600
    assert "plain-secret" in backup.read_text(encoding="utf-8")
    fake_conn.close.assert_called_once_with()


@pytest.fixture
def with_key(monkeypatch):
    pytest.importorskip("cryptography")
    from cryptography.fernet import Fernet

    monkeypatch.setenv("INTELLISTOCK_CRED_KEY", Fernet.generate_key().decode())
