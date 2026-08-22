"""Tests for backend.strategy_cache_persistence (Phase 1 snapshot extensions)."""

from __future__ import annotations

import pytest

from backend import strategy_cache_persistence as scp


def test_config_hash_stable_across_dict_order():
    """Same fields, different dict insertion order, must produce same hash."""
    cfg_a = {
        "strategy_name": "graph_nexus_analysis",
        "prompt_versions": {"sentiment": "v1", "macro": "v2"},
        "llm_stages": {"discovery": {"provider": "azure", "model": "gpt-5.4-mini", "effort": "medium"}},
        "history_scope_id_inputs": {"neo4j_uri": "bolt://x", "salt": ""},
        "lookback_learning_days": 120,
    }
    cfg_b = {
        "lookback_learning_days": 120,
        "history_scope_id_inputs": {"salt": "", "neo4j_uri": "bolt://x"},
        "llm_stages": {"discovery": {"effort": "medium", "model": "gpt-5.4-mini", "provider": "azure"}},
        "prompt_versions": {"macro": "v2", "sentiment": "v1"},
        "strategy_name": "graph_nexus_analysis",
    }
    assert scp._compute_config_hash(cfg_a) == scp._compute_config_hash(cfg_b)


def test_config_hash_changes_on_prompt_version_bump():
    """Bumping any prompt_version must change the hash."""
    cfg = {
        "strategy_name": "graph_nexus_analysis",
        "prompt_versions": {"sentiment": "v1"},
        "llm_stages": {},
        "history_scope_id_inputs": {},
        "lookback_learning_days": 120,
    }
    h1 = scp._compute_config_hash(cfg)
    cfg["prompt_versions"]["sentiment"] = "v2"
    h2 = scp._compute_config_hash(cfg)
    assert h1 != h2


def test_config_hash_ignores_neutral_fields():
    """Fields not in the canonical list must not affect the hash."""
    cfg_a = {
        "strategy_name": "graph_nexus_analysis",
        "prompt_versions": {"sentiment": "v1"},
        "llm_stages": {},
        "history_scope_id_inputs": {},
        "lookback_learning_days": 120,
        "display_name": "Nexus v1",
        "ui_theme": "dark",
        "operator_notes": "test config",
    }
    cfg_b = dict(cfg_a)
    cfg_b["display_name"] = "Nexus v999"
    cfg_b["ui_theme"] = "light"
    cfg_b["operator_notes"] = "different notes"
    assert scp._compute_config_hash(cfg_a) == scp._compute_config_hash(cfg_b)


def test_config_hash_invalid_input_returns_marker():
    assert scp._compute_config_hash(None) == "invalid"
    assert scp._compute_config_hash("not a dict") == "invalid"
    assert scp._compute_config_hash(42) == "invalid"


def test_module_hash_stable_for_same_file(tmp_path):
    """Same file contents must produce same hash across calls."""
    f = tmp_path / "fake_strategy.py"
    f.write_text("def run_once():\n    pass\n", encoding="utf-8")
    h1 = scp._compute_module_hash(str(f))
    h2 = scp._compute_module_hash(str(f))
    assert h1 == h2
    assert len(h1) == 16


def test_module_hash_changes_on_file_edit(tmp_path):
    """Edited file must produce different hash."""
    f = tmp_path / "fake_strategy.py"
    f.write_text("def run_once():\n    pass\n", encoding="utf-8")
    h1 = scp._compute_module_hash(str(f))
    f.write_text("def run_once():\n    return 1\n", encoding="utf-8")
    h2 = scp._compute_module_hash(str(f))
    assert h1 != h2


def test_module_hash_missing_file_returns_marker():
    assert scp._compute_module_hash("/nonexistent/path.py") == "missing"


def test_module_hash_normalizes_line_endings(tmp_path):
    """Bug-sweep 2026-05-21: same logical file with CRLF vs LF must hash
    identically, so a Windows-checkout backtest and a Linux-deploy live
    host agree on the module hash."""
    lf_file = tmp_path / "lf_strategy.py"
    crlf_file = tmp_path / "crlf_strategy.py"
    content_lf = b"def run_once():\n    return 1\n# end\n"
    content_crlf = b"def run_once():\r\n    return 1\r\n# end\r\n"
    lf_file.write_bytes(content_lf)
    crlf_file.write_bytes(content_crlf)
    h_lf = scp._compute_module_hash(str(lf_file))
    h_crlf = scp._compute_module_hash(str(crlf_file))
    assert h_lf == h_crlf, (
        f"line-ending normalization broken: LF hash {h_lf} != CRLF hash {h_crlf}"
    )
    assert len(h_lf) == 16


