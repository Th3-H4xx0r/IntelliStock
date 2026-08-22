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


def _seed_audit(store, monkeypatch, audit_table_exists, audit_rows_for_main=None):
    """Point live_boot_setup at ``store`` and seed the LiveBootAudit rows.

    Postgres port (G11): ``r`` and ``conn`` are still on the signature but are
    ignored, so the topology is set up on the store instead of on a mock.
    """
    import live_boot_setup

    monkeypatch.setattr(live_boot_setup, "store", store)
    if not audit_table_exists:
        monkeypatch.setattr(store, "table_list", lambda: ["Instances", "Other"])
        return
    monkeypatch.setattr(
        store, "table_list",
        lambda: ["Instances", "BrokerageAccounts", "LiveBootAudit", "Other"])
    rows = [dict(row, id=f"audit-{i}")
            for i, row in enumerate(audit_rows_for_main or [])]
    if rows:
        store.insert("LiveBootAudit", rows)


def test_first_clean_room_boot_table_absent_is_true(store, monkeypatch):
    from live_boot_setup import is_first_clean_room_boot
    _seed_audit(store, monkeypatch, audit_table_exists=False)
    assert is_first_clean_room_boot(None, conn=None, instance_id="main") is True


def test_first_clean_room_boot_table_exists_no_rows_is_true(store, monkeypatch):
    from live_boot_setup import is_first_clean_room_boot
    _seed_audit(store, monkeypatch, audit_table_exists=True, audit_rows_for_main=[])
    assert is_first_clean_room_boot(None, conn=None, instance_id="main") is True


def test_first_clean_room_boot_prior_clean_room_row_is_false(store, monkeypatch):
    from live_boot_setup import is_first_clean_room_boot
    _seed_audit(store, monkeypatch, audit_table_exists=True,
                audit_rows_for_main=[{"instance_id": "main", "mode": "clean_room"}])
    assert is_first_clean_room_boot(None, conn=None, instance_id="main") is False


def test_first_clean_room_boot_ignores_other_instances(store, monkeypatch):
    """The filter is scoped to instance_id -- another instance's clean-room row
    must not suppress this one's first-boot cleanup."""
    from live_boot_setup import is_first_clean_room_boot
    _seed_audit(store, monkeypatch, audit_table_exists=True,
                audit_rows_for_main=[{"instance_id": "other", "mode": "clean_room"}])
    assert is_first_clean_room_boot(None, conn=None, instance_id="main") is True


def test_first_clean_room_boot_only_legacy_rows_is_true(store, monkeypatch):
    """0-B: a prior LEGACY/smoke boot must NOT suppress the first clean-room
    cleanup (the per-boot audit row is written on every boot, mode='legacy')."""
    from live_boot_setup import is_first_clean_room_boot
    _seed_audit(store, monkeypatch, audit_table_exists=True,
                audit_rows_for_main=[
                    {"instance_id": "main", "mode": "legacy"},
                    {"instance_id": "main", "mode": "legacy"},
                ])
    assert is_first_clean_room_boot(None, conn=None, instance_id="main") is True


def test_rebaseline_clean_room_drawdown_resets_stale_backtest_peak():
    """2-B: a hydrated backtest drawdown peak is reset to live equity, halt cleared."""
    from live_boot_setup import rebaseline_clean_room_drawdown
    cache = {"_portfolio_drawdown_state": {"peak_value": 19977.60, "last_value": 18000.0,
                                           "halt_active": True, "up_days": 0}}
    new = rebaseline_clean_room_drawdown(cache, live_equity=5000.0)
    assert new["peak_value"] == 5000.0
    assert new["last_value"] == 5000.0
    assert new["halt_active"] is False
    assert cache["_portfolio_drawdown_state"]["peak_value"] == 5000.0


def test_rebaseline_clean_room_drawdown_noop_when_absent():
    """No drawdown state -> nothing to reset (returns None)."""
    from live_boot_setup import rebaseline_clean_room_drawdown
    assert rebaseline_clean_room_drawdown({}, live_equity=5000.0) is None
    assert rebaseline_clean_room_drawdown({"_portfolio_drawdown_state": {}}, 5000.0) is None


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


def test_first_clean_room_boot_db_error_is_false(store, monkeypatch):
    """Defensive: if the store errors out we return False (skip cleanup)."""
    import live_boot_setup
    from live_boot_setup import is_first_clean_room_boot

    def _boom():
        raise RuntimeError("connection dropped")

    monkeypatch.setattr(live_boot_setup, "store", store)
    monkeypatch.setattr(store, "table_list", _boom)
    assert is_first_clean_room_boot(None, conn=None, instance_id="main") is False


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


