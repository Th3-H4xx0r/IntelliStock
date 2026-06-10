# Live-Mode Safe Startup — Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement Phase 1 of the live-mode safe-startup design — snapshot-from-backtest hydration for `_strategy_cache` at live boot, with extended cleanup script and pre-launch validation tool.

**Architecture:** Extend `backend/strategy_cache_persistence.py` to support a snapshot-and-version-aware schema on `NexusStrategyCache` (new `origin`, `config_hash`, `nexus_module_hash`, `start_date`, `end_date`, `record_version` columns; new compound PK). Backtest writes a row with `origin="backtest"` at end-of-run. Live boot queries by `(instance_id, config_hash)`, prefers latest by `end_date`, runs a gap-day lookback if a usable snapshot is found, falls back to full lookback otherwise. Extend the cleanup script to cover all 14 per-instance tables. Add a pre-launch validation script that produces a green/yellow/red readiness report.

**Tech Stack:** Python 3, RethinkDB (`rethinkdb` driver), pytest, JSON serialization, SHA256 hashing.

**Spec reference:** `docs/superpowers/specs/2026-05-21-live-mode-safe-startup-design.md`

---

## File Structure

**New files:**
- `backend/tests/test_strategy_cache_persistence.py` — unit tests for hash, serialize, load_with_fallback, persist_backtest_snapshot
- `backend/tests/test_broker_live_boot_with_snapshot.py` — integration tests that drive broker boot paths with mocked DB
- `backend/tests/test_clear_main_instance_lookback_state.py` — cleanup-script tests
- `scripts/validate_live_launch_readiness.py` — pre-launch validator
- `docs/runbooks/live-launch-checklist.md` — operator runbook

**Modified files:**
- `backend/strategy_cache_persistence.py` — new functions and schema fields; preserved existing API for back-compat
- `backend/broker.py` — backtest-path call site for `persist_backtest_snapshot`; live-boot call site for `load_with_fallback` + gap-day lookback; live-runtime save updated to include `origin` and `config_hash`
- `scripts/clear_main_instance_lookback_state.py` — extended to cover 10 additional tables

**Untouched (out of scope for Phase 1):**
- `backend/strategies/graph_nexus_analysis.py` — no strategy-logic changes
- `backend/broker_adapters/*` — no broker-adapter changes
- All frontend files

---

## Task 1: Add `_compute_config_hash` helper

**Files:**
- Modify: `backend/strategy_cache_persistence.py` (add new function near top, after imports)
- Test: `backend/tests/test_strategy_cache_persistence.py` (CREATE)

- [ ] **Step 1: Create the test file with the first failing test**