def test_serialize_cache_roundtrip_preserves_basic_types():
    cache = {
        "_momentum_watchlist": ["AAPL", "MSFT"],
        "_deployment_bar_index": 3,
        "_peak_hwm": {"AAPL": 195.5, "MSFT": 410.2},
        "_some_set": {"a", "b", "c"},
    }
    blob = scp._serialize_cache_for_blob(cache)
    restored = scp._deserialize_cache_from_blob(blob)
    assert restored["_momentum_watchlist"] == ["AAPL", "MSFT"]
    assert restored["_deployment_bar_index"] == 3
    assert restored["_peak_hwm"] == {"AAPL": 195.5, "MSFT": 410.2}
    assert restored["_some_set"] == {"a", "b", "c"}


def test_serialize_cache_records_skipped_fields():
    """Blacklisted keys must NOT appear in the blob, and the blob must
    record which keys were dropped via __skipped_fields__."""
    cache = {
        "_momentum_watchlist": ["AAPL"],
        "_neo4j_snapshot": {"big": "lru cache"},
        "_llm_trace_buffer": ["..."],
        "_eta_sector_map": {"AAPL": "XLK"},
    }
    blob = scp._serialize_cache_for_blob(cache)
    restored = scp._deserialize_cache_from_blob(blob)
    assert "_neo4j_snapshot" not in restored
    assert "_llm_trace_buffer" not in restored
    assert "_eta_sector_map" not in restored
    assert "_momentum_watchlist" in restored
    skipped = restored.get("__skipped_fields__", [])
    assert "_neo4j_snapshot" in skipped
    assert "_llm_trace_buffer" in skipped
    assert "_eta_sector_map" in skipped


def test_deserialize_corrupt_blob_raises_value_error():
    with pytest.raises(ValueError):
        scp._deserialize_cache_from_blob("not valid json{{{")


def test_deserialize_empty_blob_returns_empty_dict():
    assert scp._deserialize_cache_from_blob("") == {}
    assert scp._deserialize_cache_from_blob("{}") == {}


# ── Store-backed tests ────────────────────────────────────────────────────
#
# Postgres port (G11): ``conn`` and ``r`` are still on every signature but are
# ignored -- the module talks to db.store directly -- so these point the module
# at the FakeStore (or real Postgres under PG_TEST_DSN) instead of walking a
# ReQL chain. ``_ensure_table`` runs real DDL, which the FakeStore has no
# server for, so it is stubbed wherever the test is not about DDL.
import datetime as _dt

import pytest


@pytest.fixture
def scp_store(store, monkeypatch):
    monkeypatch.setattr(scp, "store", store)
    monkeypatch.setattr(scp, "_ensure_table", lambda conn=None, r=None: True)
    return store


def _rows(store):
    return store.run(scp.TABLE_NAME)


def _today_iso():
    return _dt.date.today().isoformat()


def _days_ago_iso(n):
    return (_dt.date.today() - _dt.timedelta(days=n)).isoformat()


def test_persist_backtest_snapshot_writes_row(scp_store):
    cache = {"_momentum_watchlist": ["AAPL"], "_deployment_bar_index": 5}
    ok = scp.persist_backtest_snapshot(
        conn=object(),
        r=None,
        instance_id="main",
        strategy_name="graph_nexus_analysis",
        cache=cache,
        config_hash="abc123def456",
        module_hash="0011223344556677",
        start_date="2025-11-10",
        end_date="2026-05-20",
    )
    assert ok is True
    rows = _rows(scp_store)
    assert len(rows) == 1
    row = rows[0]
    assert row["instance_id"] == "main"
    assert row["strategy_name"] == "graph_nexus_analysis"
    assert row["origin"] == "backtest"
    assert row["config_hash"] == "abc123def456"
    assert row["nexus_module_hash"] == "0011223344556677"
    assert row["start_date"] == "2025-11-10"
    assert row["end_date"] == "2026-05-20"
    assert row["record_version"] == 1
    assert row["id"] == "main|graph_nexus_analysis|abc123def456|backtest|2026-05-20"


def test_persist_backtest_snapshot_handles_db_error(store, monkeypatch):
    """If the write raises, the function logs but does not propagate."""
    def _boom(*a, **k):
        raise RuntimeError("database down")

    monkeypatch.setattr(scp, "store", store)
    monkeypatch.setattr(scp, "_ensure_table", lambda conn=None, r=None: True)
    monkeypatch.setattr(store, "insert", _boom)
    ok = scp.persist_backtest_snapshot(
        conn=object(), r=None,
        instance_id="main", strategy_name="graph_nexus_analysis",
        cache={}, config_hash="abc", module_hash="def",
        start_date="2025-11-10", end_date="2026-05-20",
    )
    assert ok is False


