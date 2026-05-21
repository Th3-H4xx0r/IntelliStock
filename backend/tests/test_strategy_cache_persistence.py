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
    def desc(self, field):
        # Mirror rethinkdb's r.desc(): a sentinel passed into order_by().
        # Tests only need this to be a no-op callable that the chain stub
        # can ignore.
        return ("desc", field)


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

    def index_list(self):
        # Pretend the compound index already exists so _ensure_table is happy.
        class _W:
            def run(self, conn): return ["instance_id_config_hash"]
        return _W()

    def index_create(self, name, *args, **kwargs):
        class _W:
            def run(self, conn): return {"created": 1}
        return _W()

    def index_wait(self, name):
        class _W:
            def run(self, conn): return [{"ready": True}]
        return _W()


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


import datetime as _dt


class _FakeRWithSnapshot(_FakeR):
    """_FakeR variant that returns a specific snapshot row on query."""
    def __init__(self, snapshot_row=None, table_list=None):
        super().__init__(table_list=table_list)
        self._snapshot_row = snapshot_row
    def db(self, name):
        return _FakeDBWithSnapshot(self, name, self._snapshot_row)


class _FakeDBWithSnapshot(_FakeDB):
    def __init__(self, parent, name, snapshot_row):
        super().__init__(parent, name)
        self._snapshot_row = snapshot_row
    def table(self, name):
        return _FakeTableWithSnapshot(self.parent, self.name, name, self._snapshot_row)


class _FakeTableWithSnapshot(_FakeTable):
    def __init__(self, parent, db_name, name, snapshot_row):
        super().__init__(parent, db_name, name)
        self._snapshot_row = snapshot_row
    def get_all(self, key, index=None):
        return _ChainStub(self._snapshot_row)
    def index_list(self):
        # Pretend the compound index already exists so _ensure_table is happy.
        class _W:
            def run(self, conn): return ["instance_id_config_hash"]
        return _W()


class _ChainStub:
    """Chainable stub that returns the seeded row from any terminal `.run(conn)` call."""
    def __init__(self, row):
        self._row = row
    def filter(self, *a, **kw): return self
    def order_by(self, *a, **kw): return self
    def limit(self, *a, **kw): return self
    def nth(self, *a, **kw): return self
    def default(self, *a, **kw): return self
    def run(self, conn): return self._row


def _today_iso():
    return _dt.date.today().isoformat()


def _days_ago_iso(n):
    return (_dt.date.today() - _dt.timedelta(days=n)).isoformat()


def test_load_with_fallback_returns_match():
    row = {
        "id": "main|graph_nexus_analysis|abc|backtest|" + _today_iso(),
        "instance_id": "main",
        "strategy_name": "graph_nexus_analysis",
        "origin": "backtest",
        "config_hash": "abc",
        "nexus_module_hash": "mod1",
        "end_date": _today_iso(),
        "cache_json": '{"_momentum_watchlist": ["AAPL"]}',
        "size_bytes": 42,
    }
    fake_r = _FakeRWithSnapshot(snapshot_row=row)
    cache, reason, meta = scp.load_with_fallback(
        conn=object(), r=fake_r,
        instance_id="main", strategy_name="graph_nexus_analysis",
        current_config_hash="abc", current_module_hash="mod1",
        staleness_days=7,
    )
    assert reason == "ok"
    assert cache == {"_momentum_watchlist": ["AAPL"]}
    assert meta is not None
    assert meta["end_date"] == _today_iso()
    assert meta["origin"] == "backtest"
    assert meta["config_hash"] == "abc"


def test_load_with_fallback_returns_no_match_on_empty():
    fake_r = _FakeRWithSnapshot(snapshot_row=None)
    cache, reason, meta = scp.load_with_fallback(
        conn=object(), r=fake_r,
        instance_id="main", strategy_name="x",
        current_config_hash="abc", current_module_hash="mod",
        staleness_days=7,
    )
    assert cache is None
    assert reason == "no_match"
    assert meta is None


def test_load_with_fallback_rejects_stale():
    row = {
        "id": "main|x|abc|backtest|" + _days_ago_iso(30),
        "cache_json": '{"k": 1}',
        "end_date": _days_ago_iso(30),
        "nexus_module_hash": "mod",
        "origin": "backtest",
        "config_hash": "abc",
    }
    fake_r = _FakeRWithSnapshot(snapshot_row=row)
    cache, reason, meta = scp.load_with_fallback(
        conn=object(), r=fake_r,
        instance_id="main", strategy_name="x",
        current_config_hash="abc", current_module_hash="mod",
        staleness_days=7,
    )
    assert cache is None
    assert reason == "stale"
    assert meta is None


def test_load_with_fallback_rejects_module_drift():
    row = {
        "id": "main|x|abc|backtest|" + _today_iso(),
        "cache_json": '{"k": 1}',
        "end_date": _today_iso(),
        "nexus_module_hash": "mod_OLD",
        "origin": "backtest",
        "config_hash": "abc",
    }
    fake_r = _FakeRWithSnapshot(snapshot_row=row)
    cache, reason, meta = scp.load_with_fallback(
        conn=object(), r=fake_r,
        instance_id="main", strategy_name="x",
        current_config_hash="abc", current_module_hash="mod_NEW",
        staleness_days=7,
    )
    assert cache is None
    assert reason == "module_drift"
    assert meta is None


