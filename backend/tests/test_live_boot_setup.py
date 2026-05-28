"""Unit tests for the live broker daemon's clean-room auto-setup helpers."""
from __future__ import annotations

from unittest.mock import MagicMock


# ----------------- should_auto_reset_on_migration -----------------------


def test_auto_reset_explicit_env_truthy_wins():
    from live_boot_setup import should_auto_reset_on_migration
    for v in ("1", "true", "True", "TRUE", "yes", "on", "  True  "):
        assert should_auto_reset_on_migration(v, clean_room_mode=False) is True
        assert should_auto_reset_on_migration(v, clean_room_mode=True) is True


def test_auto_reset_explicit_env_falsy_wins():
    from live_boot_setup import should_auto_reset_on_migration
    for v in ("0", "false", "False", "no", "off"):
        assert should_auto_reset_on_migration(v, clean_room_mode=False) is False
        assert should_auto_reset_on_migration(v, clean_room_mode=True) is False, \
            "operator override (env=false) must win even when clean_room_mode=true"


def test_auto_reset_unset_env_mirrors_clean_room():
    from live_boot_setup import should_auto_reset_on_migration
    for v in (None, "", "   ", "maybe", "unknown"):
        assert should_auto_reset_on_migration(v, clean_room_mode=False) is False
        assert should_auto_reset_on_migration(v, clean_room_mode=True) is True


# ----------------- is_first_clean_room_boot -----------------------------


def _fake_r_for_first_boot(audit_table_exists: bool, audit_rows_for_main: list | None = None):
    """Build a MagicMock rethinkdb root that yields the requested topology.

    audit_rows_for_main: the LiveBootAudit rows .filter(instance_id).run() returns
    (each a dict that may carry a 'mode' key)."""
    r = MagicMock()
    db = MagicMock()
    r.db.return_value = db

    db.table_list.return_value.run.return_value = (
        ["Instances", "BrokerageAccounts", "LiveBootAudit", "Other"]
        if audit_table_exists
        else ["Instances", "BrokerageAccounts", "Other"]
    )

    # LiveBootAudit -> filter(...).run(conn) -> iterable of rows
    audit_tbl = MagicMock()
    audit_filtered = MagicMock()
    audit_filtered.run.return_value = iter(list(audit_rows_for_main or []))
    audit_tbl.filter.return_value = audit_filtered

    def _table_dispatch(name):
        if name == "LiveBootAudit":
            return audit_tbl
        return MagicMock()
    db.table.side_effect = _table_dispatch
    return r


def test_first_clean_room_boot_table_absent_is_true():
    from live_boot_setup import is_first_clean_room_boot
    r = _fake_r_for_first_boot(audit_table_exists=False)
    assert is_first_clean_room_boot(r, conn=MagicMock(), instance_id="main") is True


def test_first_clean_room_boot_table_exists_no_rows_is_true():
    from live_boot_setup import is_first_clean_room_boot
    r = _fake_r_for_first_boot(audit_table_exists=True, audit_rows_for_main=[])
    assert is_first_clean_room_boot(r, conn=MagicMock(), instance_id="main") is True


def test_first_clean_room_boot_prior_clean_room_row_is_false():
    from live_boot_setup import is_first_clean_room_boot
    r = _fake_r_for_first_boot(
        audit_table_exists=True,
        audit_rows_for_main=[{"instance_id": "main", "mode": "clean_room"}],
    )
    assert is_first_clean_room_boot(r, conn=MagicMock(), instance_id="main") is False


def test_first_clean_room_boot_only_legacy_rows_is_true():
    """0-B: a prior LEGACY/smoke boot must NOT suppress the first clean-room
    cleanup (the per-boot audit row is written on every boot, mode='legacy')."""
    from live_boot_setup import is_first_clean_room_boot
    r = _fake_r_for_first_boot(
        audit_table_exists=True,
        audit_rows_for_main=[
            {"instance_id": "main", "mode": "legacy"},
            {"instance_id": "main", "mode": "legacy"},
        ],
    )
    assert is_first_clean_room_boot(r, conn=MagicMock(), instance_id="main") is True


def test_rebaseline_clean_room_drawdown_resets_stale_backtest_peak():
    """2-B: a hydrated backtest drawdown peak is reset to live equity, halt cleared."""
    from live_boot_setup import rebaseline_clean_room_drawdown
    cache = {"_portfolio_drawdown_state": {"peak_value": 19977.60, "last_value": 18000.0,
                                           "halt_active": True, "up_days": 0}}
    new = rebaseline_clean_room_drawdown(cache, live_equity=0.00)
    assert new["peak_value"] == 0.00
    assert new["last_value"] == 0.00
    assert new["halt_active"] is False
    assert cache["_portfolio_drawdown_state"]["peak_value"] == 0.00


