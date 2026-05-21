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


class _FakeQuery:
    """Chainable mock that records calls and returns itself."""
    def __init__(self, recorder, name="root"):
        self.recorder = recorder
        self.name = name
    def __getattr__(self, attr):
        return _FakeQuery(self.recorder, f"{self.name}.{attr}")
    def __call__(self, *args, **kwargs):
        self.recorder.append((self.name, args, kwargs))
        return _FakeQuery(self.recorder, f"{self.name}()")
    def __getitem__(self, key):
        return _FakeQuery(self.recorder, f"{self.name}[{key!r}]")


class _FakeR:
    def __init__(self, table_list=None, insert_result=None):
        self.recorder: list = []
        self._table_list = table_list or [scp.TABLE_NAME]
        self._insert_result = insert_result or {"inserted": 1, "errors": 0}
        self.row = _FakeQuery(self.recorder, "row")
    def db(self, name):
        return _FakeDB(self, name)
    def now(self):
        import datetime
        return datetime.datetime.utcnow()


class _FakeDB:
    def __init__(self, parent, name):
        self.parent = parent
        self.name = name
    def table(self, name):
        return _FakeTable(self.parent, self.name, name)
    def table_list(self):
        items = self.parent._table_list
        class _RunWrap:
            def run(self, conn): return list(items)
        return _RunWrap()
    def table_create(self, name):
        class _RunWrap:
            def run(self, conn): return {"created": 1}
        return _RunWrap()


class _FakeTable:
    def __init__(self, parent, db_name, name):
        self.parent = parent
        self.db_name = db_name
        self.name = name
    def insert(self, row, conflict=None):
        result = self.parent._insert_result
        recorder = self.parent.recorder
        recorder.append(("insert", row, conflict))
        class _RunWrap:
            def run(self, conn): return result
        return _RunWrap()
    def get(self, row_id):
        class _RunWrap:
            def run(self, conn): return None
        return _RunWrap()


def test_persist_backtest_snapshot_writes_row():
    fake_r = _FakeR()
    cache = {"_momentum_watchlist": ["AAPL"], "_deployment_bar_index": 5}
    ok = scp.persist_backtest_snapshot(
        conn=object(),
        r=fake_r,
        instance_id="main",
        strategy_name="graph_nexus_analysis",
        cache=cache,
        config_hash="abc123def456",
        module_hash="0011223344556677",
        start_date="2025-11-10",
        end_date="2026-05-20",
    )
    assert ok is True
    inserts = [call for call in fake_r.recorder if call[0] == "insert"]
    assert len(inserts) == 1
    row = inserts[0][1]
    assert row["instance_id"] == "main"
    assert row["strategy_name"] == "graph_nexus_analysis"
    assert row["origin"] == "backtest"
    assert row["config_hash"] == "abc123def456"
    assert row["nexus_module_hash"] == "0011223344556677"
    assert row["start_date"] == "2025-11-10"
    assert row["end_date"] == "2026-05-20"
    assert row["record_version"] == 1
    assert row["id"] == "main|graph_nexus_analysis|abc123def456|backtest|2026-05-20"


def test_persist_backtest_snapshot_handles_db_error():
    """If insert raises, the function logs but does not propagate."""
    class _RaisingR(_FakeR):
        def db(self, name):
            class _D:
                def table_list(self):
                    class _W:
                        def run(self, conn): raise RuntimeError("rethink down")
                    return _W()
            return _D()
    ok = scp.persist_backtest_snapshot(
        conn=object(),
        r=_RaisingR(),
        instance_id="main",
        strategy_name="graph_nexus_analysis",
        cache={},
        config_hash="abc",
        module_hash="def",
        start_date="2025-11-10",
        end_date="2026-05-20",
    )
    assert ok is False


def test_persist_backtest_snapshot_rejects_blank_args():
    fake_r = _FakeR()
    assert scp.persist_backtest_snapshot(
        conn=None, r=fake_r, instance_id="main", strategy_name="x",
        cache={}, config_hash="a", module_hash="b",
        start_date="d", end_date="d",
    ) is False
    assert scp.persist_backtest_snapshot(
        conn=object(), r=fake_r, instance_id="", strategy_name="x",
        cache={}, config_hash="a", module_hash="b",
        start_date="d", end_date="d",
    ) is False