def test_load_with_fallback_rejects_deserialize_error():
    row = {
        "id": "main|x|abc|backtest|" + _today_iso(),
        "cache_json": "}{not valid json{{",
        "end_date": _today_iso(),
        "nexus_module_hash": "mod",
        "origin": "backtest",
        "config_hash": "abc",
    }
    fake_r = _FakeRWithSnapshot(snapshot_row=row)
    cache, reason, meta = scp.load_with_fallback(
        conn=object(), r=fake_r,
        instance_id="main", strategy_name="x",
        current_config_hash="abc", current_module_hash="mod",
        staleness_days=7,
    )
    assert cache is None
    assert reason == "deserialize_error"
    assert meta is None


def test_load_with_fallback_db_error_returns_none():
    class _RaisingR(_FakeR):
        def db(self, name):
            class _D:
                def table_list(self):
                    class _W:
                        def run(self, conn): raise RuntimeError("rethink down")
                    return _W()
            return _D()
    cache, reason, meta = scp.load_with_fallback(
        conn=object(), r=_RaisingR(),
        instance_id="main", strategy_name="x",
        current_config_hash="abc", current_module_hash="mod",
        staleness_days=7,
    )
    assert cache is None
    assert reason == "db_error"
    assert meta is None


def test_load_with_fallback_env_flag_off(monkeypatch):
    monkeypatch.setenv("NEXUS_LIVE_SNAPSHOT_LOAD", "off")
    fake_r = _FakeRWithSnapshot(snapshot_row={"cache_json": "{}", "end_date": _today_iso(), "nexus_module_hash": "mod", "origin": "backtest", "config_hash": "abc"})
    cache, reason, meta = scp.load_with_fallback(
        conn=object(), r=fake_r,
        instance_id="main", strategy_name="x",
        current_config_hash="abc", current_module_hash="mod",
        staleness_days=7,
    )
    assert cache is None
    assert reason == "disabled"
    assert meta is None


def test_ensure_table_creates_compound_index_when_missing():
    """When the table exists but the compound index doesn't, create it."""
    index_create_calls = []
    index_wait_calls = []

    class _R:
        row = _FakeQuery([], "row")
        def db(self, name): return _D(name)
        def now(self): import datetime; return datetime.datetime.utcnow()
        def desc(self, *a, **kw): return None
    class _D:
        def __init__(self, name): self.name = name
        def table_list(self):
            class _W:
                def run(self, conn): return [scp.TABLE_NAME]
            return _W()
        def table(self, name): return _T()
    class _T:
        def index_list(self):
            class _W:
                def run(self, conn): return []
            return _W()
        def index_create(self, name, *args, **kwargs):
            index_create_calls.append((name, args, kwargs))
            class _W:
                def run(self, conn): return {"created": 1}
            return _W()
        def index_wait(self, name):
            index_wait_calls.append(name)
            class _W:
                def run(self, conn): return [{"ready": True}]
            return _W()

    ok = scp._ensure_table(object(), _R())
    assert ok is True
    assert len(index_create_calls) == 1
    assert index_create_calls[0][0] == "instance_id_config_hash"
    assert index_wait_calls == ["instance_id_config_hash"]


def test_ensure_table_skips_index_create_when_present():
    create_calls = []
    class _R:
        row = _FakeQuery([], "row")
        def db(self, name): return _D()
        def now(self): import datetime; return datetime.datetime.utcnow()
    class _D:
        def table_list(self):
            class _W:
                def run(self, conn): return [scp.TABLE_NAME]
            return _W()
        def table(self, name): return _T()
    class _T:
        def index_list(self):
            class _W:
                def run(self, conn): return ["instance_id_config_hash"]
            return _W()
        def index_create(self, *a, **kw):
            create_calls.append(a)
            raise AssertionError("index_create should not be called")

    ok = scp._ensure_table(object(), _R())
    assert ok is True
    assert create_calls == []


def test_save_strategy_cache_writes_origin_live_when_hash_provided():
    fake_r = _FakeR()
    ok = scp.save_strategy_cache_to_db(
        conn=object(), r=fake_r,
        instance_id="main", strategy_name="graph_nexus_analysis",
        cache={"_momentum_watchlist": ["AAPL"]},
        config_hash="abc",
        module_hash="mod",
        end_date=_today_iso(),
    )
    assert ok is True
    inserts = [c for c in fake_r.recorder if c[0] == "insert"]
    assert len(inserts) == 1
    row = inserts[0][1]
    assert row["origin"] == "live"
    assert row["config_hash"] == "abc"
    assert row["nexus_module_hash"] == "mod"
    assert row["end_date"] == _today_iso()
    assert row["id"] == f"main|graph_nexus_analysis|abc|live|{_today_iso()}"
    assert row["record_version"] == 1


def test_save_strategy_cache_back_compat_legacy_id_when_no_hash():
    """Old callers that don't pass config_hash keep the original PK form."""
    fake_r = _FakeR()
    ok = scp.save_strategy_cache_to_db(
        conn=object(), r=fake_r,
        instance_id="main", strategy_name="graph_nexus_analysis",
        cache={"_momentum_watchlist": ["AAPL"]},
    )
    assert ok is True
    inserts = [c for c in fake_r.recorder if c[0] == "insert"]
    assert len(inserts) == 1
    row = inserts[0][1]
    assert row["id"] == "main|graph_nexus_analysis"
    # Legacy row MUST NOT have these new fields:
    assert "origin" not in row
    assert "config_hash" not in row
    assert "nexus_module_hash" not in row
    assert "end_date" not in row
    assert "record_version" not in row