def test_rebaseline_clean_room_drawdown_noop_when_absent():
    """No drawdown state -> nothing to reset (returns None)."""
    from live_boot_setup import rebaseline_clean_room_drawdown
    assert rebaseline_clean_room_drawdown({}, live_equity=0.00) is None
    assert rebaseline_clean_room_drawdown({"_portfolio_drawdown_state": {}}, 0.00) is None


def test_strip_stale_momentum_baseline_removes_first_seen_price():
    """2-E: hydrated momentum-watchlist entries lose their backtest first_seen_price."""
    from live_boot_setup import strip_stale_momentum_baseline
    cache = {"_momentum_watchlist": {
        "NVDA": {"first_seen_price": 95.0, "first_seen_date": "2026-01-10", "score": 1.2},
        "AMD": {"first_seen_price": 140.0, "score": 0.8},
        "INTC": {"score": 0.5},  # no baseline -> untouched
    }}
    n = strip_stale_momentum_baseline(cache)
    assert n == 2
    assert "first_seen_price" not in cache["_momentum_watchlist"]["NVDA"]
    assert "first_seen_price" not in cache["_momentum_watchlist"]["AMD"]
    # other fields preserved
    assert cache["_momentum_watchlist"]["NVDA"]["score"] == 1.2
    assert cache["_momentum_watchlist"]["NVDA"]["first_seen_date"] == "2026-01-10"


def test_strip_stale_momentum_baseline_noop_when_absent():
    from live_boot_setup import strip_stale_momentum_baseline
    assert strip_stale_momentum_baseline({}) == 0
    assert strip_stale_momentum_baseline({"_momentum_watchlist": {}}) == 0


def test_first_clean_room_boot_empty_instance_id_is_false():
    """Defensive: an empty instance_id should NOT trigger destructive cleanup."""
    from live_boot_setup import is_first_clean_room_boot
    r = MagicMock()
    assert is_first_clean_room_boot(r, conn=MagicMock(), instance_id="") is False
    assert is_first_clean_room_boot(r, conn=MagicMock(), instance_id=None) is False  # type: ignore[arg-type]


def test_first_clean_room_boot_db_error_is_false():
    """Defensive: if RethinkDB errors out we return False (skip cleanup)."""
    from live_boot_setup import is_first_clean_room_boot
    r = MagicMock()
    r.db.side_effect = RuntimeError("connection dropped")
    assert is_first_clean_room_boot(r, conn=MagicMock(), instance_id="main") is False


# ----------------- run_first_clean_room_boot_cleanup --------------------


def test_run_cleanup_delegates_to_clear_instance_state():
    """The function must call clear_instance_state.execute with the
    'full_instance' scope and apply=True."""
    from unittest.mock import patch
    from live_boot_setup import run_first_clean_room_boot_cleanup

    expected = {
        "instance_id": "main",
        "scope": "full_instance",
        "apply": True,
        "tables": [{"table": "GraphNexusTradeContexts", "would_delete": 5,
                    "deleted": 5, "skipped": False, "reason": None}],
        "total_would_delete": 5,
        "total_deleted": 5,
    }
    with patch("clear_instance_state.execute", return_value=expected) as mock_exec:
        result = run_first_clean_room_boot_cleanup(MagicMock(), MagicMock(), "main")

    mock_exec.assert_called_once()
    _args, kwargs = mock_exec.call_args
    assert kwargs.get("instance_id") == "main"
    assert kwargs.get("scope") == "full_instance"
    assert kwargs.get("apply") is True
    assert result == expected


def test_run_cleanup_catches_execute_failure():
    """If the underlying clear raises, return a dict with `error` set rather
    than letting the exception escape into broker.py boot."""
    from unittest.mock import patch
    from live_boot_setup import run_first_clean_room_boot_cleanup

    with patch("clear_instance_state.execute", side_effect=RuntimeError("boom")):
        result = run_first_clean_room_boot_cleanup(MagicMock(), MagicMock(), "main")
    assert result["instance_id"] == "main"
    assert result["scope"] == "full_instance"
    assert result["apply"] is True
    assert "error" in result
    assert "boom" in result["error"]
    assert result["total_deleted"] == 0