def test_persist_backtest_snapshot_rejects_blank_args(scp_store):
    assert scp.persist_backtest_snapshot(
        conn=None, r=None, instance_id="", strategy_name="x",
        cache={}, config_hash="a", module_hash="b",
        start_date="d", end_date="d",
    ) is False
    assert scp.persist_backtest_snapshot(
        conn=object(), r=None, instance_id="main", strategy_name="",
        cache={}, config_hash="a", module_hash="b",
        start_date="d", end_date="d",
    ) is False


def _seed_snapshot(store, row):
    store.insert(scp.TABLE_NAME, row, conflict="replace")


def _load(**overrides):
    kwargs = dict(
        conn=object(), r=None,
        instance_id="main", strategy_name="x",
        current_config_hash="abc", current_module_hash="mod",
        staleness_days=7,
    )
    kwargs.update(overrides)
    return scp.load_with_fallback(**kwargs)


def test_load_with_fallback_returns_match(scp_store):
    _seed_snapshot(scp_store, {
        "id": "main|graph_nexus_analysis|abc|backtest|" + _today_iso(),
        "instance_id": "main",
        "strategy_name": "graph_nexus_analysis",
        "origin": "backtest",
        "config_hash": "abc",
        "nexus_module_hash": "mod1",
        "end_date": _today_iso(),
        "cache_json": '{"_momentum_watchlist": ["AAPL"]}',
        "size_bytes": 42,
        "updated_at_epoch": 1.0,
    })
    cache, reason, meta = _load(strategy_name="graph_nexus_analysis",
                                current_module_hash="mod1")
    assert reason == "ok"
    assert cache == {"_momentum_watchlist": ["AAPL"]}
    assert meta is not None
    assert meta["end_date"] == _today_iso()
    assert meta["origin"] == "backtest"
    assert meta["config_hash"] == "abc"


def test_load_with_fallback_returns_no_match_on_empty(scp_store):
    cache, reason, meta = _load()
    assert cache is None
    assert reason == "no_match"
    assert meta is None


def test_load_with_fallback_ignores_another_instances_snapshot(scp_store):
    """The lookup is keyed on (instance_id, config_hash, strategy_name)."""
    _seed_snapshot(scp_store, {
        "id": "other|x|abc|backtest|" + _today_iso(),
        "instance_id": "other", "strategy_name": "x", "origin": "backtest",
        "config_hash": "abc", "nexus_module_hash": "mod",
        "end_date": _today_iso(), "cache_json": '{"k": 1}',
        "updated_at_epoch": 1.0,
    })
    cache, reason, meta = _load()
    assert (cache, reason, meta) == (None, "no_match", None)


def test_load_with_fallback_rejects_stale(scp_store):
    _seed_snapshot(scp_store, {
        "id": "main|x|abc|backtest|" + _days_ago_iso(30),
        "instance_id": "main", "strategy_name": "x", "origin": "backtest",
        "config_hash": "abc", "nexus_module_hash": "mod",
        "end_date": _days_ago_iso(30), "cache_json": '{"k": 1}',
        "updated_at_epoch": 1.0,
    })
    cache, reason, meta = _load()
    assert cache is None
    assert reason == "stale"
    assert meta is None


def test_load_with_fallback_rejects_module_drift(scp_store):
    _seed_snapshot(scp_store, {
        "id": "main|x|abc|backtest|" + _today_iso(),
        "instance_id": "main", "strategy_name": "x", "origin": "backtest",
        "config_hash": "abc", "nexus_module_hash": "mod_OLD",
        "end_date": _today_iso(), "cache_json": '{"k": 1}',
        "updated_at_epoch": 1.0,
    })
    cache, reason, meta = _load(current_module_hash="mod_NEW")
    assert cache is None
    assert reason == "module_drift"
    assert meta is None


def test_load_with_fallback_rejects_deserialize_error(scp_store):
    _seed_snapshot(scp_store, {
        "id": "main|x|abc|backtest|" + _today_iso(),
        "instance_id": "main", "strategy_name": "x", "origin": "backtest",
        "config_hash": "abc", "nexus_module_hash": "mod",
        "end_date": _today_iso(), "cache_json": "}{not valid json{{",
        "updated_at_epoch": 1.0,
    })
    cache, reason, meta = _load()
    assert cache is None
    assert reason == "deserialize_error"
    assert meta is None


