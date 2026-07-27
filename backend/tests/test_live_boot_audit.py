"""Unit tests for backend/live_boot_audit.py — build + persist."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock


def test_build_audit_row_shape():
    from live_boot_audit import build_audit_row

    boot_at = datetime(2026, 5, 28, 14, 0, tzinfo=timezone.utc)
    row = build_audit_row(
        instance_id="main",
        broker_type="alpaca",
        mode="clean_room",
        broker_cash_at_boot=9876.54,
        broker_positions_total=3,
        strategy_owned={"TSLA": 10.0},
        external={"AAPL": {"qty": 5.0, "market_value": 920.0, "note": "no WAL trace"}},
        initial_value=10000.0,
        initial_value_source="explicit",
        snapshot_loaded=True,
        snapshot_keys=42,
        trades_seeded=1,
        trades_seeded_source="wal",
        notes=["external positions detected: 1 ticker"],
        boot_at_utc=boot_at,
    )

    assert row["id"] == f"main|{boot_at.isoformat()}"
    assert row["instance_id"] == "main"
    assert row["mode"] == "clean_room"
    assert row["broker_type"] == "alpaca"
    assert row["strategy_owned_count"] == 1
    assert row["strategy_owned_tickers"] == ["TSLA"]
    assert row["external_count"] == 1
    assert row["external_tickers_qty"]["AAPL"] == 5.0
    assert row["initial_value"] == 10000.0
    assert row["initial_value_source"] == "explicit"
    assert row["snapshot_loaded"] is True
    assert row["trades_seeded"] == 1
    assert row["trades_seeded_source"] == "wal"
    assert "external positions detected: 1 ticker" in row["notes"]


def test_build_audit_row_legacy_mode_default_timestamp():
    """build_audit_row produces a valid id even when boot_at_utc is omitted."""
    from live_boot_audit import build_audit_row

    row = build_audit_row(
        instance_id="nexus-live",
        broker_type="alpaca",
        mode="legacy",
        broker_cash_at_boot=5000.0,
        broker_positions_total=0,
        strategy_owned={},
        external={},
        initial_value=5000.0,
        initial_value_source="broker_equity",
        snapshot_loaded=False,
        snapshot_keys=0,
        trades_seeded=0,
        trades_seeded_source="none",
    )

    assert row["id"].startswith("nexus-live|")
    assert row["mode"] == "legacy"
    assert row["external_count"] == 0
    assert row["strategy_owned_count"] == 0


def test_persist_audit_row_inserts_into_table():
    """persist_audit_row queries table_list and inserts the row."""
    from live_boot_audit import persist_audit_row

    fake_r = MagicMock()
    fake_conn = MagicMock()
    fake_db = MagicMock()
    fake_table = MagicMock()
    fake_r.db.return_value = fake_db
    fake_db.table_list.return_value.run.return_value = ["LiveBootAudit"]
    fake_db.table.return_value = fake_table
    fake_table.insert.return_value.run.return_value = {"inserted": 1}

    row = {"id": "main|2026-05-28T00:00:00+00:00", "instance_id": "main"}
    result = persist_audit_row(r=fake_r, conn=fake_conn, row=row)

    fake_r.db.assert_called_with("IntelliStock")
    fake_db.table.assert_called_with("LiveBootAudit")
    fake_table.insert.assert_called_once()
    assert result == {"inserted": 1}


def test_persist_audit_row_creates_table_when_missing():
    """First call should create the LiveBootAudit table + indices."""
    from live_boot_audit import persist_audit_row

    fake_r = MagicMock()
    fake_conn = MagicMock()
    fake_db = MagicMock()
    fake_table = MagicMock()
    fake_r.db.return_value = fake_db
    # First time: table is missing
    fake_db.table_list.return_value.run.return_value = ["Instances"]  # no LiveBootAudit
    fake_db.table.return_value = fake_table
    fake_db.table_create.return_value.run.return_value = {"tables_created": 1}
    fake_table.index_create.return_value.run.return_value = {"created": 1}
    fake_table.index_wait.return_value.run.return_value = []
    fake_table.insert.return_value.run.return_value = {"inserted": 1}

    row = {"id": "main|t", "instance_id": "main"}
    persist_audit_row(r=fake_r, conn=fake_conn, row=row)

    fake_db.table_create.assert_called_with("LiveBootAudit", primary_key="id")
    # Insert still proceeded after table creation
    fake_table.insert.assert_called_once()