Create `backend/tests/test_strategy_cache_persistence.py` with this content:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest backend/tests/test_strategy_cache_persistence.py -v`

Expected: FAIL with `AttributeError: module 'backend.strategy_cache_persistence' has no attribute '_compute_config_hash'`.

- [ ] **Step 3: Add the `_compute_config_hash` implementation**

In `backend/strategy_cache_persistence.py`, near the top (after the `import json` and other imports, before the `TABLE_NAME` constant), add:

```python
import hashlib
```

Then near the other private helpers (after `_decode_json`, before `_ensure_table`), add:

```python
def _canonical_json(value: Any) -> str:
    """Deterministic JSON encoding: sorted keys, no whitespace."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _compute_config_hash(config: dict) -> str:
    """Compute a stable 16-char SHA256 hex of the behaviorally-significant
    portion of the strategy config.

    Fields included (all others are considered behaviorally neutral):
    - strategy_name
    - prompt_versions (all roles)
    - llm_stages (provider/model/effort per stage)
    - history_scope_id_inputs (neo4j_uri, neo4j_user, sentiment_cache_scope_salt,
      use_toon_format, num_articles_for_llm)
    - lookback_learning_days
    """
    if not isinstance(config, dict):
        return "invalid"
    canonical = {
        "strategy_name": config.get("strategy_name", ""),
        "prompt_versions": config.get("prompt_versions", {}),
        "llm_stages": config.get("llm_stages", {}),
        "history_scope_id_inputs": config.get("history_scope_id_inputs", {}),
        "lookback_learning_days": config.get("lookback_learning_days", 0),
    }
    blob = _canonical_json(canonical).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:16]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest backend/tests/test_strategy_cache_persistence.py -v`

Expected: 2 passed.

- [ ] **Step 5: Add neutral-field-ignore test and verify**

Append to `backend/tests/test_strategy_cache_persistence.py`:

```python
def test_config_hash_ignores_neutral_fields():
    """Fields not in the canonical list must not affect the hash."""
    cfg_a = {
        "strategy_name": "graph_nexus_analysis",
        "prompt_versions": {"sentiment": "v1"},
        "llm_stages": {},
        "history_scope_id_inputs": {},
        "lookback_learning_days": 120,
        "display_name": "Nexus v1",          # neutral
        "ui_theme": "dark",                   # neutral
        "operator_notes": "test config",      # neutral
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
```

Run: `python -m pytest backend/tests/test_strategy_cache_persistence.py -v`

Expected: 4 passed.

- [ ] **Step 6: Commit**

```bash
git add backend/strategy_cache_persistence.py backend/tests/test_strategy_cache_persistence.py
git commit -m "feat(strategy_cache): add _compute_config_hash for snapshot keying"
```

---

## Task 2: Add `_compute_module_hash` helper

**Files:**
- Modify: `backend/strategy_cache_persistence.py` (add function near `_compute_config_hash`)
- Test: `backend/tests/test_strategy_cache_persistence.py` (add tests)

- [ ] **Step 1: Add the failing tests**

Append to `backend/tests/test_strategy_cache_persistence.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest backend/tests/test_strategy_cache_persistence.py::test_module_hash_stable_for_same_file -v`

Expected: FAIL — `AttributeError`.

- [ ] **Step 3: Add `_compute_module_hash`**

In `backend/strategy_cache_persistence.py`, immediately after `_compute_config_hash`, add:

```python
def _compute_module_hash(file_path: str) -> str:
    """16-char SHA256 hex of file bytes. Returns 'missing' if file not readable.

    Used to detect strategy code changes between the time a snapshot was
    written and the time it's being loaded.
    """
    try:
        with open(file_path, "rb") as fh:
            data = fh.read()
    except Exception:
        return "missing"
    return hashlib.sha256(data).hexdigest()[:16]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest backend/tests/test_strategy_cache_persistence.py -v`

Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/strategy_cache_persistence.py backend/tests/test_strategy_cache_persistence.py
git commit -m "feat(strategy_cache): add _compute_module_hash for drift detection"
```

---

## Task 3: Add snapshot row builder + JSON serialization helpers

**Files:**
- Modify: `backend/strategy_cache_persistence.py`
- Test: `backend/tests/test_strategy_cache_persistence.py`

The existing `_coerce_for_json` and `_decode_json` already handle sets, OrderedDicts, ISO timestamps. We add a thin wrapper that builds a complete row dict for the new schema.

- [ ] **Step 1: Add failing tests**

Append to `backend/tests/test_strategy_cache_persistence.py`:

```python
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
        "_neo4j_snapshot": {"big": "lru cache"},  # blacklisted
        "_llm_trace_buffer": ["..."],              # blacklisted
        "_eta_sector_map": {"AAPL": "XLK"},        # blacklisted
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest backend/tests/test_strategy_cache_persistence.py::test_serialize_cache_roundtrip_preserves_basic_types -v`

Expected: FAIL — `AttributeError`.

- [ ] **Step 3: Add the serialize/deserialize wrappers**

In `backend/strategy_cache_persistence.py`, after `_decode_json` and before `_ensure_table`, add:

```python
def _serialize_cache_for_blob(cache: dict) -> str:
    """JSON-encode `cache` for storage in NexusStrategyCache.cache_json.

    Drops blacklisted keys (see _BLACKLIST_PREFIXES) and records dropped
    key names under `__skipped_fields__` so future readers can tell what
    was intentionally omitted.
    """
    if not isinstance(cache, dict):
        return "{}"
    filtered: dict = {}
    skipped: list = []
    for k, v in cache.items():
        if not isinstance(k, str):
            continue
        if _is_blacklisted(k):
            skipped.append(k)
            continue
        coerced = _coerce_for_json(v)
        if coerced is None and v is not None:
            skipped.append(k)
            continue
        filtered[k] = coerced
    if skipped:
        filtered["__skipped_fields__"] = sorted(set(skipped))
    return json.dumps(filtered, default=str)


def _deserialize_cache_from_blob(blob: str) -> dict:
    """Decode a cache_json blob written by _serialize_cache_for_blob.

    Raises ValueError on malformed JSON. Returns {} for empty input.
    """
    if not blob:
        return {}
    try:
        raw = json.loads(blob)
    except (ValueError, TypeError) as e:
        raise ValueError(f"corrupt cache blob: {e}") from e
    if not isinstance(raw, dict):
        return {}
    return _decode_json(raw)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest backend/tests/test_strategy_cache_persistence.py -v`

Expected: 11 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/strategy_cache_persistence.py backend/tests/test_strategy_cache_persistence.py
git commit -m "feat(strategy_cache): add _serialize/_deserialize_cache_for_blob with __skipped_fields__ marker"
```

---

## Task 4: Add `persist_backtest_snapshot`

**Files:**
- Modify: `backend/strategy_cache_persistence.py`
- Test: `backend/tests/test_strategy_cache_persistence.py`

- [ ] **Step 1: Add failing tests with mocked conn/r**

Append to `backend/tests/test_strategy_cache_persistence.py`:

```python
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
        # `r.row` for filter expressions
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
        class _RunWrap:
            def __init__(self, items): self._items = items
            def run(self, conn): return list(self._items)
        return _RunWrap(self.parent._table_list)
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
        class _RunWrap:
            def __init__(self, result, recorder, row, conflict):
                self._result = result
                recorder.append(("insert", row, conflict))
            def run(self, conn): return self._result
        return _RunWrap(self.parent._insert_result, self.parent.recorder, row, conflict)
    def get(self, row_id):
        class _RunWrap:
            def __init__(self): self._row = None
            def run(self, conn): return self._row
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
    # PK must include all the discriminators so backtest + live snapshots coexist
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest backend/tests/test_strategy_cache_persistence.py::test_persist_backtest_snapshot_writes_row -v`

Expected: FAIL — `AttributeError`.

- [ ] **Step 3: Implement `persist_backtest_snapshot`**

In `backend/strategy_cache_persistence.py`, after `save_strategy_cache_to_db`, add:

```python
def persist_backtest_snapshot(
    conn,
    r,
    *,
    instance_id: str,
    strategy_name: str,
    cache: dict,
    config_hash: str,
    module_hash: str,
    start_date: str,
    end_date: str,
    max_blob_bytes: int = 5_000_000,
) -> bool:
    """Write a backtest end-of-run snapshot of `_strategy_cache` to NexusStrategyCache.

    Row PK = "{instance_id}|{strategy_name}|{config_hash}|backtest|{end_date}"
    so backtest snapshots coexist with live runtime rows. Returns True on
    success, False otherwise. Never raises.
    """
    if conn is None or r is None or not instance_id or not strategy_name:
        return False
    if not config_hash or not module_hash:
        return False
    if not end_date:
        return False
    row_id = f"{instance_id}|{strategy_name}|{config_hash}|backtest|{end_date}"
    try:
        if not _ensure_table(conn, r):
            return False
        blob = _serialize_cache_for_blob(cache or {})
        if len(blob) > max_blob_bytes:
            # Same shrinking strategy as save_strategy_cache_to_db.
            payload = json.loads(blob)
            sized = sorted(
                payload.items(),
                key=lambda kv: len(json.dumps(kv[1], default=str)),
                reverse=True,
            )
            for k, _ in sized:
                if k == "__skipped_fields__":
                    continue
                payload.pop(k, None)
                payload.setdefault("__skipped_fields__", []).append(k)
                blob = json.dumps(payload, default=str)
                if len(blob) <= max_blob_bytes:
                    break
        r.db(DB_NAME).table(TABLE_NAME).insert(
            {
                "id": row_id,
                "instance_id": instance_id,
                "strategy_name": strategy_name,
                "origin": "backtest",
                "config_hash": config_hash,
                "nexus_module_hash": module_hash,
                "start_date": start_date,
                "end_date": end_date,
                "cache_json": blob,
                "size_bytes": len(blob),
                "updated_at": r.now(),
                "updated_at_epoch": time.time(),
                "record_version": 1,
            },
            conflict="replace",
        ).run(conn)
        return True
    except Exception:
        return False
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest backend/tests/test_strategy_cache_persistence.py -v`

Expected: 14 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/strategy_cache_persistence.py backend/tests/test_strategy_cache_persistence.py
git commit -m "feat(strategy_cache): add persist_backtest_snapshot writer"
```

---

## Task 5: Add `load_with_fallback`

**Files:**
- Modify: `backend/strategy_cache_persistence.py`
- Test: `backend/tests/test_strategy_cache_persistence.py`

- [ ] **Step 1: Add failing tests**

Append to `backend/tests/test_strategy_cache_persistence.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest backend/tests/test_strategy_cache_persistence.py::test_load_with_fallback_returns_match -v`

Expected: FAIL — `AttributeError`.

- [ ] **Step 3: Implement `load_with_fallback`**

In `backend/strategy_cache_persistence.py`, after `persist_backtest_snapshot`, add:

```python
def load_with_fallback(
    conn,
    r,
    *,
    instance_id: str,
    strategy_name: str,
    current_config_hash: str,
    current_module_hash: str,
    staleness_days: int = 7,
) -> tuple:
    """Try to hydrate `_strategy_cache` from a recent NexusStrategyCache row.

    Returns (cache_dict_or_None, reason, meta_or_None) where:
      reason ∈ {"ok", "disabled", "no_match", "stale", "module_drift",
                "deserialize_error", "db_error"}
      meta is a dict {"id", "origin", "end_date", "config_hash", "size_bytes"}
        on the "ok" path, None otherwise.
    """
    import os as _os
    if (_os.environ.get("NEXUS_LIVE_SNAPSHOT_LOAD", "on") or "on").lower() == "off":
        return None, "disabled", None
    if conn is None or r is None or not instance_id or not strategy_name:
        return None, "no_match", None
    try:
        if not _ensure_table(conn, r):
            return None, "db_error", None
        row = (
            r.db(DB_NAME).table(TABLE_NAME)
             .get_all([instance_id, current_config_hash], index="instance_id_config_hash")
             .filter(r.row["strategy_name"].eq(strategy_name))
             .order_by(r.desc("end_date"), r.desc("created_at"))
             .limit(1)
             .nth(0)
             .default(None)
             .run(conn)
        )
    except Exception:
        return None, "db_error", None
    if not row:
        return None, "no_match", None
    module_hash = row.get("nexus_module_hash", "")
    if module_hash != current_module_hash:
        return None, "module_drift", None
    end_date_str = row.get("end_date") or ""
    try:
        import datetime as _dt
        end_dt = _dt.date.fromisoformat(end_date_str)
        if (_dt.date.today() - end_dt).days > staleness_days:
            return None, "stale", None
    except Exception:
        return None, "stale", None
    try:
        cache = _deserialize_cache_from_blob(row.get("cache_json", "") or "")
    except ValueError:
        return None, "deserialize_error", None
    meta = {
        "id": row.get("id", ""),
        "origin": row.get("origin", ""),
        "end_date": end_date_str,
        "config_hash": row.get("config_hash", ""),
        "size_bytes": int(row.get("size_bytes", 0) or 0),
    }
    return cache, "ok", meta
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest backend/tests/test_strategy_cache_persistence.py -v`

Expected: 21 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/strategy_cache_persistence.py backend/tests/test_strategy_cache_persistence.py
git commit -m "feat(strategy_cache): add load_with_fallback with staleness + drift checks"
```

---

## Task 6: Add compound index `instance_id_config_hash` on first table-create

**Files:**
- Modify: `backend/strategy_cache_persistence.py`
- Test: `backend/tests/test_strategy_cache_persistence.py`

The `_ensure_table` function currently just creates the table without indexes. The load query needs a compound `(instance_id, config_hash)` secondary index. Add `index_create` + `index_wait` to `_ensure_table`.

- [ ] **Step 1: Add failing test**

Append to `backend/tests/test_strategy_cache_persistence.py`:

```python
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
        def index_create(self, name, fields):
            index_create_calls.append((name, fields))
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest backend/tests/test_strategy_cache_persistence.py::test_ensure_table_creates_compound_index_when_missing -v`

Expected: FAIL — current `_ensure_table` doesn't touch indexes.

- [ ] **Step 3: Update `_ensure_table`**

Replace the existing `_ensure_table` in `backend/strategy_cache_persistence.py`:

```python
_COMPOUND_INDEX_NAME = "instance_id_config_hash"


def _ensure_table(conn, r) -> bool:
    """Ensure NexusStrategyCache exists with the compound secondary index
    required for load_with_fallback's get_all query.

    Returns True if the table+index are usable, False on any error.
    """
    try:
        tables = list(r.db(DB_NAME).table_list().run(conn))
    except Exception:
        return False
    if TABLE_NAME not in tables:
        try:
            r.db(DB_NAME).table_create(TABLE_NAME).run(conn)
        except Exception:
            return False
    try:
        existing_indexes = list(r.db(DB_NAME).table(TABLE_NAME).index_list().run(conn))
    except Exception:
        return False
    if _COMPOUND_INDEX_NAME not in existing_indexes:
        try:
            r.db(DB_NAME).table(TABLE_NAME).index_create(
                _COMPOUND_INDEX_NAME,
                lambda doc: [doc["instance_id"], doc["config_hash"]],
            ).run(conn)
            r.db(DB_NAME).table(TABLE_NAME).index_wait(_COMPOUND_INDEX_NAME).run(conn)
        except Exception:
            # If create fails (e.g. existing rows lack the fields), the load
            # query will fall back to None; persistent failures will be
            # visible via the load_with_fallback "db_error" path.
            return False
    return True
```

Note: the test's `_T.index_create` accepts `(name, fields)` — adjust the test if signature differs from rethinkdb driver. The rethinkdb Python driver's signature is `index_create(name, [func])` where the function receives the row.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest backend/tests/test_strategy_cache_persistence.py -v`

Expected: 23 passed.

- [ ] **Step 5: Update test mock to match real index_create signature**

In `test_ensure_table_creates_compound_index_when_missing`, change the `_T.index_create` to accept any args and inspect them:

```python
def index_create(self, name, *args, **kwargs):
    index_create_calls.append((name, args, kwargs))
    class _W:
        def run(self, conn): return {"created": 1}
    return _W()
```

And the assertion:

```python
assert index_create_calls[0][0] == "instance_id_config_hash"
```

Run: `python -m pytest backend/tests/test_strategy_cache_persistence.py -v`

Expected: 23 passed.

- [ ] **Step 6: Commit**

```bash
git add backend/strategy_cache_persistence.py backend/tests/test_strategy_cache_persistence.py
git commit -m "feat(strategy_cache): create instance_id_config_hash compound index on ensure"
```

---

## Task 7: Update `save_strategy_cache_to_db` to include `origin` + `config_hash`

**Files:**
- Modify: `backend/strategy_cache_persistence.py`
- Test: `backend/tests/test_strategy_cache_persistence.py`

The existing live runtime save needs to write the new fields too, so live snapshots can be loaded by the new query. Maintain back-compat: callers that don't pass the new args still work (defaulting to legacy values for the old PK form).

- [ ] **Step 1: Add failing test**

Append to `backend/tests/test_strategy_cache_persistence.py`:

```python
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
    # back-compat: original 2-segment PK
    assert row["id"] == "main|graph_nexus_analysis"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest backend/tests/test_strategy_cache_persistence.py::test_save_strategy_cache_writes_origin_live_when_hash_provided -v`

Expected: FAIL — `save_strategy_cache_to_db` doesn't accept those kwargs yet.

- [ ] **Step 3: Update `save_strategy_cache_to_db`**

Replace the existing function in `backend/strategy_cache_persistence.py`:

```python
def save_strategy_cache_to_db(
    conn,
    r,
    instance_id: str,
    strategy_name: str,
    cache: dict,
    *,
    max_blob_bytes: int = 1_000_000,
    config_hash: Optional[str] = None,
    module_hash: Optional[str] = None,
    end_date: Optional[str] = None,
) -> bool:
    """Upsert `cache` dict for `(instance_id, strategy_name)`.

    Phase 1: if config_hash + module_hash + end_date are all provided, writes
    with the new 5-segment PK and origin="live" so the snapshot loader can
    pick it up on next boot. Otherwise writes with the legacy 2-segment PK
    (back-compat for callers that haven't been updated yet).
    """
    if conn is None or r is None or not instance_id or not strategy_name:
        return False
    if not isinstance(cache, dict):
        return False

    use_new_schema = bool(config_hash and module_hash and end_date)
    if use_new_schema:
        row_id = f"{instance_id}|{strategy_name}|{config_hash}|live|{end_date}"
    else:
        row_id = f"{instance_id}|{strategy_name}"

    try:
        filtered = {
            k: _coerce_for_json(v)
            for k, v in cache.items()
            if not _is_blacklisted(k)
        }
        filtered = {k: v for k, v in filtered.items() if v is not None}
        blob = json.dumps(filtered, default=str)
        if len(blob) > max_blob_bytes:
            sized = sorted(
                filtered.items(),
                key=lambda kv: len(json.dumps(kv[1], default=str)),
                reverse=True,
            )
            for k, _ in sized:
                filtered.pop(k, None)
                blob = json.dumps(filtered, default=str)
                if len(blob) <= max_blob_bytes:
                    break
        if not _ensure_table(conn, r):
            return False
        row = {
            "id": row_id,
            "instance_id": instance_id,
            "strategy_name": strategy_name,
            "cache_json": blob,
            "size_bytes": len(blob),
            "updated_at": r.now(),
            "updated_at_epoch": time.time(),
        }
        if use_new_schema:
            row.update(
                {
                    "origin": "live",
                    "config_hash": config_hash,
                    "nexus_module_hash": module_hash,
                    "end_date": end_date,
                    "record_version": 1,
                }
            )
        r.db(DB_NAME).table(TABLE_NAME).insert(row, conflict="replace").run(conn)
        return True
    except Exception:
        return False
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest backend/tests/test_strategy_cache_persistence.py -v`

Expected: 25 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/strategy_cache_persistence.py backend/tests/test_strategy_cache_persistence.py
git commit -m "feat(strategy_cache): extend save_strategy_cache_to_db with origin/config_hash/end_date"
```

---

## Task 8: Identify exact broker.py insertion sites

This task is **investigative** — no production code change. Output is a written note attached to the next commit so the implementing engineer in Tasks 9–11 has exact line numbers.

**Files:**
- Read only: `backend/broker.py`

- [ ] **Step 1: Find the live boot strategy-cache load site**

Run: `grep -n "load_strategy_cache_from_db" backend/broker.py`

Expected: at least one hit, probably near line 5070. Record the line number and the ~20 lines of surrounding context.

- [ ] **Step 2: Find the live runtime save_strategy_cache_to_db call(s)**

Run: `grep -n "save_strategy_cache_to_db" backend/broker.py`

Record line number(s). There may be one in a periodic save loop and one in a shutdown hook.

- [ ] **Step 3: Find the backtest main loop end / BacktestResults write site**

Run: `grep -n "BacktestResults" backend/broker.py`

Expected: writes at ~6375 and ~8775 (per session #4 audit). The end of the BACKTEST main loop is where `persist_backtest_snapshot` will be called. Find the line that completes the main backtest loop (look for "for current_time" or similar termination block).

- [ ] **Step 4: Find the `_run_live_historic_lookback` invocation**

Run: `grep -n "_run_live_historic_lookback" backend/broker.py`

Record the call site and the function signature (current parameters).

- [ ] **Step 5: Find the live-mode entry point**

Run: `grep -n "elif mode == MODE_LIVE" backend/broker.py`

Record the line — Task 9 will add the snapshot-load call inside this block.

- [ ] **Step 6: Write findings into a temp note file for use by Tasks 9–11**

Write the line numbers to `/tmp/broker_phase1_notes.txt` (or wherever convenient). This file is not committed — it's working notes.

- [ ] **Step 7: (No commit — investigation only)**

---

## Task 9: Wire backtest end-of-run snapshot persist into broker.py

**Files:**
- Modify: `backend/broker.py` (one new call, ~30 LOC inc. logging)
- Test: `backend/tests/test_broker_live_boot_with_snapshot.py` (CREATE)

- [ ] **Step 1: Run GitNexus impact analysis**

Per project CLAUDE.md, before editing a broker.py symbol, check the blast radius. Find the function name where you'll insert the call (e.g., `run_backtest` or the equivalent), then run via the GitNexus MCP tool:

```
gitnexus_impact({target: "<function_name>", direction: "upstream"})
```

Record the risk level. If HIGH or CRITICAL, flag it and stop for review before continuing.

- [ ] **Step 2: Create the integration-test file with first failing test**

Create `backend/tests/test_broker_live_boot_with_snapshot.py`:

```python
"""Integration tests for broker live-boot snapshot wiring (Phase 1)."""

from __future__ import annotations

import datetime as dt
import pytest

from backend import strategy_cache_persistence as scp


def _today_iso():
    return dt.date.today().isoformat()


def test_backtest_persist_snapshot_called_with_expected_fields(monkeypatch):
    """When the backtest path completes, persist_backtest_snapshot must be
    called once with the merged _strategy_cache, current config_hash, and
    current module_hash."""
    captured = {}

    def _fake_persist(conn, r, *, instance_id, strategy_name, cache,
                     config_hash, module_hash, start_date, end_date,
                     max_blob_bytes=5_000_000):
        captured.update({
            "instance_id": instance_id,
            "strategy_name": strategy_name,
            "cache": cache,
            "config_hash": config_hash,
            "module_hash": module_hash,
            "start_date": start_date,
            "end_date": end_date,
        })
        return True

    monkeypatch.setattr(scp, "persist_backtest_snapshot", _fake_persist)

    # Import the helper that wraps the call from broker (added in step 4 below).
    from backend import broker
    broker._invoke_persist_backtest_snapshot(
        conn=object(),
        r=object(),
        base_instance_id="main",
        strategy_name="graph_nexus_analysis",
        strategy_cache={"_momentum_watchlist": ["AAPL"]},
        config_dict={
            "strategy_name": "graph_nexus_analysis",
            "prompt_versions": {"sentiment": "v1"},
            "llm_stages": {},
            "history_scope_id_inputs": {},
            "lookback_learning_days": 120,
        },
        start_date="2025-11-10",
        end_date=_today_iso(),
    )

    assert captured["instance_id"] == "main"
    assert captured["strategy_name"] == "graph_nexus_analysis"
    assert captured["cache"] == {"_momentum_watchlist": ["AAPL"]}
    assert captured["start_date"] == "2025-11-10"
    assert captured["end_date"] == _today_iso()
    # config_hash and module_hash are computed inside the helper — just sanity-check format
    assert len(captured["config_hash"]) == 16
    assert len(captured["module_hash"]) in (16, 7)  # 16 if file readable; "missing" else
```

- [ ] **Step 3: Run to confirm it fails**

Run: `python -m pytest backend/tests/test_broker_live_boot_with_snapshot.py::test_backtest_persist_snapshot_called_with_expected_fields -v`

Expected: FAIL with `AttributeError: module 'backend.broker' has no attribute '_invoke_persist_backtest_snapshot'`.

- [ ] **Step 4: Add the helper to `backend/broker.py`**

Locate a stable area near the top of `backend/broker.py` (after imports, before the main loop functions). Add:

```python
def _invoke_persist_backtest_snapshot(
    conn,
    r,
    *,
    base_instance_id: str,
    strategy_name: str,
    strategy_cache: dict,
    config_dict: dict,
    start_date: str,
    end_date: str,
) -> bool:
    """Compute hashes and call strategy_cache_persistence.persist_backtest_snapshot.

    Wrapped in try/except so a snapshot failure never breaks the backtest itself.
    Returns True on success, False otherwise.
    """
    import os as _os
    if (_os.environ.get("NEXUS_BACKTEST_SNAPSHOT_WRITE", "on") or "on").lower() == "off":
        return False
    if not base_instance_id or not strategy_name:
        return False
    try:
        from backend import strategy_cache_persistence as _scp
        config_hash = _scp._compute_config_hash(config_dict)
        # Resolve graph_nexus_analysis.py path relative to this module
        from backend.strategies import graph_nexus_analysis as _gna
        module_path = getattr(_gna, "__file__", "")
        module_hash = _scp._compute_module_hash(module_path) if module_path else "missing"
        ok = _scp.persist_backtest_snapshot(
            conn=conn,
            r=r,
            instance_id=base_instance_id,
            strategy_name=strategy_name,
            cache=strategy_cache,
            config_hash=config_hash,
            module_hash=module_hash,
            start_date=start_date,
            end_date=end_date,
        )
        if ok:
            print(f"[snapshot] persisted: id={base_instance_id}|{strategy_name}|{config_hash}|backtest|{end_date}")
        else:
            print(f"[snapshot] persist FAILED for id={base_instance_id}|{strategy_name}|{config_hash}|backtest|{end_date}")
        return ok
    except Exception as e:
        print(f"[snapshot] persist raised (suppressed): {e}")
        return False
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest backend/tests/test_broker_live_boot_with_snapshot.py -v`

Expected: 1 passed.

- [ ] **Step 6: Wire the helper into the backtest path**

Using the line numbers identified in Task 8 Step 3, locate the end of the backtest main loop (where it finishes processing all bars but before the BacktestResults write — or just after BacktestResults write). Just before the broker exits the backtest mode branch (search for the `BacktestResults` write completion and add this call just after it):

```python
# Phase 1 snapshot: persist final _strategy_cache for live-boot reuse
try:
    _invoke_persist_backtest_snapshot(
        conn=conn,
        r=r,
        base_instance_id=base_instance_id or instance_id or "",
        strategy_name=strategy_spec.get("name") or "graph_nexus_analysis",
        strategy_cache=_strategy_cache.get(strategy_spec.get("name") or "graph_nexus_analysis", {}),
        config_dict={
            "strategy_name": strategy_spec.get("name") or "graph_nexus_analysis",
            "prompt_versions": _collect_prompt_versions(strategy_spec.get("config", {})),
            "llm_stages": _collect_llm_stages(strategy_spec.get("config", {})),
            "history_scope_id_inputs": _collect_history_scope_inputs(strategy_spec.get("config", {})),
            "lookback_learning_days": int(strategy_spec.get("config", {}).get("lookback_learning_days", 120)),
        },
        start_date=str(start_date) if start_date else "",
        end_date=str(end_date) if end_date else "",
    )
except Exception as e:
    print(f"[snapshot] wrap-up failed (suppressed): {e}")
```

If `_collect_prompt_versions`, `_collect_llm_stages`, `_collect_history_scope_inputs` don't exist as helpers (they probably don't — they're shorthand for what to plumb in), implement them as trivial dict-pluck helpers right before `_invoke_persist_backtest_snapshot`:

```python
def _collect_prompt_versions(cfg: dict) -> dict:
    return {
        "sentiment": cfg.get("nexus_sentiment_prompt_version", ""),
        "macro": cfg.get("nexus_macro_prompt_version", ""),
        "company": cfg.get("nexus_company_prompt_version", ""),
        "discovery": cfg.get("nexus_discovery_prompt_version", ""),
        "analyst": cfg.get("nexus_analyst_prompt_version", ""),
    }


def _collect_llm_stages(cfg: dict) -> dict:
    out = {}
    for stage in ("discovery", "sentiment", "macro", "company", "analyst"):
        out[stage] = {
            "provider": cfg.get(f"{stage}_llm_provider", ""),
            "model": cfg.get(f"{stage}_llm_model", ""),
            "effort": cfg.get(f"{stage}_llm_effort", ""),
        }
    return out


def _collect_history_scope_inputs(cfg: dict) -> dict:
    return {
        "neo4j_uri": cfg.get("neo4j_uri", ""),
        "neo4j_user": cfg.get("neo4j_user", ""),
        "sentiment_cache_scope_salt": cfg.get("sentiment_cache_scope_salt", ""),
        "use_toon_format": bool(cfg.get("use_toon_format", False)),
        "num_articles_for_llm": int(cfg.get("num_articles_for_llm", 0)),
    }
```

If the actual field names in the config dict differ (verify against `backend/strategies/graph_nexus_analysis.py` constants like `_NEXUS_*_PROMPT_VERSION`), update the helpers to use the real names. Confirm by reading those constants in `backend/strategies/graph_nexus_analysis.py` and matching to whatever the strategy config dict spelling is.

- [ ] **Step 7: Verify the backtest path syntax check**

Run: `python -c "import backend.broker"`

Expected: no errors.

- [ ] **Step 8: Commit**

```bash
git add backend/broker.py backend/tests/test_broker_live_boot_with_snapshot.py
git commit -m "feat(broker): persist _strategy_cache snapshot at end of backtest run"
```

---

## Task 10: Wire live-boot snapshot load + gap-day lookback into broker.py

**Files:**
- Modify: `backend/broker.py` (~80 LOC)
- Test: `backend/tests/test_broker_live_boot_with_snapshot.py`

- [ ] **Step 1: GitNexus impact analysis on the live-boot strategy-cache section**

The current code calls `load_strategy_cache_from_db` somewhere around `broker.py:5070`. Identify the enclosing function name (likely `_load_live_strategy_caches` or similar; if no enclosing function, the broker.py main flow itself).

Run: `gitnexus_impact({target: "<enclosing_function>", direction: "upstream"})`.

Record risk. Stop and flag if HIGH/CRITICAL.

- [ ] **Step 2: Add failing test for the wrapper**

Append to `backend/tests/test_broker_live_boot_with_snapshot.py`:

```python
def test_live_boot_loads_snapshot_when_present(monkeypatch):
    """Snapshot found + not stale + no module drift → hydrate from blob,
    compute gap_dates as small list."""
    monkeypatch.setattr(scp, "load_with_fallback", lambda *a, **kw: (
        {"_momentum_watchlist": ["AAPL"]}, "ok"
    ))
    from backend import broker
    cache, reason, gap_dates = broker._invoke_load_snapshot_with_gap(
        conn=object(),
        r=object(),
        instance_id="main",
        strategy_name="graph_nexus_analysis",
        current_config_hash="abc",
        current_module_hash="mod",
        snapshot_end_date_iso=_today_iso(),
        today_iso=_today_iso(),
    )
    assert reason == "ok"
    assert cache == {"_momentum_watchlist": ["AAPL"]}
    assert gap_dates == []  # snapshot ends today → no gap


def test_live_boot_returns_gap_dates_when_snapshot_partial(monkeypatch):
    """Snapshot ends 3 days ago → gap_dates is the trading days between."""
    monkeypatch.setattr(scp, "load_with_fallback", lambda *a, **kw: (
        {"k": 1}, "ok"
    ))
    from backend import broker
    three_days_ago = (dt.date.today() - dt.timedelta(days=3)).isoformat()
    cache, reason, gap_dates = broker._invoke_load_snapshot_with_gap(
        conn=object(),
        r=object(),
        instance_id="main",
        strategy_name="graph_nexus_analysis",
        current_config_hash="abc",
        current_module_hash="mod",
        snapshot_end_date_iso=three_days_ago,
        today_iso=_today_iso(),
    )
    assert reason == "ok"
    # 3 calendar days → 1-3 trading days depending on weekends; just check non-empty
    assert isinstance(gap_dates, list)
    assert all(d > three_days_ago for d in gap_dates)
    assert all(d <= _today_iso() for d in gap_dates)


def test_live_boot_falls_back_when_snapshot_missing(monkeypatch):
    monkeypatch.setattr(scp, "load_with_fallback", lambda *a, **kw: (None, "no_match"))
    from backend import broker
    cache, reason, gap_dates = broker._invoke_load_snapshot_with_gap(
        conn=object(),
        r=object(),
        instance_id="main",
        strategy_name="graph_nexus_analysis",
        current_config_hash="abc",
        current_module_hash="mod",
        snapshot_end_date_iso="",
        today_iso=_today_iso(),
    )
    assert cache is None
    assert reason == "no_match"
    assert gap_dates is None  # signals "run full lookback"
```

- [ ] **Step 3: Run to confirm tests fail**

Run: `python -m pytest backend/tests/test_broker_live_boot_with_snapshot.py -v`

Expected: 3 failures, 1 pass (from Task 9).

- [ ] **Step 4: Add the wrapper function to `backend/broker.py`**

Near `_invoke_persist_backtest_snapshot` (added in Task 9), add:

```python
def _invoke_load_snapshot_with_gap(
    conn,
    r,
    *,
    instance_id: str,
    strategy_name: str,
    current_config_hash: str,
    current_module_hash: str,
    snapshot_end_date_iso: str,
    today_iso: str,
    staleness_days: int = 7,
) -> tuple:
    """Call load_with_fallback and compute gap-day list for shortened lookback.

    Returns (cache_dict_or_None, reason, gap_dates_list_or_None).
    - cache=None, gap_dates=None  → snapshot not usable; caller runs FULL lookback
    - cache=dict, gap_dates=[]    → snapshot fresh; caller skips lookback entirely
    - cache=dict, gap_dates=[...] → snapshot partial; caller runs short lookback for those days
    """
    from backend import strategy_cache_persistence as _scp
    cache, reason = _scp.load_with_fallback(
        conn=conn, r=r,
        instance_id=instance_id, strategy_name=strategy_name,
        current_config_hash=current_config_hash,
        current_module_hash=current_module_hash,
        staleness_days=staleness_days,
    )
    if cache is None:
        return None, reason, None
    # Compute trading days between snapshot end_date and today_iso (exclusive of start, inclusive of today).
    try:
        import datetime as _dt
        end_dt = _dt.date.fromisoformat(snapshot_end_date_iso) if snapshot_end_date_iso else _dt.date.fromisoformat(today_iso)
        today_dt = _dt.date.fromisoformat(today_iso)
        gap_dates = []
        cursor = end_dt + _dt.timedelta(days=1)
        while cursor <= today_dt:
            # Mon=0..Sun=6 — weekdays only. Holidays are handled later by the lookback's
            # existing skip-if-no-data logic; the gap list is permissive.
            if cursor.weekday() < 5:
                gap_dates.append(cursor.isoformat())
            cursor += _dt.timedelta(days=1)
        return cache, reason, gap_dates
    except Exception:
        # Bad date parse → conservative: return cache but no gap (caller will treat
        # an empty list as "snapshot covers today, no lookback needed").
        return cache, reason, []
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest backend/tests/test_broker_live_boot_with_snapshot.py -v`

Expected: 4 passed.

- [ ] **Step 6: Update `_invoke_load_snapshot_with_gap` to read `end_date` from meta**

The wrapper currently takes `snapshot_end_date_iso` and `today_iso` as parameters. Since Task 5 already returns `meta` from `load_with_fallback`, simplify the wrapper to read end_date from `meta` and compute today internally. Replace the function body added in Step 4 with this final version:

```python
def _invoke_load_snapshot_with_gap(
    conn,
    r,
    *,
    instance_id: str,
    strategy_name: str,
    current_config_hash: str,
    current_module_hash: str,
    staleness_days: int = 7,
) -> tuple:
    """Call load_with_fallback and compute gap-day list for shortened lookback.

    Returns (cache_dict_or_None, reason, gap_dates_list_or_None).
    - cache=None, gap_dates=None  → snapshot not usable; caller runs FULL lookback
    - cache=dict, gap_dates=[]    → snapshot fresh; caller skips lookback entirely
    - cache=dict, gap_dates=[...] → snapshot partial; caller runs short lookback for those days
    """
    from backend import strategy_cache_persistence as _scp
    cache, reason, meta = _scp.load_with_fallback(
        conn=conn, r=r,
        instance_id=instance_id, strategy_name=strategy_name,
        current_config_hash=current_config_hash,
        current_module_hash=current_module_hash,
        staleness_days=staleness_days,
    )
    if cache is None:
        return None, reason, None
    try:
        import datetime as _dt
        end_date_str = (meta or {}).get("end_date", "") if meta else ""
        end_dt = _dt.date.fromisoformat(end_date_str) if end_date_str else _dt.date.today()
        today_dt = _dt.date.today()
        gap_dates = []
        cursor = end_dt + _dt.timedelta(days=1)
        while cursor <= today_dt:
            if cursor.weekday() < 5:  # weekdays only; holidays skipped by lookback's existing data-availability check
                gap_dates.append(cursor.isoformat())
            cursor += _dt.timedelta(days=1)
        return cache, reason, gap_dates
    except Exception:
        # Bad date parse → conservative: return cache but no gap.
        return cache, reason, []
```

Note: this replaces the function body added in Step 4. The test in Step 2 calls `_invoke_load_snapshot_with_gap` without `snapshot_end_date_iso` or `today_iso` arguments — update the test accordingly.

- [ ] **Step 7: Update the test calls to match the new wrapper signature**

In `backend/tests/test_broker_live_boot_with_snapshot.py`, locate the 3 tests added in Step 2 (`test_live_boot_loads_snapshot_when_present`, `test_live_boot_returns_gap_dates_when_snapshot_partial`, `test_live_boot_falls_back_when_snapshot_missing`). Remove the `snapshot_end_date_iso=` and `today_iso=` kwargs from each call. The tests now look like:

```python
def test_live_boot_loads_snapshot_when_present(monkeypatch):
    monkeypatch.setattr(scp, "load_with_fallback", lambda *a, **kw: (
        {"_momentum_watchlist": ["AAPL"]}, "ok", {"end_date": _today_iso()}
    ))
    from backend import broker
    cache, reason, gap_dates = broker._invoke_load_snapshot_with_gap(
        conn=object(), r=object(),
        instance_id="main", strategy_name="graph_nexus_analysis",
        current_config_hash="abc", current_module_hash="mod",
    )
    assert reason == "ok"
    assert cache == {"_momentum_watchlist": ["AAPL"]}
    assert gap_dates == []


def test_live_boot_returns_gap_dates_when_snapshot_partial(monkeypatch):
    three_days_ago = (dt.date.today() - dt.timedelta(days=3)).isoformat()
    monkeypatch.setattr(scp, "load_with_fallback", lambda *a, **kw: (
        {"k": 1}, "ok", {"end_date": three_days_ago}
    ))
    from backend import broker
    cache, reason, gap_dates = broker._invoke_load_snapshot_with_gap(
        conn=object(), r=object(),
        instance_id="main", strategy_name="graph_nexus_analysis",
        current_config_hash="abc", current_module_hash="mod",
    )
    assert reason == "ok"
    assert isinstance(gap_dates, list)
    assert all(d > three_days_ago for d in gap_dates)
    assert all(d <= _today_iso() for d in gap_dates)


def test_live_boot_falls_back_when_snapshot_missing(monkeypatch):
    monkeypatch.setattr(scp, "load_with_fallback", lambda *a, **kw: (None, "no_match", None))
    from backend import broker
    cache, reason, gap_dates = broker._invoke_load_snapshot_with_gap(
        conn=object(), r=object(),
        instance_id="main", strategy_name="graph_nexus_analysis",
        current_config_hash="abc", current_module_hash="mod",
    )
    assert cache is None
    assert reason == "no_match"
    assert gap_dates is None
```

- [ ] **Step 8: Wire the wrapper into the live-mode entry point**

In `backend/broker.py`, locate the live-boot strategy cache section (around line 5057-5309 per session #8 audit). Find the existing call to `load_strategy_cache_from_db`. Replace it with:

```python
# Phase 1 snapshot load — try snapshot first, fall back to legacy load + full lookback.
_snap_cache, _snap_reason, _gap_dates = _invoke_load_snapshot_with_gap(
    conn=conn,
    r=r,
    instance_id=instance_id,
    strategy_name=strategy_name,
    current_config_hash=_scp._compute_config_hash({
        "strategy_name": strategy_name,
        "prompt_versions": _collect_prompt_versions(strategy_spec.get("config", {})),
        "llm_stages": _collect_llm_stages(strategy_spec.get("config", {})),
        "history_scope_id_inputs": _collect_history_scope_inputs(strategy_spec.get("config", {})),
        "lookback_learning_days": int(strategy_spec.get("config", {}).get("lookback_learning_days", 120)),
    }),
    current_module_hash=_scp._compute_module_hash(_gna.__file__ if hasattr(_gna, "__file__") else ""),
)
print(f"[snapshot] decision: reason={_snap_reason} gap_days={None if _gap_dates is None else len(_gap_dates)}")
if _snap_cache is not None:
    _strategy_cache.setdefault(strategy_name, {}).update(_snap_cache)
    print(f"[snapshot] hydrated {len(_snap_cache)} keys into _strategy_cache[{strategy_name!r}]")
else:
    # Fall back to legacy per-instance row load (current behavior).
    _legacy = load_strategy_cache_from_db(conn, r, instance_id, strategy_name)
    if _legacy:
        _strategy_cache.setdefault(strategy_name, {}).update(_legacy)
        print(f"[snapshot] legacy row loaded: {len(_legacy)} keys")
```

Make sure these imports exist at the top of the function or module:

```python
from backend import strategy_cache_persistence as _scp
from backend.strategies import graph_nexus_analysis as _gna
```

- [ ] **Step 9: Re-run all tests**

Run: `python -m pytest backend/tests/test_strategy_cache_persistence.py backend/tests/test_broker_live_boot_with_snapshot.py -v`

Expected: all pass.

- [ ] **Step 10: Wire the gap-day list into `_run_live_historic_lookback`**

Find `_run_live_historic_lookback` (per Task 8 step 4). Its current signature processes 120 days by default. Add an optional `restrict_to_dates: Optional[list]=None` parameter. When `restrict_to_dates` is provided, the lookback iterates only those dates.

```python
def _run_live_historic_lookback(
    *,
    # existing params ...
    restrict_to_dates: Optional[List[str]] = None,
):
    # ... existing logic that computes the candidate date range ...
    if restrict_to_dates is not None:
        # narrow the candidate list to only these dates
        candidates = [d for d in candidates if d in set(restrict_to_dates)]
    # ... rest of existing logic ...
```

Then in the call site (per Task 8 step 4), pass `restrict_to_dates=_gap_dates if _gap_dates else None`. If `_gap_dates` is `[]` (empty list, snapshot covers today), skip the lookback entirely with a log line `[snapshot] gap_dates empty; skipping lookback`.

- [ ] **Step 11: Add a 3 BLOCKER boot-time logs**

In the same live-boot section, after the snapshot load and lookback decisions, add:

```python
# BLOCKER #2 — F1b ramp bypass with 0 warm positions
_warm_pos_count = len(getattr(live_adapter, "_positions", {}) or {})
print(f"[live_boot] warm_positions={_warm_pos_count}, F1b_bypass={'enabled' if _warm_pos_count > 0 else 'disabled'}, ramp_starting_bar_index={_strategy_cache.get(strategy_name, {}).get('_deployment_bar_index', 0)}")

# BLOCKER #3 — _nexus_full_cycle_completed_date communication
_fcd = _strategy_cache.get(strategy_name, {}).get("_nexus_full_cycle_completed_date", "")
print(f"[live_boot] _nexus_full_cycle_completed_date={_fcd or '<unset>'}; next FULL cycle expected ~06:30 AM PT")

# BLOCKER #1 — settlement check (soft warning)
try:
    _cash_avail = float(getattr(live_adapter, "cash_available", 0.0) or 0.0)
    _cash_total = float(getattr(live_adapter, "cash_total", _cash_avail) or _cash_avail)
    if _cash_total > 0 and _cash_avail < _cash_total * 0.95:
        print(f"[live_boot] WARNING: unsettled funds detected (available={_cash_avail:.2f}, total={_cash_total:.2f}); T+1/T+2 may block early buys")
except Exception:
    pass
```

- [ ] **Step 12: Syntax check broker.py**

Run: `python -c "import backend.broker"`

Expected: no errors.

- [ ] **Step 13: Commit**

```bash
git add backend/broker.py backend/strategy_cache_persistence.py backend/tests/test_strategy_cache_persistence.py backend/tests/test_broker_live_boot_with_snapshot.py
git commit -m "feat(broker): live boot snapshot load + gap-day lookback + BLOCKER boot logs"
```

---

## Task 11: Update live runtime save call site to include `origin` + `config_hash` + `end_date`

**Files:**
- Modify: `backend/broker.py` (one or two call sites for `save_strategy_cache_to_db`)
- Test: `backend/tests/test_broker_live_boot_with_snapshot.py`

- [ ] **Step 1: Add failing test that asserts current saves pass the new kwargs**

Append to `backend/tests/test_broker_live_boot_with_snapshot.py`:

```python
def test_live_runtime_save_passes_new_kwargs(monkeypatch):
    """The broker's live runtime save path must pass config_hash, module_hash,
    end_date so the saved row uses the new PK form."""
    captured = {}

    def _fake_save(conn, r, instance_id, strategy_name, cache, **kwargs):
        captured.update(kwargs)
        captured["positional"] = (instance_id, strategy_name)
        return True

    monkeypatch.setattr(scp, "save_strategy_cache_to_db", _fake_save)

    from backend import broker
    broker._invoke_save_strategy_cache(
        conn=object(),
        r=object(),
        instance_id="main",
        strategy_name="graph_nexus_analysis",
        cache={"_momentum_watchlist": ["AAPL"]},
        config_dict={
            "strategy_name": "graph_nexus_analysis",
            "prompt_versions": {"sentiment": "v1"},
            "llm_stages": {},
            "history_scope_id_inputs": {},
            "lookback_learning_days": 120,
        },
    )
    assert captured["positional"] == ("main", "graph_nexus_analysis")
    assert "config_hash" in captured
    assert "module_hash" in captured
    assert "end_date" in captured
    assert len(captured["config_hash"]) == 16
    assert captured["end_date"] == _today_iso()
```

- [ ] **Step 2: Run to confirm it fails**

Run: `python -m pytest backend/tests/test_broker_live_boot_with_snapshot.py::test_live_runtime_save_passes_new_kwargs -v`

Expected: FAIL — `_invoke_save_strategy_cache` not defined.

- [ ] **Step 3: Add the wrapper in broker.py**

Near `_invoke_persist_backtest_snapshot` and `_invoke_load_snapshot_with_gap`, add:

```python
def _invoke_save_strategy_cache(
    conn,
    r,
    *,
    instance_id: str,
    strategy_name: str,
    cache: dict,
    config_dict: dict,
) -> bool:
    """Wrapper around save_strategy_cache_to_db that computes config_hash and
    module_hash from the current strategy state and passes end_date=today.

    Used by the live runtime periodic save path. Errors are swallowed.
    """
    try:
        from backend import strategy_cache_persistence as _scp
        from backend.strategies import graph_nexus_analysis as _gna
        config_hash = _scp._compute_config_hash(config_dict)
        module_path = getattr(_gna, "__file__", "")
        module_hash = _scp._compute_module_hash(module_path) if module_path else "missing"
        import datetime as _dt
        return _scp.save_strategy_cache_to_db(
            conn, r, instance_id, strategy_name, cache,
            config_hash=config_hash,
            module_hash=module_hash,
            end_date=_dt.date.today().isoformat(),
        )
    except Exception as e:
        print(f"[snapshot] live save raised (suppressed): {e}")
        return False
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest backend/tests/test_broker_live_boot_with_snapshot.py -v`

Expected: all pass.

- [ ] **Step 5: Replace the existing live-mode `save_strategy_cache_to_db` call(s)**

Find each call site from Task 8 step 2. For each one that's in the LIVE runtime save path (not the backtest), replace the call with:

```python
_invoke_save_strategy_cache(
    conn=conn,
    r=r,
    instance_id=instance_id,
    strategy_name=strategy_name,
    cache=_strategy_cache.get(strategy_name, {}),
    config_dict={
        "strategy_name": strategy_name,
        "prompt_versions": _collect_prompt_versions(strategy_spec.get("config", {})),
        "llm_stages": _collect_llm_stages(strategy_spec.get("config", {})),
        "history_scope_id_inputs": _collect_history_scope_inputs(strategy_spec.get("config", {})),
        "lookback_learning_days": int(strategy_spec.get("config", {}).get("lookback_learning_days", 120)),
    },
)
```

Reuse the `_collect_*` helpers from Task 9.

- [ ] **Step 6: Syntax check**

Run: `python -c "import backend.broker"`

Expected: no errors.

- [ ] **Step 7: Commit**

```bash
git add backend/broker.py backend/tests/test_broker_live_boot_with_snapshot.py
git commit -m "feat(broker): live runtime save now writes new-schema row with config_hash"
```

---

## Task 12: Extend `clear_main_instance_lookback_state.py` to cover all 14 tables

**Files:**
- Modify: `scripts/clear_main_instance_lookback_state.py`
- Test: `backend/tests/test_clear_main_instance_lookback_state.py` (CREATE)

- [ ] **Step 1: Add a `--instance` CLI flag and the test for it**

Create `backend/tests/test_clear_main_instance_lookback_state.py`:

```python
"""Tests for scripts/clear_main_instance_lookback_state.py extensions (Phase 1)."""

from __future__ import annotations

import sys
import os
from pathlib import Path

# Add scripts/ to path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import clear_main_instance_lookback_state as cleaner


def test_targets_list_covers_all_14_phase1_tables():
    """Phase 1 design requires all 14 per-instance tables be cleared."""
    table_names = {t[0] for t in cleaner.TARGETS}
    expected = {
        # Original 4
        "GraphNexusTradeContexts", "GraphNexusOutcomes",
        "NexusRuntimeState", "LiveState",
        # Phase 1 additions
        "NexusStrategyCache", "LiveOrderWAL",
        "GraphNexusDiscoveredStocks", "GraphNexusMarketTrends",
        "GraphNexusRotationCooldown", "GraphNexusTradeOutcomes",
        "GraphNexusLearningCache",
        "GraphNexusDiscoverySnapshots", "GraphNexusOutcomeSeries",
        "GraphNexusAnalystPanel",
    }
    missing = expected - table_names
    assert not missing, f"missing tables in TARGETS: {missing}"


def test_nexus_strategy_cache_target_filters_origin_live_only():
    """The cleanup must NOT delete backtest-origin snapshots."""
    nsc = [t for t in cleaner.TARGETS if t[0] == "NexusStrategyCache"]
    assert len(nsc) == 1
    criteria = nsc[0][1]
    # Expect a filter clause that references `origin` field
    has_origin_filter = any(
        ("origin" in str(c)) or (len(c) >= 3 and c[0] == "origin")
        for c in criteria
    )
    assert has_origin_filter, "NexusStrategyCache cleanup must filter on origin"


def test_instance_id_default_is_main():
    assert cleaner.INSTANCE_ID == "main"
```

- [ ] **Step 2: Run tests to verify the first fails**

Run: `python -m pytest backend/tests/test_clear_main_instance_lookback_state.py -v`

Expected: `test_targets_list_covers_all_14_phase1_tables` FAILS (current TARGETS has only 4).

- [ ] **Step 3: Extend TARGETS in `scripts/clear_main_instance_lookback_state.py`**

Replace the `TARGETS = [...]` block with:

```python
TARGETS = [
    # ----- Original 4 (already cleared by this script) -----
    ("GraphNexusTradeContexts", [
        ("instance_id", f"{INSTANCE_ID}|", "prefix"),
        ("base_instance_id", INSTANCE_ID, "exact"),
    ]),
    ("GraphNexusOutcomes", [
        ("instance_id", f"{INSTANCE_ID}|", "prefix"),
        ("base_instance_id", INSTANCE_ID, "exact"),
    ]),
    ("NexusRuntimeState", [
        ("id", f"{INSTANCE_ID}:", "prefix"),
    ]),
    ("LiveState", [
        ("id", INSTANCE_ID, "exact"),
    ]),
    # ----- Phase 1 additions (10 more tables) -----
    # NexusStrategyCache: clear LIVE-origin rows only; preserve BACKTEST snapshots
    # so the live boot can hydrate from them. The id includes "|live|" for the
    # new schema; legacy 2-segment ids (no origin field) are also live-origin.
    ("NexusStrategyCache", [
        ("origin", "live", "exact"),
    ]),
    ("LiveOrderWAL", [
        ("instance_id", INSTANCE_ID, "exact"),
    ]),
    ("GraphNexusDiscoveredStocks", [
        ("instance_id", INSTANCE_ID, "exact"),
    ]),
    ("GraphNexusMarketTrends", [
        ("instance_id", INSTANCE_ID, "exact"),
    ]),
    ("GraphNexusRotationCooldown", [
        ("instance_id", INSTANCE_ID, "exact"),
    ]),
    ("GraphNexusTradeOutcomes", [
        ("instance_id", INSTANCE_ID, "exact"),
    ]),
    ("GraphNexusLearningCache", [
        ("instance_id", INSTANCE_ID, "exact"),
    ]),
    ("GraphNexusDiscoverySnapshots", [
        ("instance_id", INSTANCE_ID, "exact"),
    ]),
    ("GraphNexusOutcomeSeries", [
        ("instance_id", INSTANCE_ID, "exact"),
    ]),
    ("GraphNexusAnalystPanel", [
        ("instance_id", INSTANCE_ID, "exact"),
    ]),
]
```

Also extend the `INSTANCE_ID` constant to be CLI-overridable:

```python
import argparse

# at top of file, after `import sys`
def _parse_args():
    p = argparse.ArgumentParser(description="Clear per-instance lookback state.")
    p.add_argument("--apply", action="store_true", help="actually delete (default: dry-run)")
    p.add_argument("--instance", default="main", help="instance_id to clear (default: main)")
    return p.parse_args()
```

Replace the `__main__` block:

```python
if __name__ == "__main__":
    args = _parse_args()
    # Rebuild TARGETS for the chosen instance
    INSTANCE_ID = args.instance
    # ... rebuild TARGETS using the new INSTANCE_ID by re-importing or refactoring ...
    raise SystemExit(main(args.apply))
```

Refactor: move `TARGETS` construction inside a `_build_targets(instance_id: str)` function, called from `main()`. Update `main()` signature to take `instance_id`. Update tests to call `_build_targets("main")` for the assertion.

The updated test file:

```python
def test_build_targets_covers_all_14_phase1_tables():
    targets = cleaner._build_targets("main")
    table_names = {t[0] for t in targets}
    expected = {
        "GraphNexusTradeContexts", "GraphNexusOutcomes",
        "NexusRuntimeState", "LiveState",
        "NexusStrategyCache", "LiveOrderWAL",
        "GraphNexusDiscoveredStocks", "GraphNexusMarketTrends",
        "GraphNexusRotationCooldown", "GraphNexusTradeOutcomes",
        "GraphNexusLearningCache",
        "GraphNexusDiscoverySnapshots", "GraphNexusOutcomeSeries",
        "GraphNexusAnalystPanel",
    }
    missing = expected - table_names
    assert not missing, f"missing: {missing}"
```

(Replace the previous `test_targets_list_covers_all_14_phase1_tables` with this one.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest backend/tests/test_clear_main_instance_lookback_state.py -v`

Expected: 3 passed.

- [ ] **Step 5: Dry-run the script to confirm it still works**

Run: `python scripts/clear_main_instance_lookback_state.py --instance main`

Expected: prints `WILL DELETE N rows from <table>` lines for each of the 14 tables (or `SKIP <table>: table does not exist`). Exit code 0.

- [ ] **Step 6: Commit**

```bash
git add scripts/clear_main_instance_lookback_state.py backend/tests/test_clear_main_instance_lookback_state.py
git commit -m "feat(scripts): extend clear_main_instance_lookback_state to all 14 per-instance tables + --instance flag"
```

---

## Task 13: Add `scripts/validate_live_launch_readiness.py`

**Files:**
- Create: `scripts/validate_live_launch_readiness.py`

This script is a one-shot diagnostic — exit code 0 = green, 1 = yellow (warnings), 2 = red (don't launch).

- [ ] **Step 1: Write the script**

Create `scripts/validate_live_launch_readiness.py`:

```python
"""Pre-launch validation for live mode.

Exit codes:
  0 — GREEN: ready to launch
  1 — YELLOW: warnings (operator should read, may proceed at their discretion)
  2 — RED: blocking issues (do not launch)

Usage:
  python scripts/validate_live_launch_readiness.py [--instance main]
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import sys


DB_NAME = "IntelliStock"


def _connect_rethinkdb():
    try:
        from rethinkdb import RethinkDB  # type: ignore
    except ImportError:
        return None, None
    r = RethinkDB()
    host = os.environ.get("RETHINKDB_HOST", "localhost")
    port = int(os.environ.get("RETHINKDB_PORT", "28015"))
    try:
        conn = r.connect(host=host, port=port)
        return r, conn
    except Exception:
        return r, None


def _check_snapshot_present(r, conn, instance_id: str) -> tuple:
    """(level, message) where level is 'green'|'yellow'|'red'."""
    try:
        rows = list(
            r.db(DB_NAME).table("NexusStrategyCache")
             .filter((r.row["instance_id"] == instance_id) & (r.row.get_field("origin").default("") == "backtest"))
             .pluck("id", "end_date", "config_hash", "size_bytes")
             .run(conn)
        )
    except Exception as e:
        return "red", f"snapshot query failed: {e}"
    if not rows:
        return "red", f"no backtest-origin snapshot found for instance={instance_id}"
    latest = max(rows, key=lambda x: x.get("end_date") or "")
    end_date_str = latest.get("end_date", "")
    try:
        end_dt = dt.date.fromisoformat(end_date_str)
        age = (dt.date.today() - end_dt).days
    except Exception:
        return "red", f"snapshot has invalid end_date: {end_date_str!r}"
    if age > 7:
        return "yellow", f"latest snapshot is {age} days old (end_date={end_date_str}); consider rerunning backtest"
    return "green", f"snapshot found: id={latest['id']} end_date={end_date_str} bytes={latest.get('size_bytes', 0)}"


def _check_no_legacy_live_strategy_cache(r, conn, instance_id: str) -> tuple:
    try:
        count = int(
            r.db(DB_NAME).table("NexusStrategyCache")
             .filter((r.row["instance_id"] == instance_id) & (r.row.get_field("origin").default("") == "live"))
             .count().run(conn) or 0
        )
    except Exception as e:
        return "yellow", f"cannot check live cache rows: {e}"
    if count > 0:
        return "yellow", f"{count} live-origin NexusStrategyCache rows still present for instance={instance_id}; run cleanup script"
    return "green", "no leftover live-origin NexusStrategyCache rows"


def _check_wal_open_count(r, conn, instance_id: str) -> tuple:
    try:
        if "LiveOrderWAL" not in list(r.db(DB_NAME).table_list().run(conn)):
            return "green", "LiveOrderWAL table does not exist; nothing to check"
        non_terminal = ("intent", "open", "pending", "partial")
        count = int(
            r.db(DB_NAME).table("LiveOrderWAL")
             .filter(r.row["instance_id"] == instance_id)
             .filter(lambda doc: r.expr(list(non_terminal)).contains(doc["state"].default("")))
             .count().run(conn) or 0
        )
    except Exception as e:
        return "yellow", f"WAL check raised: {e}"
    if count > 0:
        return "yellow", f"{count} non-terminal WAL entries for instance={instance_id}; reconciliation will run on boot"
    return "green", "WAL has no non-terminal entries"


def _check_per_instance_residue(r, conn, instance_id: str) -> tuple:
    """Sum row counts across all per-instance tables that should be empty after cleanup."""
    leftover_tables = (
        "GraphNexusDiscoveredStocks",
        "GraphNexusMarketTrends",
        "GraphNexusRotationCooldown",
        "GraphNexusTradeOutcomes",
        "GraphNexusLearningCache",
        "GraphNexusDiscoverySnapshots",
        "GraphNexusOutcomeSeries",
        "GraphNexusAnalystPanel",
    )
    try:
        existing = set(r.db(DB_NAME).table_list().run(conn))
    except Exception as e:
        return "yellow", f"table_list failed: {e}"
    by_table = {}
    for tbl in leftover_tables:
        if tbl not in existing:
            continue
        try:
            c = int(
                r.db(DB_NAME).table(tbl)
                 .filter(r.row["instance_id"] == instance_id)
                 .count().run(conn) or 0
            )
            if c > 0:
                by_table[tbl] = c
        except Exception:
            continue
    if by_table:
        msg = "; ".join(f"{k}={v}" for k, v in sorted(by_table.items()))
        return "yellow", f"per-instance residue: {msg} — run cleanup script with --apply"
    return "green", "no per-instance residue in auxiliary tables"


def main(instance_id: str) -> int:
    print(f"=== Live launch readiness check: instance={instance_id} ===")
    r, conn = _connect_rethinkdb()
    if r is None:
        print("RED: rethinkdb driver not installed.")
        return 2
    if conn is None:
        print("RED: cannot connect to RethinkDB.")
        return 2

    checks = [
        ("snapshot present", _check_snapshot_present),
        ("no legacy live cache", _check_no_legacy_live_strategy_cache),
        ("WAL open count", _check_wal_open_count),
        ("per-instance residue", _check_per_instance_residue),
    ]
    results = []
    try:
        for name, fn in checks:
            level, msg = fn(r, conn, instance_id)
            tag = {"green": "[GREEN]", "yellow": "[YELLOW]", "red": "[RED]"}.get(level, "[?]")
            print(f"{tag} {name}: {msg}")
            results.append(level)
    finally:
        try:
            conn.close()
        except Exception:
            pass

    if "red" in results:
        print("\nVERDICT: RED — do not launch.")
        return 2
    if "yellow" in results:
        print("\nVERDICT: YELLOW — proceed at operator's discretion.")
        return 1
    print("\nVERDICT: GREEN — ready to launch.")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--instance", default="main")
    args = p.parse_args()
    raise SystemExit(main(args.instance))
```

- [ ] **Step 2: Quick smoke test**

Run: `python scripts/validate_live_launch_readiness.py --instance nonexistent_instance`

Expected: output mentions RED for snapshot present (no rows). Exit code 2. No exception.

- [ ] **Step 3: Commit**

```bash
git add scripts/validate_live_launch_readiness.py
git commit -m "feat(scripts): add validate_live_launch_readiness.py pre-flight check"
```

---

## Task 14: Create operator runbook

**Files:**
- Create: `docs/runbooks/live-launch-checklist.md`

- [ ] **Step 1: Write the runbook**

Create the file with this content:

```markdown
# Live Launch Checklist

**Audience:** Operator preparing to start a fresh nexus strategy in live mode on `instance_id="main"` (or any other instance).

**Spec:** `docs/superpowers/specs/2026-05-21-live-mode-safe-startup-design.md`

## T-24h (evening before launch)

- [ ] **Lock strategy config.** Decide model, prompt versions, history_scope_id ingredients. Do not change between now and launch.
- [ ] **Run a backtest with `base_instance_id="main"` and `end_date=today`.** Use the configured lookback length (default 120 trading days).
- [ ] **Verify backtest log:** look for the line `[snapshot] persisted: id=main|graph_nexus_analysis|<hash>|backtest|<end_date> bytes=<N>`. If absent, the snapshot wasn't written; investigate before proceeding.

## T-1h to T-30min (morning of launch)

- [ ] **Liquidate all Robinhood positions.** Sell everything currently held in the broker account. Wait for fills to confirm.
- [ ] **Wait for settlement.** Acceptable to launch with some unsettled funds, but log will warn if cash_available < cash_total * 0.95.
- [ ] **Run cleanup script (dry-run first):**
  ```bash
  python scripts/clear_main_instance_lookback_state.py --instance main
  ```
  Read the row counts to be deleted. Confirm they look reasonable.
- [ ] **Run cleanup script (apply):**
  ```bash
  python scripts/clear_main_instance_lookback_state.py --instance main --apply
  ```
  Expected: summary listing cleared row counts across all 14 per-instance tables. Backtest-origin `NexusStrategyCache` rows should be preserved.
- [ ] **Run validation:**
  ```bash
  python scripts/validate_live_launch_readiness.py --instance main
  ```
  Expected: VERDICT: GREEN. If YELLOW, read the warning and decide. If RED, do not proceed.

## T-15min

- [ ] **Start the live instance.** Use the UI button or API call.
- [ ] **Tail the live log.** Look for this boot sequence:
  ```
  [live_boot] strategy_name=graph_nexus_analysis
  [snapshot] decision: reason=ok gap_days=1
  [snapshot] hydrated <N> keys
  [lookback] running for 1 gap day(s): [<today>]
  [live_boot] warm_positions=0, F1b_bypass=disabled, ramp_starting_bar_index=0
  [live_boot] _nexus_full_cycle_completed_date=<yesterday>; next FULL cycle expected ~06:30 AM PT
  ```
- [ ] **Confirm Discord post.** A `🟢 Live launch ready` embed should appear in the agent channel.

## T+0 (market open / first FULL cycle ~06:30 AM PT)

- [ ] **Watch first FULL cycle.** Confirm first buy/sell decisions appear in the log.
- [ ] **Sanity-check AI Credits card.** Open `/backtests/<recent-backtest-id>`; the AI Credits card should still render (Session #8 feature unbroken).

## Rollback (if anything looks wrong during the first hour)

- Set the env flag on the Instances row: `NEXUS_LIVE_SNAPSHOT_LOAD=off`.
- Restart the broker. A fresh full 120-day lookback will run from scratch (no snapshot used).
- Or stop the instance entirely and investigate before resuming trading.

## What this checklist protects against

- Old (deprecated) nexus version's persisted state (cooldowns, blacklists, peak HWM, discovered-stock "sold" flags) leaking into the new strategy's decision-making.
- Stale Robinhood positions distorting the new strategy's deployment ramp.
- Stale `LiveOrderWAL` entries causing spurious order replays on boot.

## What this checklist does NOT protect against

- Bugs in the new strategy itself (your backtest report is the judge).
- Network/broker outages.
- Sudden config drift made after T-24h (re-run the backtest if you change anything).

## Phase 2 notes

When the versioned per-instance schema (Phase 2 per spec §11) ships, this cleanup script becomes unnecessary. Multiple strategy versions can run side-by-side on the same instance without contaminating each other.
```

- [ ] **Step 2: Verify the file renders as markdown**

Open in any markdown previewer or `cat docs/runbooks/live-launch-checklist.md` to eyeball.

- [ ] **Step 3: Commit**

```bash
git add docs/runbooks/live-launch-checklist.md
git commit -m "docs(runbook): live launch checklist (Phase 1)"
```

---

## Task 15: Run full test suite, confirm no regressions

**Files:**
- No code changes.

- [ ] **Step 1: Run the new tests**

Run: `python -m pytest backend/tests/test_strategy_cache_persistence.py backend/tests/test_broker_live_boot_with_snapshot.py backend/tests/test_clear_main_instance_lookback_state.py -v`

Expected: ~40 passed.

- [ ] **Step 2: Run the entire backend test suite**

Run: `python -m pytest backend/tests/ -q`

Expected: 158 (pre-Phase-1) + ~40 (Phase 1) = ~200 passed. No failures.

- [ ] **Step 3: Front-end build sanity (verify session #8 work still builds)**

Run: `cd frontend && npx vite build`

Expected: build succeeds; `TokenUsageView.vue` ~21 kB; `BacktestDetailView.vue` ~52 kB. (Phase 1 doesn't touch frontend; this is regression confirmation only.)

- [ ] **Step 4: GitNexus detect_changes scope check**

Run: `gitnexus_detect_changes()` (via MCP tool, as per project CLAUDE.md).

Expected: changes confined to:
- `backend/strategy_cache_persistence.py`
- `backend/broker.py`
- `scripts/clear_main_instance_lookback_state.py`
- `scripts/validate_live_launch_readiness.py`
- `backend/tests/test_strategy_cache_persistence.py`
- `backend/tests/test_broker_live_boot_with_snapshot.py`
- `backend/tests/test_clear_main_instance_lookback_state.py`
- `docs/runbooks/live-launch-checklist.md`

Plus the previously committed spec at `docs/superpowers/specs/2026-05-21-live-mode-safe-startup-design.md` and this plan.

If anything else shows up, investigate before considering Phase 1 complete.

- [ ] **Step 5: Reindex GitNexus**

Run: `npx gitnexus analyze --embeddings`

Expected: completes successfully.

- [ ] **Step 6: Final commit (if any small fixups needed)**

If steps 1-5 surface anything, fix and:

```bash
git add <files>
git commit -m "fix(phase1): <what>"
```

---

## Task 16: Hand-off to operator + manual smoke walk-through

**Files:**
- No code.

- [ ] **Step 1: Operator runs backtest (per runbook T-24h)**

Operator's job. Implementing engineer is on standby.

- [ ] **Step 2: Operator runs cleanup + validation**

Operator's job. Engineer reviews log output if asked.

- [ ] **Step 3: Operator starts live instance, tails logs**

Engineer confirms the boot-sequence log lines match the runbook's expected pattern. If they don't, debug; common causes:
- Snapshot row missing (backtest didn't write it) — check Task 9's wiring at the BacktestResults completion site
- `gap_dates` is None when it should be `[]` — check Task 10's wrapper return shape
- Module hash mismatch when nothing was edited — check `graph_nexus_analysis.py`'s file mtime / contents

- [ ] **Step 4: After first FULL cycle, debrief**

If the strategy made any decisions on day 1 that look anomalous, gather logs and have the operator open a new conversation to debug — not in this Phase 1 plan's scope.

- [ ] **Step 5: Mark Phase 1 complete**

When the operator confirms a successful first full trading day, Phase 1 ships. Phase 2 (versioned per-instance schema) is brainstormed in a future session.

---

## Out-of-Scope Reminders

- **Phase 2** (versioned per-instance schema across ~14 tables): separate brainstorm/spec/plan cycle.
- **Strategy logic changes**: this plan touches scaffolding only; no `graph_nexus_analysis.py` edits.
- **Frontend changes**: none.
- **Broker adapter changes**: none.
- **UI for the snapshot system**: deferred — operators use the CLI tools.
- **Telemetry counters table** (spec §8.3): deferred. The boot log lines from Task 10 Step 11 already emit `[snapshot] decision: reason=<...> gap_days=<N>`, `[live_boot] warm_positions=<N>`, etc. — log-scraping can aggregate these into time-series. A dedicated counter table can be added in a small follow-up once we see the snapshot system in production.