# --------- should_reset_backtest_state_on_boot (Scope D A1/A2) ----------
# Gates the 2-B drawdown re-baseline AND the 2-E momentum strip so they fire
# only when the boot-hydrated state is BACKTEST-origin. A live-origin snapshot
# carries live-accumulated drawdown/halt + first_seen_price and must NEVER be
# reset (the Scope-C bug: it fired on every clean-room restart, silently
# clearing an active drawdown halt and the live high-water mark).


def test_should_reset_backtest_origin_resets():
    from live_boot_setup import should_reset_backtest_state_on_boot
    assert should_reset_backtest_state_on_boot("backtest", is_first_clean_room_boot=False) is True
    assert should_reset_backtest_state_on_boot("backtest", is_first_clean_room_boot=True) is True


def test_should_reset_live_origin_never_resets():
    """A live-origin snapshot must NOT be reset, even on a (mis-detected) first boot."""
    from live_boot_setup import should_reset_backtest_state_on_boot
    assert should_reset_backtest_state_on_boot("live", is_first_clean_room_boot=True) is False
    assert should_reset_backtest_state_on_boot("live", is_first_clean_room_boot=False) is False
    # case / whitespace insensitive
    assert should_reset_backtest_state_on_boot("  LIVE ", is_first_clean_room_boot=True) is False


def test_should_reset_unknown_origin_only_on_first_boot():
    """Legacy/blank-origin rows (no origin field): reset only on the genuine
    first clean-room boot; on a restart, preserve whatever was hydrated."""
    from live_boot_setup import should_reset_backtest_state_on_boot
    for origin in (None, "", "   ", "legacy"):
        assert should_reset_backtest_state_on_boot(origin, is_first_clean_room_boot=True) is True
        assert should_reset_backtest_state_on_boot(origin, is_first_clean_room_boot=False) is False


# --------- write_clean_room_cleanup_marker (Scope D A3) -----------------
# Scope C gated the destructive first-boot cleanup's idempotency solely on the
# forensic LiveBootAudit row, written AFTER _build_adapter (and after a sys.exit
# on adapter-build failure) on a SEPARATE connection. A first-boot cleanup
# followed by an adapter failure left no marker -> the next boot re-ran the wipe.
# A3 writes a dedicated 'cleanup-done' sentinel on the SAME connection right
# after cleanup, before _build_adapter.


def test_write_cleanup_marker_inserts_clean_room_sentinel(store, monkeypatch):
    import live_boot_setup
    from live_boot_setup import write_clean_room_cleanup_marker

    monkeypatch.setattr(live_boot_setup, "store", store)
    ok = write_clean_room_cleanup_marker(None, conn=None, instance_id="main")
    assert ok is True
    row = store.get("LiveBootAudit", "main|cleanup-done")
    assert row["mode"] == "clean_room"
    assert row["instance_id"] == "main"
    assert row["marker"] == "cleanup-done"
    # conflict="replace" keeps it idempotent across repeated boots.
    assert write_clean_room_cleanup_marker(None, conn=None, instance_id="main") is True
    assert store.count("LiveBootAudit") == 1


def test_write_cleanup_marker_makes_first_boot_false(store, monkeypatch):
    """The mode='clean_room' sentinel makes is_first_clean_room_boot False even
    if the later forensic audit row never gets written."""
    from live_boot_setup import is_first_clean_room_boot
    _seed_audit(store, monkeypatch, audit_table_exists=True,
                audit_rows_for_main=[{"instance_id": "main", "mode": "clean_room",
                                      "marker": "cleanup-done"}])
    assert is_first_clean_room_boot(None, conn=None, instance_id="main") is False


def test_write_cleanup_marker_empty_instance_id_is_noop():
    from live_boot_setup import write_clean_room_cleanup_marker
    assert write_clean_room_cleanup_marker(MagicMock(), MagicMock(), "") is False


def test_write_cleanup_marker_swallows_db_error(store, monkeypatch):
    """Best-effort: a DB failure returns False, never raises into broker boot."""
    import live_boot_setup
    from live_boot_setup import write_clean_room_cleanup_marker

    def _boom(*a, **k):
        raise RuntimeError("conn dropped")

    monkeypatch.setattr(live_boot_setup, "store", store)
    monkeypatch.setattr(store, "insert", _boom)
    assert write_clean_room_cleanup_marker(None, None, "main") is False