def test_load_with_fallback_db_error_returns_none(store, monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("database down")

    monkeypatch.setattr(scp, "store", store)
    monkeypatch.setattr(scp, "_ensure_table", lambda conn=None, r=None: True)
    monkeypatch.setattr(store, "run", _boom)
    cache, reason, meta = _load()
    assert cache is None
    assert reason == "db_error"
    assert meta is None


def test_load_with_fallback_prefers_newer_updated_at_when_end_date_ties(scp_store):
    """Phase 1 bug-sweep regression: when two snapshots share the same
    ``end_date``, the secondary order must be ``updated_at_epoch`` (a real
    field set by both writers), not ``created_at`` (never written, so the
    tiebreaker was undefined before this fix).
    """
    same_day = _today_iso()
    for suffix, epoch, blob in (("older", 100.0, '{"k": "old"}'),
                                ("newer", 200.0, '{"k": "new"}')):
        _seed_snapshot(scp_store, {
            "id": f"main|x|abc|backtest|{same_day}|{suffix}",
            "instance_id": "main", "strategy_name": "x", "origin": "backtest",
            "config_hash": "abc", "nexus_module_hash": "mod",
            "end_date": same_day, "cache_json": blob,
            "updated_at_epoch": epoch,
        })
    cache, reason, _meta = _load()
    assert reason == "ok"
    assert cache == {"k": "new"}, "the tiebreaker must be updated_at_epoch DESC"


def test_load_with_fallback_env_flag_off(scp_store, monkeypatch):
    monkeypatch.setenv("NEXUS_LIVE_SNAPSHOT_LOAD", "off")
    _seed_snapshot(scp_store, {
        "id": "main|x|abc|backtest|" + _today_iso(),
        "instance_id": "main", "strategy_name": "x", "origin": "backtest",
        "config_hash": "abc", "nexus_module_hash": "mod",
        "end_date": _today_iso(), "cache_json": "{}", "updated_at_epoch": 1.0,
    })
    cache, reason, meta = _load()
    assert cache is None
    assert reason == "disabled"
    assert meta is None


@pytest.fixture
def unlatched_ensure(monkeypatch):
    """_ensure_table latches after its first success; reset it per test."""
    monkeypatch.setattr(scp, "_TABLE_ENSURED", [False])


def test_ensure_table_delegates_to_the_schema_registry(monkeypatch, unlatched_ensure):
    """R25: index DDL lives in db/schema.py, which already declares
    NexusStrategyCache's indexed fields. index_wait is gone -- CREATE INDEX
    is synchronous."""
    from db import schema as db_schema

    ensured = []
    monkeypatch.setattr(scp.db_schema, "ensure_table", ensured.append)
    assert scp._ensure_table(object(), None) is True
    assert ensured == [scp.TABLE_NAME]
    assert "instance_id" in set(db_schema.spec(scp.TABLE_NAME).indexed_fields)


def test_ensure_table_is_latched_after_the_first_success(monkeypatch,
                                                         unlatched_ensure):
    """Called once a tick in live; ensure_schema takes an advisory lock."""
    ensured = []
    monkeypatch.setattr(scp.db_schema, "ensure_table", ensured.append)
    assert scp._ensure_table() is True
    assert scp._ensure_table() is True
    assert ensured == [scp.TABLE_NAME]


def test_ensure_table_returns_false_when_ddl_fails(monkeypatch, unlatched_ensure):
    def _boom(_t):
        raise RuntimeError("no permission")

    monkeypatch.setattr(scp.db_schema, "ensure_table", _boom)
    assert scp._ensure_table(object(), None) is False
    # A failure must NOT latch -- the next call retries.
    assert scp._TABLE_ENSURED == [False]


def test_save_strategy_cache_writes_origin_live_when_hash_provided(scp_store):
    ok = scp.save_strategy_cache_to_db(
        conn=object(), r=None,
        instance_id="main", strategy_name="graph_nexus_analysis",
        cache={"_momentum_watchlist": ["AAPL"]},
        config_hash="abc",
        module_hash="mod",
        end_date=_today_iso(),
    )
    assert ok is True
    rows = _rows(scp_store)
    assert len(rows) == 1
    row = rows[0]
    assert row["origin"] == "live"
    assert row["config_hash"] == "abc"
    assert row["nexus_module_hash"] == "mod"
    assert row["end_date"] == _today_iso()
    assert row["id"] == f"main|graph_nexus_analysis|abc|live|{_today_iso()}"
    assert row["record_version"] == 1


def test_save_strategy_cache_back_compat_legacy_id_when_no_hash(scp_store):
    """Old callers that don't pass config_hash keep the original PK form."""
    ok = scp.save_strategy_cache_to_db(
        conn=object(), r=None,
        instance_id="main", strategy_name="graph_nexus_analysis",
        cache={"_momentum_watchlist": ["AAPL"]},
    )
    assert ok is True
    rows = _rows(scp_store)
    assert len(rows) == 1
    row = rows[0]
    assert row["id"] == "main|graph_nexus_analysis"
    # Legacy row MUST NOT have these new fields:
    assert "origin" not in row
    assert "config_hash" not in row
    assert "nexus_module_hash" not in row
    assert "end_date" not in row
    assert "record_version" not in row
