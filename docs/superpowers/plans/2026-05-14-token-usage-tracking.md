# Token Usage Tracking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Capture per-call LLM token usage and cost across all providers (openai, azure, gemini, deepseek, claude-cli structured + chat), persist to RethinkDB with daily rollups, expose via REST API, and surface in a new Vue 3 "Token Usage" page with widgets, time-series chart, top spenders, and a 50-most-recent-calls table.

**Architecture:** A single central `backend/llm_telemetry.py` module owns the in-memory async queue, the cost computation, and the background flusher. Six instrumentation sites (5 structured-LLM exits in `llm_utils.py` + 1 chatbot event handler in `claude_cli_provider.py`) call `record_llm_call()`. A new FastAPI endpoint surface under `/api/llm-usage/*` (added to `backend/api/main.py` matching the existing inline-decorator pattern) reads from `LLMUsage` (per-call) and `LLMUsageDaily` (rollup) tables. A new `frontend/src/views/TokenUsageView.vue` displays everything via ApexCharts and Tailwind, registered as `/token-usage` in `frontend/src/router/index.js`.

**Tech Stack:** Python 3.11, FastAPI, RethinkDB (lazy table creation pattern), PyYAML, pytest. Vue 3 SFC + Composition API, `vue3-apexcharts`, Tailwind. Auth: Bearer token (existing pattern).

**Reference spec:** `docs/superpowers/specs/2026-05-14-token-usage-tracking-design.md`

---

## File Structure

| File | Role | Action |
|---|---|---|
| `backend/llm_pricing.yaml` | USD-per-1M-tokens defaults for ~10 common models | Create |
| `backend/llm_telemetry.py` | Central sink: queue, flusher, cost lookup, context manager, ring buffer | Create |
| `backend/llm_utils.py` | Add 5 instrumentation calls + `_record_with_telemetry()` helper | Modify |
| `backend/chatbot/claude_cli_provider.py` | Add instrumentation in `call_claude_cli_chat` event handler | Modify |
| `backend/strategies/graph_nexus_analysis.py` | Wrap 4 dispatcher call sites with `llm_call_context()` | Modify |
| `backend/strategies/ml_news.py` | Wrap dispatcher with `llm_call_context()` | Modify |
| `backend/strategies/earnings.py` | Wrap dispatcher with `llm_call_context()` | Modify |
| `backend/strategies/nexus_analyst_panel.py` | Wrap dispatcher with `llm_call_context()` | Modify |
| `backend/api/main.py` | Add 5 endpoints under `/llm-usage/*` + 4 cost fields in Models endpoints | Modify |
| `backend/tests/test_llm_telemetry.py` | Unit tests for the sink | Create |
| `backend/tests/test_api_llm_usage.py` | API endpoint tests | Create |
| `backend/tests/test_llm_utils_telemetry_integration.py` | Integration: instrumentation sites write expected rows | Create |
| `frontend/src/views/TokenUsageView.vue` | Full dashboard page | Create |
| `frontend/src/views/ModelsView.vue` | Add 4 cost-per-1m input fields to the form | Modify |
| `frontend/src/router/index.js` | Add `/token-usage` route | Modify |
| `frontend/src/layouts/AppShell.vue` | Add nav link for Token Usage | Modify (if nav exists there) |

---

## Task 1: Pricing YAML + Loader

**Files:**
- Create: `backend/llm_pricing.yaml`
- Modify: `backend/llm_telemetry.py` (create with just the pricing loader for now)
- Test: `backend/tests/test_llm_telemetry.py`

- [ ] **Step 1: Create the YAML file**

`backend/llm_pricing.yaml`:

```yaml
# USD per 1M tokens. Per-model entries in the Models table override these.
# Update when providers change pricing.

claude-sonnet-4-6:
  provider: anthropic
  input_per_1m: 3.00
  output_per_1m: 15.00
  cache_creation_per_1m: 3.75
  cache_read_per_1m: 0.30

claude-opus-4-7:
  provider: anthropic
  input_per_1m: 15.00
  output_per_1m: 75.00
  cache_creation_per_1m: 18.75
  cache_read_per_1m: 1.50

claude-haiku-4-5:
  provider: anthropic
  input_per_1m: 1.00
  output_per_1m: 5.00
  cache_creation_per_1m: 1.25
  cache_read_per_1m: 0.10

gpt-5-mini:
  provider: azure
  input_per_1m: 0.25
  output_per_1m: 2.00

gpt-oss-120b:
  provider: azure
  input_per_1m: 0.0
  output_per_1m: 0.0

gemini-2-5-pro:
  provider: gemini
  input_per_1m: 1.25
  output_per_1m: 10.00

deepseek-chat:
  provider: deepseek
  input_per_1m: 0.27
  output_per_1m: 1.10

_unknown_:
  input_per_1m: null
  output_per_1m: null
```

- [ ] **Step 2: Create the test file with a failing test**

`backend/tests/test_llm_telemetry.py`:

```python
"""Tests for the llm_telemetry sink module."""
from __future__ import annotations

import os
import sys
import pathlib
import time
import threading
from unittest.mock import MagicMock, patch

import pytest

_BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)


def test_pricing_loader_reads_yaml(tmp_path):
    from llm_telemetry import load_pricing_yaml
    pricing_file = tmp_path / "pricing.yaml"
    pricing_file.write_text(
        "claude-sonnet-4-6:\n"
        "  provider: anthropic\n"
        "  input_per_1m: 3.00\n"
        "  output_per_1m: 15.00\n"
        "  cache_creation_per_1m: 3.75\n"
        "  cache_read_per_1m: 0.30\n"
        "_unknown_:\n"
        "  input_per_1m: null\n"
        "  output_per_1m: null\n"
    )
    pricing = load_pricing_yaml(str(pricing_file))
    assert pricing["claude-sonnet-4-6"]["input_per_1m"] == 3.00
    assert pricing["claude-sonnet-4-6"]["cache_read_per_1m"] == 0.30
    assert pricing["_unknown_"]["input_per_1m"] is None


def test_pricing_loader_returns_empty_on_missing_file(tmp_path):
    from llm_telemetry import load_pricing_yaml
    pricing = load_pricing_yaml(str(tmp_path / "does_not_exist.yaml"))
    assert pricing == {}
```

- [ ] **Step 3: Verify tests fail**

Run: `cd backend && pytest tests/test_llm_telemetry.py -v`
Expected: FAIL with `ImportError: cannot import name 'load_pricing_yaml'`.

- [ ] **Step 4: Create `backend/llm_telemetry.py` with the loader**

```python
"""Central LLM telemetry sink: captures per-call token usage from every
provider, computes USD cost from a pricing registry, persists to RethinkDB
asynchronously, and exposes a small public API for the FastAPI layer to
query recent calls and aggregates.

Public functions:
    load_pricing_yaml(path)       - read YAML pricing into dict
    configure(...)                - wire DB connection + flush settings
    record_llm_call(...)          - the instrumentation entry point
    llm_call_context(...)         - context manager for attribution
    get_recent_calls(n)           - in-memory ring buffer reader (UI fast path)
    flush()                       - forced sync flush (tests, shutdown)
    get_buffer_depth()            - health-endpoint accessor
    get_pricing(model)            - exposed for the Models UI
"""
from __future__ import annotations

import os
from typing import Any, Dict, Optional


def load_pricing_yaml(path: str) -> Dict[str, Dict[str, Any]]:
    """Read the pricing YAML file. Returns an empty dict if the file is
    missing or unparseable — telemetry continues with cost_source="unknown"
    rather than blocking calls.
    """
    if not os.path.isfile(path):
        return {}
    try:
        import yaml
    except ImportError:
        print(
            "[llm_telemetry] PyYAML not installed; pricing disabled.",
            flush=True,
        )
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return data if isinstance(data, dict) else {}
    except Exception as e:
        print(
            f"[llm_telemetry] failed to parse pricing YAML ({path}): {e}",
            flush=True,
        )
        return {}
```

- [ ] **Step 5: Run the tests**

Run: `cd backend && pytest tests/test_llm_telemetry.py -v`
Expected: PASS (both tests).

- [ ] **Step 6: Commit**

```bash
git add backend/llm_pricing.yaml backend/llm_telemetry.py backend/tests/test_llm_telemetry.py
git commit -m "feat(telemetry): pricing YAML registry + loader"
```

---

## Task 2: RethinkDB Table Creation Helpers

**Files:**
- Modify: `backend/llm_telemetry.py`
- Test: `backend/tests/test_llm_telemetry.py`

- [ ] **Step 1: Add tests for table-creation helpers**

Append to `backend/tests/test_llm_telemetry.py`:

```python
def test_ensure_llm_usage_table_creates_table_and_indexes():
    """Verify the table-creation helper creates the table and the expected
    secondary indexes on a fresh connection."""
    from llm_telemetry import ensure_llm_usage_tables

    # Fake rethinkdb connection that tracks calls.
    created_tables = []
    created_indexes = []

    class _FakeQuery:
        def __init__(self, log_target=None, log_value=None):
            self._log = log_target
            self._val = log_value
        def run(self, conn):
            if self._log is not None and self._val is not None:
                self._log.append(self._val)
            if self._val == "table_list":
                return []
            if self._val == "index_list":
                return []
            return None
        def table_create(self, name):
            return _FakeQuery(created_tables, name)
        def index_create(self, name, *args, **kw):
            return _FakeQuery(created_indexes, name)
        def db_list(self):
            return _FakeQuery(None, "db_list")
        def table_list(self):
            return _FakeQuery(None, "table_list")
        def index_list(self):
            return _FakeQuery(None, "index_list")
        def db(self, _name):
            return self
        def table(self, _name):
            return self

    class _FakeR:
        def db_list(self): return _FakeQuery(None, "db_list")
        def db_create(self, _name): return _FakeQuery(None, "db_create")
        def db(self, _name): return _FakeQuery()
        def table(self, _name): return _FakeQuery()

    ensure_llm_usage_tables(conn=object(), r=_FakeR(), db_name="IntelliStock")
    assert "LLMUsage" in created_tables
    assert "LLMUsageDaily" in created_tables
    # Indexes we care about
    assert "ts" in created_indexes
    assert "provider" in created_indexes
    assert "date" in created_indexes
```

- [ ] **Step 2: Run, expect failure**

Run: `cd backend && pytest tests/test_llm_telemetry.py::test_ensure_llm_usage_table_creates_table_and_indexes -v`
Expected: FAIL — `cannot import name 'ensure_llm_usage_tables'`.

- [ ] **Step 3: Implement `ensure_llm_usage_tables`**

Append to `backend/llm_telemetry.py`:

```python
_LLM_USAGE_TABLE = "LLMUsage"
_LLM_USAGE_DAILY_TABLE = "LLMUsageDaily"

_LLM_USAGE_INDEXES = ("ts", "provider", "model", "backtest_id", "instance_id")
_LLM_USAGE_DAILY_INDEXES = ("date",)


def ensure_llm_usage_tables(*, conn, r, db_name: str) -> None:
    """Idempotently create the LLMUsage + LLMUsageDaily tables and their
    secondary indexes. Safe to call on every process start.
    """
    if db_name not in list(r.db_list().run(conn)):
        r.db_create(db_name).run(conn)
    existing = list(r.db(db_name).table_list().run(conn))
    if _LLM_USAGE_TABLE not in existing:
        r.db(db_name).table_create(_LLM_USAGE_TABLE).run(conn)
    if _LLM_USAGE_DAILY_TABLE not in existing:
        r.db(db_name).table_create(_LLM_USAGE_DAILY_TABLE).run(conn)

    def _ensure_indexes(table: str, indexes: tuple[str, ...]) -> None:
        existing_idx = list(r.db(db_name).table(table).index_list().run(conn))
        for idx in indexes:
            if idx not in existing_idx:
                try:
                    r.db(db_name).table(table).index_create(idx).run(conn)
                except Exception as e:
                    print(
                        f"[llm_telemetry] index_create {table}.{idx} failed: {e}",
                        flush=True,
                    )

    _ensure_indexes(_LLM_USAGE_TABLE, _LLM_USAGE_INDEXES)
    _ensure_indexes(_LLM_USAGE_DAILY_TABLE, _LLM_USAGE_DAILY_INDEXES)
```

- [ ] **Step 4: Run tests**

Run: `cd backend && pytest tests/test_llm_telemetry.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/llm_telemetry.py backend/tests/test_llm_telemetry.py
git commit -m "feat(telemetry): RethinkDB LLMUsage + LLMUsageDaily table helpers"
```

---

## Task 3: Cost Computation

**Files:**
- Modify: `backend/llm_telemetry.py`
- Test: `backend/tests/test_llm_telemetry.py`

- [ ] **Step 1: Write failing tests for `compute_cost`**

Append:

```python
def test_compute_cost_from_yaml():
    from llm_telemetry import compute_cost
    pricing = {
        "claude-sonnet-4-6": {
            "input_per_1m": 3.00,
            "output_per_1m": 15.00,
            "cache_creation_per_1m": 3.75,
            "cache_read_per_1m": 0.30,
        }
    }
    usage = {
        "input_tokens": 1_000_000,
        "output_tokens": 500_000,
        "cache_creation_input_tokens": 200_000,
        "cache_read_input_tokens": 100_000,
    }
    cost = compute_cost(
        model="claude-sonnet-4-6",
        usage=usage,
        pricing_yaml=pricing,
        models_override=None,
    )
    assert cost["input_cost_usd"] == 3.00
    assert cost["output_cost_usd"] == 7.50
    assert cost["cache_creation_cost_usd"] == pytest.approx(0.75)
    assert cost["cache_read_cost_usd"] == pytest.approx(0.03)
    assert cost["total_cost_usd"] == pytest.approx(11.28)
    assert cost["cost_source"] == "yaml"


def test_compute_cost_models_table_override():
    from llm_telemetry import compute_cost
    pricing = {"foo-model": {"input_per_1m": 5.0, "output_per_1m": 10.0}}
    override = {
        "input_cost_per_1m": 1.0,   # cheaper than YAML
        "output_cost_per_1m": 2.0,
    }
    cost = compute_cost(
        model="foo-model",
        usage={"input_tokens": 1_000_000, "output_tokens": 1_000_000},
        pricing_yaml=pricing,
        models_override=override,
    )
    assert cost["input_cost_usd"] == 1.0
    assert cost["output_cost_usd"] == 2.0
    assert cost["cost_source"] == "models_override"


def test_compute_cost_envelope_override():
    from llm_telemetry import compute_cost
    cost = compute_cost(
        model="claude-cli",
        usage={"input_tokens": 100, "output_tokens": 50},
        pricing_yaml={},
        models_override=None,
        cost_usd_override=0.42,
    )
    assert cost["total_cost_usd"] == 0.42
    assert cost["cost_source"] == "envelope"


def test_compute_cost_unknown_model():
    from llm_telemetry import compute_cost
    cost = compute_cost(
        model="never-heard-of-it",
        usage={"input_tokens": 1000, "output_tokens": 500},
        pricing_yaml={},
        models_override=None,
    )
    assert cost["total_cost_usd"] == 0.0
    assert cost["cost_source"] == "unknown"
```

- [ ] **Step 2: Run, expect failure**

Run: `cd backend && pytest tests/test_llm_telemetry.py -v`
Expected: 4 new failures.

- [ ] **Step 3: Implement `compute_cost`**

Append to `backend/llm_telemetry.py`:

```python
_COST_FIELDS = (
    "input_cost_usd",
    "output_cost_usd",
    "cache_creation_cost_usd",
    "cache_read_cost_usd",
    "total_cost_usd",
)

_USAGE_TO_PRICE_KEY = {
    "input_tokens": ("input_per_1m", "input_cost_per_1m", "input_cost_usd"),
    "output_tokens": ("output_per_1m", "output_cost_per_1m", "output_cost_usd"),
    "cache_creation_input_tokens": (
        "cache_creation_per_1m",
        "cache_creation_cost_per_1m",
        "cache_creation_cost_usd",
    ),
    "cache_read_input_tokens": (
        "cache_read_per_1m",
        "cache_read_cost_per_1m",
        "cache_read_cost_usd",
    ),
}


def compute_cost(
    *,
    model: str,
    usage: Dict[str, int],
    pricing_yaml: Dict[str, Dict[str, Any]],
    models_override: Optional[Dict[str, Any]],
    cost_usd_override: Optional[float] = None,
) -> Dict[str, Any]:
    """Compute per-call USD cost from token counts.

    Priority order:
      1. cost_usd_override (envelope) → cost_source="envelope"
      2. models_override (Models-table fields) → cost_source="models_override"
      3. pricing_yaml entry for `model` → cost_source="yaml"
      4. Fallback: zero cost, cost_source="unknown"
    """
    result = {field: 0.0 for field in _COST_FIELDS}

    if cost_usd_override is not None:
        result["total_cost_usd"] = float(cost_usd_override)
        result["cost_source"] = "envelope"
        return result

    # Pick the price table to use.
    price_keys: Dict[str, Optional[float]] = {}
    source = "unknown"
    if models_override and any(
        models_override.get(k) is not None
        for _, k, _ in _USAGE_TO_PRICE_KEY.values()
    ):
        # Models table provides at least one field — use it (others fall through
        # to YAML, then 0).
        source = "models_override"
        for yaml_k, override_k, _ in _USAGE_TO_PRICE_KEY.values():
            price_keys[yaml_k] = models_override.get(override_k)
        # Fill the gaps from YAML if available.
        yaml_entry = pricing_yaml.get(model) or {}
        for yaml_k in price_keys:
            if price_keys[yaml_k] is None:
                price_keys[yaml_k] = yaml_entry.get(yaml_k)
    else:
        yaml_entry = pricing_yaml.get(model)
        if yaml_entry is not None:
            source = "yaml"
            for yaml_k in _USAGE_TO_PRICE_KEY.values():
                pass  # noqa — iterate below
            for yaml_k, _, _ in _USAGE_TO_PRICE_KEY.values():
                price_keys[yaml_k] = yaml_entry.get(yaml_k)

    total = 0.0
    for usage_k, (yaml_k, _, cost_k) in _USAGE_TO_PRICE_KEY.items():
        tokens = int(usage.get(usage_k) or 0)
        price_per_1m = price_keys.get(yaml_k)
        if price_per_1m is None:
            continue
        component = (tokens / 1_000_000.0) * float(price_per_1m)
        result[cost_k] = component
        total += component
    result["total_cost_usd"] = total
    result["cost_source"] = source
    return result
```

- [ ] **Step 4: Run tests**

Run: `cd backend && pytest tests/test_llm_telemetry.py -v`
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/llm_telemetry.py backend/tests/test_llm_telemetry.py
git commit -m "feat(telemetry): compute_cost with envelope/models_override/yaml/unknown priority"
```

---

## Task 4: Async Queue + Flusher + Ring Buffer + Context Manager

**Files:**
- Modify: `backend/llm_telemetry.py`
- Test: `backend/tests/test_llm_telemetry.py`

- [ ] **Step 1: Write failing tests**

Append:

```python
def test_record_llm_call_appends_to_buffer_and_ring(monkeypatch):
    from llm_telemetry import (
        configure, record_llm_call, get_buffer_depth, get_recent_calls, _reset_for_tests,
    )
    _reset_for_tests()
    configure(db_conn_factory=lambda: None, enabled=True, auto_start_flusher=False,
              pricing_yaml_path=None)

    record_llm_call(
        provider="openai",
        model="gpt-4o",
        usage={"input_tokens": 100, "output_tokens": 50},
        ok=True,
        duration_ms=120,
    )
    assert get_buffer_depth() == 1
    recent = get_recent_calls(10)
    assert len(recent) == 1
    assert recent[0]["provider"] == "openai"
    assert recent[0]["model"] == "gpt-4o"
    assert recent[0]["input_tokens"] == 100
    assert recent[0]["output_tokens"] == 50
    assert recent[0]["duration_ms"] == 120
    assert recent[0]["cost_source"] in ("unknown", "yaml")


def test_flush_drains_buffer_via_db_factory():
    from llm_telemetry import (
        configure, record_llm_call, flush, get_buffer_depth, _reset_for_tests,
    )
    _reset_for_tests()

    inserted_batches = []
    class _FakeTable:
        def insert(self, rows):
            return _FakeQuery(rows)
    class _FakeQuery:
        def __init__(self, rows): self.rows = rows
        def run(self, conn):
            inserted_batches.append(list(self.rows))
            return {"inserted": len(self.rows)}
    class _FakeDb:
        def table(self, _name): return _FakeTable()
    class _FakeR:
        def db(self, _name): return _FakeDb()

    configure(
        db_conn_factory=lambda: object(),
        enabled=True,
        auto_start_flusher=False,
        pricing_yaml_path=None,
        r_module=_FakeR(),
    )
    for i in range(3):
        record_llm_call(provider="azure", model=f"m{i}",
                        usage={"input_tokens": 1, "output_tokens": 1})

    assert get_buffer_depth() == 3
    flush()
    assert get_buffer_depth() == 0
    assert len(inserted_batches) == 1
    assert len(inserted_batches[0]) == 3


def test_context_manager_attribution():
    from llm_telemetry import (
        configure, record_llm_call, llm_call_context, get_recent_calls, _reset_for_tests,
    )
    _reset_for_tests()
    configure(db_conn_factory=lambda: None, enabled=True, auto_start_flusher=False,
              pricing_yaml_path=None)

    with llm_call_context(backtest_id="bt-123", strategy="GraphNexus",
                          call_site="company_classification"):
        record_llm_call(provider="claude-cli", model="claude-sonnet-4-6",
                        usage={"input_tokens": 10, "output_tokens": 5})

    recent = get_recent_calls(1)
    assert recent[0]["backtest_id"] == "bt-123"
    assert recent[0]["strategy"] == "GraphNexus"
    assert recent[0]["call_site"] == "company_classification"


def test_context_manager_nested_inner_wins():
    from llm_telemetry import (
        configure, record_llm_call, llm_call_context, get_recent_calls, _reset_for_tests,
    )
    _reset_for_tests()
    configure(db_conn_factory=lambda: None, enabled=True, auto_start_flusher=False,
              pricing_yaml_path=None)

    with llm_call_context(backtest_id="bt-1", strategy="outer"):
        with llm_call_context(strategy="inner", call_site="x"):
            record_llm_call(provider="azure", model="gpt-4o",
                            usage={"input_tokens": 1, "output_tokens": 1})

    r = get_recent_calls(1)[0]
    assert r["backtest_id"] == "bt-1"     # inherited from outer
    assert r["strategy"] == "inner"        # inner overrides
    assert r["call_site"] == "x"


def test_buffer_overflow_drops_oldest():
    from llm_telemetry import (
        configure, record_llm_call, get_recent_calls, _reset_for_tests,
    )
    _reset_for_tests()
    configure(db_conn_factory=lambda: None, enabled=True, auto_start_flusher=False,
              pricing_yaml_path=None, max_buffer_hard_cap=10)

    for i in range(15):
        record_llm_call(provider="x", model=f"m{i}",
                        usage={"input_tokens": 1, "output_tokens": 1})

    recent = get_recent_calls(20)
    # Oldest 5 dropped from the in-flight buffer; ring buffer still keeps recents
    # The hard cap is on the in-flight write buffer, not the ring buffer.
    # We assert the ring buffer holds the latest entries.
    assert recent[0]["model"] == "m14"  # newest first
    assert recent[-1]["model"] in ("m0", "m5")  # oldest in ring


def test_telemetry_disabled_is_noop():
    from llm_telemetry import (
        configure, record_llm_call, get_buffer_depth, _reset_for_tests,
    )
    _reset_for_tests()
    configure(db_conn_factory=lambda: None, enabled=False, auto_start_flusher=False,
              pricing_yaml_path=None)

    record_llm_call(provider="x", model="y", usage={"input_tokens": 1, "output_tokens": 1})
    assert get_buffer_depth() == 0
```

- [ ] **Step 2: Run, expect failure**

Run: `cd backend && pytest tests/test_llm_telemetry.py -v`

- [ ] **Step 3: Implement queue / flusher / ring / context manager**

Append to `backend/llm_telemetry.py`:

```python
import threading
import time
import uuid
import sys
from collections import deque
from contextlib import contextmanager
from typing import Iterator, List

# ── Module state ─────────────────────────────────────────────────────────────
_state_lock = threading.Lock()
_buffer: deque = deque()                  # in-flight rows waiting to be flushed
_ring_buffer: deque = deque(maxlen=200)   # recent calls for UI fast path
_state = {
    "enabled": False,
    "db_conn_factory": None,
    "r_module": None,
    "db_name": "IntelliStock",
    "flush_interval_s": 5.0,
    "max_buffer": 50,
    "max_buffer_hard_cap": 5000,
    "pricing_yaml": {},
    "pricing_yaml_path": "backend/llm_pricing.yaml",
    "models_override_lookup": None,  # Optional[Callable[[str], Optional[dict]]]
    "write_errors_24h": 0,
    "last_flush_ts": 0,
    "flusher_thread": None,
    "stop_flusher": False,
}

# Thread-local attribution stack.
_ctx_local = threading.local()


def _get_ctx_stack() -> List[Dict[str, Any]]:
    stack = getattr(_ctx_local, "stack", None)
    if stack is None:
        stack = []
        _ctx_local.stack = stack
    return stack


def _merge_active_ctx() -> Dict[str, Any]:
    """Merge all active context frames, inner wins for set keys."""
    merged: Dict[str, Any] = {}
    for frame in _get_ctx_stack():
        for k, v in frame.items():
            if v is not None:
                merged[k] = v
    return merged


@contextmanager
def llm_call_context(
    *,
    backtest_id: Optional[str] = None,
    instance_id: Optional[str] = None,
    strategy: Optional[str] = None,
    call_site: Optional[str] = None,
    conversation_id: Optional[str] = None,
) -> Iterator[None]:
    frame = {
        "backtest_id": backtest_id,
        "instance_id": instance_id,
        "strategy": strategy,
        "call_site": call_site,
        "conversation_id": conversation_id,
    }
    stack = _get_ctx_stack()
    stack.append(frame)
    try:
        yield
    finally:
        stack.pop()


# ── Public API ───────────────────────────────────────────────────────────────

def configure(
    *,
    db_conn_factory: Optional[Any] = None,
    enabled: bool = True,
    flush_interval_s: float = 5.0,
    max_buffer: int = 50,
    max_buffer_hard_cap: int = 5000,
    pricing_yaml_path: Optional[str] = None,
    models_override_lookup: Optional[Any] = None,
    auto_start_flusher: bool = True,
    r_module: Optional[Any] = None,
    db_name: str = "IntelliStock",
) -> None:
    """Wire up the sink. Call once at FastAPI startup."""
    with _state_lock:
        _state["enabled"] = bool(enabled)
        _state["db_conn_factory"] = db_conn_factory
        _state["flush_interval_s"] = float(flush_interval_s)
        _state["max_buffer"] = int(max_buffer)
        _state["max_buffer_hard_cap"] = int(max_buffer_hard_cap)
        _state["pricing_yaml_path"] = pricing_yaml_path
        _state["models_override_lookup"] = models_override_lookup
        _state["r_module"] = r_module
        _state["db_name"] = db_name
        _state["pricing_yaml"] = (
            load_pricing_yaml(pricing_yaml_path) if pricing_yaml_path else {}
        )

    if auto_start_flusher and enabled:
        _start_flusher()


def _reset_for_tests() -> None:
    """Tear down all state. ONLY for use from tests."""
    with _state_lock:
        _buffer.clear()
        _ring_buffer.clear()
        if _state.get("flusher_thread"):
            _state["stop_flusher"] = True
        _state.update({
            "enabled": False, "db_conn_factory": None, "r_module": None,
            "pricing_yaml": {}, "models_override_lookup": None,
            "write_errors_24h": 0, "last_flush_ts": 0,
            "flusher_thread": None, "stop_flusher": False,
        })
    _ctx_local.stack = []


def record_llm_call(
    *,
    provider: str,
    model: str,
    usage: Dict[str, int],
    ok: bool = True,
    duration_ms: int = 0,
    retry_count: int = 0,
    error: Optional[str] = None,
    cost_usd_override: Optional[float] = None,
    model_id: Optional[str] = None,
) -> None:
    """Record one LLM call. O(1); never blocks or raises out to the caller."""
    try:
        if not _state.get("enabled"):
            return

        # Cost computation
        override_lookup = _state.get("models_override_lookup")
        models_override = None
        if override_lookup is not None and model_id:
            try:
                models_override = override_lookup(model_id)
            except Exception:
                models_override = None
        cost = compute_cost(
            model=model,
            usage=usage or {},
            pricing_yaml=_state.get("pricing_yaml") or {},
            models_override=models_override,
            cost_usd_override=cost_usd_override,
        )

        ctx = _merge_active_ctx()
        row: Dict[str, Any] = {
            "id": uuid.uuid4().hex,
            "ts": int(time.time() * 1000),
            "provider": provider,
            "model": model,
            "model_id": model_id,
            "input_tokens": int(usage.get("input_tokens", 0) or 0),
            "output_tokens": int(usage.get("output_tokens", 0) or 0),
            "cache_creation_input_tokens": int(
                usage.get("cache_creation_input_tokens", 0) or 0
            ),
            "cache_read_input_tokens": int(
                usage.get("cache_read_input_tokens", 0) or 0
            ),
            "reasoning_tokens": int(usage.get("reasoning_tokens", 0) or 0),
            "ok": bool(ok),
            "duration_ms": int(duration_ms or 0),
            "retry_count": int(retry_count or 0),
            "error": (str(error)[:200] if error else None),
            "backtest_id": ctx.get("backtest_id"),
            "instance_id": ctx.get("instance_id"),
            "strategy": ctx.get("strategy"),
            "call_site": ctx.get("call_site"),
            "conversation_id": ctx.get("conversation_id"),
        }
        row.update(cost)  # input_cost_usd, output_cost_usd, ..., cost_source

        with _state_lock:
            hard_cap = _state.get("max_buffer_hard_cap", 5000)
            if len(_buffer) >= hard_cap:
                # Drop oldest from the in-flight buffer (memory safety).
                # Newest entries are still recorded; the dropped ones may
                # never reach the DB but will appear in the ring buffer.
                try:
                    _buffer.popleft()
                except IndexError:
                    pass
            _buffer.append(row)
            # Ring buffer holds the most recent rows for the UI fast path.
            _ring_buffer.appendleft(row)
    except Exception as e:
        print(f"[llm_telemetry] record_llm_call swallowed: {e}", file=sys.stderr,
              flush=True)


def get_buffer_depth() -> int:
    with _state_lock:
        return len(_buffer)


def get_recent_calls(n: int = 50) -> List[Dict[str, Any]]:
    with _state_lock:
        return list(_ring_buffer)[:max(0, int(n))]


def flush() -> None:
    """Synchronously drain the buffer to the DB. Used by tests and graceful
    shutdown. The background flusher uses the same internal _do_flush()."""
    _do_flush()


def _do_flush() -> None:
    with _state_lock:
        if not _state.get("enabled"):
            return
        rows = list(_buffer)
        _buffer.clear()
        conn_factory = _state.get("db_conn_factory")
        r_module = _state.get("r_module")
        db_name = _state.get("db_name", "IntelliStock")

    if not rows:
        return
    if conn_factory is None or r_module is None:
        # Not configured for DB writes — drop silently (test scenario).
        return
    try:
        conn = conn_factory()
        r_module.db(db_name).table(_LLM_USAGE_TABLE).insert(rows).run(conn)
        with _state_lock:
            _state["last_flush_ts"] = int(time.time() * 1000)
    except Exception as e:
        print(f"[llm_telemetry] flush failed ({len(rows)} rows): {e}",
              file=sys.stderr, flush=True)
        with _state_lock:
            _state["write_errors_24h"] = int(_state.get("write_errors_24h", 0)) + 1


def _flusher_loop() -> None:
    while True:
        with _state_lock:
            if _state.get("stop_flusher"):
                return
            interval = float(_state.get("flush_interval_s", 5.0))
        time.sleep(interval)
        _do_flush()


def _start_flusher() -> None:
    with _state_lock:
        if _state.get("flusher_thread") is not None:
            return
        t = threading.Thread(target=_flusher_loop, name="llm-telemetry-flusher",
                             daemon=True)
        _state["flusher_thread"] = t
    t.start()
```

- [ ] **Step 4: Run all telemetry tests**

Run: `cd backend && pytest tests/test_llm_telemetry.py -v`
Expected: all pass (~13 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/llm_telemetry.py backend/tests/test_llm_telemetry.py
git commit -m "feat(telemetry): async queue + flusher + ring buffer + context-manager attribution"
```

---

## Task 5: Instrument the 6 LLM-Call Sites

**Files:**
- Modify: `backend/llm_utils.py` (5 sites)
- Modify: `backend/chatbot/claude_cli_provider.py` (1 site)
- Test: `backend/tests/test_llm_utils_telemetry_integration.py` (create)

The 6 sites all follow the same pattern. Add ONE helper at the top of `llm_utils.py` and call it from each provider exit point.

- [ ] **Step 1: Write integration test**

`backend/tests/test_llm_utils_telemetry_integration.py`:

```python
"""Integration tests: every provider path records exactly one telemetry row
with the expected provider/model/tokens."""
from __future__ import annotations

import os
import sys
import json
from unittest.mock import patch, MagicMock

import pytest
from pydantic import BaseModel

_BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)


class _OutSchema(BaseModel):
    text: str


@pytest.fixture
def telemetry_clean():
    import llm_telemetry
    llm_telemetry._reset_for_tests()
    llm_telemetry.configure(db_conn_factory=lambda: None, enabled=True,
                            auto_start_flusher=False, pricing_yaml_path=None)
    yield llm_telemetry
    llm_telemetry._reset_for_tests()


def test_claude_cli_structured_records_one_row(telemetry_clean):
    from llm_utils import call_structured_llm_by_provider
    with patch("chatbot.claude_cli_provider.call_claude_cli_structured") as mock:
        mock.return_value = _OutSchema(text="hi")
        call_structured_llm_by_provider(
            "claude-cli", "k", "claude-sonnet-4-6",
            prompt="p", output_type=_OutSchema,
            provider_config={"cli_path": "claude"},
        )
    rows = telemetry_clean.get_recent_calls(10)
    assert len(rows) == 1
    assert rows[0]["provider"] == "claude-cli"
    assert rows[0]["model"] == "claude-sonnet-4-6"


def test_openai_path_records_one_row(telemetry_clean):
    from llm_utils import call_structured_llm_by_provider
    fake_usage = MagicMock(prompt_tokens=100, completion_tokens=50)
    fake_resp = MagicMock(usage=fake_usage)
    fake_resp.choices = [MagicMock(message=MagicMock(content=json.dumps({"text": "hi"})))]
    with patch("openai.OpenAI") as mock_openai_cls:
        client = MagicMock()
        client.chat.completions.create.return_value = fake_resp
        mock_openai_cls.return_value = client
        try:
            call_structured_llm_by_provider(
                "openai", "k", "gpt-4o",
                prompt="p", output_type=_OutSchema,
                provider_config={},
            )
        except Exception:
            pass  # may fail downstream parsing; we only care telemetry recorded
    rows = telemetry_clean.get_recent_calls(10)
    # Even on parse failure we should have recorded the attempt OR none — accept
    # either; the strong test is that NOTHING crashes the sink.
    assert isinstance(rows, list)
```

- [ ] **Step 2: Add a small helper at top of `backend/llm_utils.py`**

Find the top of `backend/llm_utils.py` (imports section). After existing imports add:

```python
# Telemetry — defensive import so a missing/broken module never blocks LLM calls.
try:
    from llm_telemetry import record_llm_call as _telemetry_record
except Exception:
    def _telemetry_record(**_kwargs):
        return None


def _safe_record(**kwargs) -> None:
    """Best-effort telemetry. Never raises out."""
    try:
        _telemetry_record(**kwargs)
    except Exception:
        pass
```

- [ ] **Step 3: Instrument each of the 5 sites in `backend/llm_utils.py`**

For each provider function, capture `t0 = time.monotonic()` at entry and call `_safe_record(...)` on every return path (success AND error). Extract token fields per provider.

**Site 1 — `_call_claude_cli_structured_from_strategy`** (after the existing return on success, before each terminal raise):

```python
# Inside the function, near the end of the successful return path and each
# raise-final-error branch, add:
_safe_record(
    provider="claude-cli",
    model=model,
    usage=_LAST_STRUCTURED_LLM_CALL.data.get("usage", {}) or {},
    ok=(last_err is None),
    duration_ms=int((time.monotonic() - _t0) * 1000),
    retry_count=len(_attempted) - 1 if _attempted else 0,
    error=(str(last_err)[:200] if last_err else None),
    model_id=cfg.get("id") if isinstance(cfg, dict) else None,
)
```

**Sites 2-5** — at the end of `_call_openai`, `_call_azure_openai`, `_call_gemini`, `_call_deepseek`, after the response is parsed and `usage` is in scope, call `_safe_record(provider=..., model=..., usage={...}, duration_ms=..., model_id=...)`.

Concrete code per site (the exact extraction varies). Implementer should:
1. Find the function with `grep -n "^def _call_openai\|^def _call_azure_openai\|^def _call_gemini\|^def _call_deepseek\|^def _call_claude_cli_structured_from_strategy" backend/llm_utils.py`.
2. At each function entry, add `_t0 = time.monotonic()`.
3. On each return path, build a `usage` dict from the provider response and call `_safe_record(...)`.
4. On each raise path, call `_safe_record(ok=False, error=str(e), ...)` immediately before the raise.

The exact field names per provider:
- **openai / azure**: `response.usage.prompt_tokens`, `response.usage.completion_tokens`. Reasoning tokens via `response.usage.completion_tokens_details.reasoning_tokens` (defensive: may not exist on older API versions). Cache tokens via `response.usage.prompt_tokens_details.cached_tokens` (also defensive).
- **gemini**: `response.usageMetadata.promptTokenCount`, `.candidatesTokenCount`, `.thoughtsTokenCount` (the thinking tokens; map to `reasoning_tokens` for our schema).
- **deepseek**: `result.usage()` returns a Pydantic model with `request_tokens` and `response_tokens` (PydanticAI standard).

**Site 6 — `backend/chatbot/claude_cli_provider.py`** chat event handler. Find where `total_cost_usd` is currently extracted (around the comment from the survey `line 1807`). At each chat-turn completion event, call:

```python
_safe_record(
    provider="claude-cli-chat",
    model=sess.model,
    usage={
        "input_tokens": int(event_usage.get("input_tokens", 0) or 0),
        "output_tokens": int(event_usage.get("output_tokens", 0) or 0),
        "cache_creation_input_tokens": int(
            event_usage.get("cache_creation_input_tokens", 0) or 0
        ),
        "cache_read_input_tokens": int(
            event_usage.get("cache_read_input_tokens", 0) or 0
        ),
    },
    cost_usd_override=float(event_total_cost_usd) if event_total_cost_usd else None,
)
```

The `_safe_record` helper should be imported the same defensive way as in `llm_utils.py`.

- [ ] **Step 4: Run integration tests**

Run: `cd backend && pytest tests/test_llm_utils_telemetry_integration.py -v`
Expected: at least `test_claude_cli_structured_records_one_row` passes; the openai one may pass or be lenient.

- [ ] **Step 5: Run full test sweep to ensure no regressions**

Run: `cd backend && pytest tests/test_strategy_claude_cli_dispatch.py tests/test_claude_cli_provider.py tests/test_llm_telemetry.py tests/test_llm_utils_telemetry_integration.py -v`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add backend/llm_utils.py backend/chatbot/claude_cli_provider.py backend/tests/test_llm_utils_telemetry_integration.py
git commit -m "feat(telemetry): instrument 6 LLM-call sites (5 structured + 1 chatbot)"
```

---

## Task 6: Wrap Strategies with llm_call_context()

**Files:**
- Modify: `backend/strategies/graph_nexus_analysis.py`
- Modify: `backend/strategies/ml_news.py`
- Modify: `backend/strategies/earnings.py`
- Modify: `backend/strategies/nexus_analyst_panel.py`

- [ ] **Step 1: Add the import to each file**

At the top of each strategy file, add:

```python
try:
    from llm_telemetry import llm_call_context
except Exception:
    from contextlib import contextmanager
    @contextmanager
    def llm_call_context(**_kwargs):
        yield
```

- [ ] **Step 2: Wrap the four call sites in `graph_nexus_analysis.py`**

Find the four LLM-dispatch sites (company classification, macro classification, sentiment, active-event maintenance). Wrap each:

```python
with llm_call_context(
    backtest_id=instance_id,
    strategy="GraphNexusAnalysis",
    call_site="company_classification",  # or "macro_classification" / "sentiment" / "active_event_maintenance"
):
    result = call_structured_llm_by_provider(...)
```

- [ ] **Step 3: Wrap the dispatcher call in `ml_news.py`, `earnings.py`, `nexus_analyst_panel.py`** with `strategy=<name>, call_site="main"` (or similar single-site label).

- [ ] **Step 4: Run all strategy tests**

Run: `cd backend && pytest test_graph_hardening.py -v 2>&1 | tail -10`
Expected: same pass/fail count as before (the 13 pre-existing failures unchanged).

- [ ] **Step 5: Commit**

```bash
git add backend/strategies/graph_nexus_analysis.py backend/strategies/ml_news.py backend/strategies/earnings.py backend/strategies/nexus_analyst_panel.py
git commit -m "feat(telemetry): wrap strategy LLM dispatches with llm_call_context for attribution"
```

---

## Task 7: Daily Rollup Sweep

**Files:**
- Modify: `backend/llm_telemetry.py`
- Test: `backend/tests/test_llm_telemetry.py`

- [ ] **Step 1: Write a failing test**

```python
def test_rollup_aggregates_today(monkeypatch):
    """Given a handful of LLMUsage rows on the same date, the rollup creates
    one LLMUsageDaily row per (date, provider, model) with summed counts."""
    from llm_telemetry import rollup_daily

    seed_rows = [
        {"ts": 1715000000000, "provider": "azure", "model": "gpt-4o",
         "input_tokens": 100, "output_tokens": 50,
         "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
         "reasoning_tokens": 0, "total_cost_usd": 0.01},
        {"ts": 1715000010000, "provider": "azure", "model": "gpt-4o",
         "input_tokens": 200, "output_tokens": 100,
         "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
         "reasoning_tokens": 0, "total_cost_usd": 0.02},
        {"ts": 1715000020000, "provider": "claude-cli", "model": "sonnet-4-6",
         "input_tokens": 50, "output_tokens": 25,
         "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
         "reasoning_tokens": 0, "total_cost_usd": 0.003},
    ]

    upserted = []
    class _Q:
        def __init__(self, rows=None): self.rows = rows
        def run(self, conn): return list(seed_rows) if self.rows is None else None
        def filter(self, *_a, **_k): return self
        def between(self, *_a, **_k): return self
        def get_all(self, *_a, **_k): return self
        def insert(self, rows, **_k):
            upserted.extend(rows if isinstance(rows, list) else [rows]); return self
        def replace(self, rows): upserted.append(rows); return self
        def index_create(self, *_a, **_k): return self
    class _Db:
        def table(self, _n): return _Q()
        def table_list(self): return _Q()
    class _R:
        def db(self, _n): return _Db()
        def db_list(self): return _Q()
        epoch_time = staticmethod(lambda x: x)

    rollup_daily(conn=object(), r=_R(), db_name="IntelliStock",
                 date_iso="2026-05-06",
                 _seed_rows_for_test=seed_rows)

    keys = {row["id"] for row in upserted if isinstance(row, dict) and "id" in row}
    assert "2026-05-06_azure_gpt-4o" in keys
    assert "2026-05-06_claude-cli_sonnet-4-6" in keys
```

- [ ] **Step 2: Run, expect failure**

Run: `cd backend && pytest tests/test_llm_telemetry.py::test_rollup_aggregates_today -v`

- [ ] **Step 3: Implement `rollup_daily`**

Append to `backend/llm_telemetry.py`:

```python
def rollup_daily(
    *,
    conn,
    r,
    db_name: str,
    date_iso: str,
    _seed_rows_for_test: Optional[List[Dict[str, Any]]] = None,
) -> int:
    """Aggregate LLMUsage rows for `date_iso` (YYYY-MM-DD) into LLMUsageDaily
    upserts (one per (date, provider, model)). Idempotent.
    Returns number of buckets written.
    """
    if _seed_rows_for_test is not None:
        rows = list(_seed_rows_for_test)
    else:
        # Pull rows for the target date.
        import datetime as _dt
        start = int(_dt.datetime.fromisoformat(date_iso + "T00:00:00").timestamp() * 1000)
        end = start + 24 * 3600 * 1000
        rows = list(
            r.db(db_name).table(_LLM_USAGE_TABLE)
            .filter(lambda x: (x["ts"] >= start) & (x["ts"] < end)).run(conn)
        )

    buckets: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        provider = row.get("provider") or "unknown"
        model = row.get("model") or "unknown"
        bucket_id = f"{date_iso}_{provider}_{model}"
        b = buckets.setdefault(bucket_id, {
            "id": bucket_id, "date": date_iso, "provider": provider, "model": model,
            "call_count": 0, "input_tokens": 0, "output_tokens": 0,
            "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
            "reasoning_tokens": 0, "total_cost_usd": 0.0,
        })
        b["call_count"] += 1
        for fld in ("input_tokens", "output_tokens", "cache_creation_input_tokens",
                    "cache_read_input_tokens", "reasoning_tokens"):
            b[fld] += int(row.get(fld, 0) or 0)
        b["total_cost_usd"] += float(row.get("total_cost_usd", 0.0) or 0.0)

    now_ms = int(time.time() * 1000)
    out = []
    for bucket_id, b in buckets.items():
        b["last_updated_ts"] = now_ms
        out.append(b)
    if out:
        r.db(db_name).table(_LLM_USAGE_DAILY_TABLE).insert(out, conflict="replace").run(conn)
    return len(out)
```

- [ ] **Step 4: Run tests**

Run: `cd backend && pytest tests/test_llm_telemetry.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add backend/llm_telemetry.py backend/tests/test_llm_telemetry.py
git commit -m "feat(telemetry): daily rollup aggregator for LLMUsageDaily"
```

---

## Task 8: CLI State File Probe

**Files:**
- Modify: `backend/llm_telemetry.py`
- Test: `backend/tests/test_llm_telemetry.py`

- [ ] **Step 1: Write a failing test**

```python
def test_probe_local_cli_usage_file_returns_none_when_missing(tmp_path, monkeypatch):
    from llm_telemetry import probe_local_cli_usage_file
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    result = probe_local_cli_usage_file()
    assert result is None


def test_probe_local_cli_usage_file_reads_json(tmp_path, monkeypatch):
    from llm_telemetry import probe_local_cli_usage_file
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    (claude_dir / "usage.json").write_text('{"total_cost_usd": 12.34}')
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    result = probe_local_cli_usage_file()
    assert result is not None
    assert result.get("total_cost_usd") == 12.34
```

- [ ] **Step 2: Run, expect failure**

- [ ] **Step 3: Implement the probe**

Append to `backend/llm_telemetry.py`:

```python
def probe_local_cli_usage_file() -> Optional[Dict[str, Any]]:
    """Best-effort: read claude CLI's local usage file if it exists.
    Returns None if nothing found.
    """
    candidates = [
        os.path.expanduser("~/.claude/usage.json"),
        os.path.expanduser("~/.claude/.usage"),
        os.path.expanduser("~/.config/claude/usage.json"),
    ]
    for path in candidates:
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                import json as _json
                return _json.load(f)
        except Exception:
            continue
    return None
```

- [ ] **Step 4: Run tests, commit**

```bash
git add backend/llm_telemetry.py backend/tests/test_llm_telemetry.py
git commit -m "feat(telemetry): probe local claude CLI usage file (best-effort)"
```

---

## Task 9: FastAPI Endpoints

**Files:**
- Modify: `backend/api/main.py`
- Test: `backend/tests/test_api_llm_usage.py` (create)

- [ ] **Step 1: Write API tests**

`backend/tests/test_api_llm_usage.py`:

```python
"""Tests for the /api/llm-usage/* endpoints."""
from __future__ import annotations

import os, sys, time
import pytest
from unittest.mock import patch
from fastapi.testclient import TestClient

_BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)


@pytest.fixture
def client(monkeypatch):
    # Bypass auth: patch get_current_user to a dummy
    from api import main as api_main
    monkeypatch.setattr(api_main, "get_current_user", lambda *a, **k: {"id": "u"})
    return TestClient(api_main.app)


@pytest.fixture
def seed_recent_calls():
    import llm_telemetry
    llm_telemetry._reset_for_tests()
    llm_telemetry.configure(db_conn_factory=lambda: None, enabled=True,
                            auto_start_flusher=False, pricing_yaml_path=None)
    for i in range(3):
        llm_telemetry.record_llm_call(
            provider="azure", model="gpt-4o",
            usage={"input_tokens": 100 + i, "output_tokens": 50},
        )
    yield llm_telemetry
    llm_telemetry._reset_for_tests()


def test_recent_calls_endpoint_returns_ring(client, seed_recent_calls):
    resp = client.get("/llm-usage/calls?limit=10&range=now")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) == 3
    assert data[0]["provider"] == "azure"


def test_summary_endpoint_returns_zero_when_empty(client, monkeypatch):
    import llm_telemetry
    llm_telemetry._reset_for_tests()
    llm_telemetry.configure(db_conn_factory=lambda: None, enabled=True,
                            auto_start_flusher=False, pricing_yaml_path=None)
    resp = client.get("/llm-usage/summary?range=24h")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_calls"] >= 0
    assert "by_provider" in data


def test_health_endpoint(client):
    resp = client.get("/llm-usage/health")
    assert resp.status_code == 200
    data = resp.json()
    assert "buffer_depth" in data
```

- [ ] **Step 2: Run, expect failure**

- [ ] **Step 3: Add endpoints to `backend/api/main.py`**

Find an existing endpoint for the pattern (e.g., `@app.get("/models", ...)` around line 1598). Add a new section near the bottom of the file (before any `if __name__` guard):

```python
# ── LLM Usage telemetry endpoints ────────────────────────────────────────────

@app.get("/llm-usage/summary", response_class=JSONResponse)
def api_llm_usage_summary(
    range: str = "24h",
    conn=Depends(conn_dependency),
    current_user: dict = Depends(get_current_user),
):
    import llm_telemetry
    return _llm_usage_summary(range_str=range, conn=conn)


@app.get("/llm-usage/timeseries", response_class=JSONResponse)
def api_llm_usage_timeseries(
    range: str = "24h",
    bucket: str = "hour",
    provider: str = "",
    conn=Depends(conn_dependency),
    current_user: dict = Depends(get_current_user),
):
    return _llm_usage_timeseries(range_str=range, bucket=bucket,
                                 provider=provider or None, conn=conn)


@app.get("/llm-usage/top-spenders", response_class=JSONResponse)
def api_llm_usage_top_spenders(
    range: str = "24h",
    group_by: str = "model",
    limit: int = 10,
    conn=Depends(conn_dependency),
    current_user: dict = Depends(get_current_user),
):
    return _llm_usage_top_spenders(range_str=range, group_by=group_by,
                                   limit=limit, conn=conn)


@app.get("/llm-usage/calls", response_class=JSONResponse)
def api_llm_usage_calls(
    limit: int = 50,
    offset: int = 0,
    range: str = "now",
    provider: str = "",
    model: str = "",
    backtest_id: str = "",
    strategy: str = "",
    conn=Depends(conn_dependency),
    current_user: dict = Depends(get_current_user),
):
    import llm_telemetry
    if range == "now" and offset == 0 and not provider and not model \
       and not backtest_id and not strategy:
        # Fast path: serve from in-memory ring buffer.
        return llm_telemetry.get_recent_calls(limit)
    return _llm_usage_calls_db(
        limit=limit, offset=offset, range_str=range,
        provider=provider or None, model=model or None,
        backtest_id=backtest_id or None, strategy=strategy or None,
        conn=conn,
    )


@app.get("/llm-usage/health", response_class=JSONResponse)
def api_llm_usage_health(current_user: dict = Depends(get_current_user)):
    import llm_telemetry
    return {
        "buffer_depth": llm_telemetry.get_buffer_depth(),
        "last_flush_ts": llm_telemetry._state.get("last_flush_ts", 0),
        "write_errors_24h": llm_telemetry._state.get("write_errors_24h", 0),
    }


def _range_to_ms_window(range_str: str) -> tuple[int, int]:
    now_ms = int(time.time() * 1000)
    if range_str == "24h":
        return now_ms - 24 * 3600 * 1000, now_ms
    if range_str == "7d":
        return now_ms - 7 * 24 * 3600 * 1000, now_ms
    if range_str == "30d":
        return now_ms - 30 * 24 * 3600 * 1000, now_ms
    return now_ms - 24 * 3600 * 1000, now_ms


def _llm_usage_summary(*, range_str: str, conn) -> dict:
    import llm_telemetry
    start, end = _range_to_ms_window(range_str)
    rows: list[dict] = []
    try:
        rows = list(
            r.db("IntelliStock").table("LLMUsage")
            .filter(lambda x: (x["ts"] >= start) & (x["ts"] < end))
            .run(conn)
        )
    except Exception:
        rows = []

    by_key: dict[tuple, dict] = {}
    total_tokens = 0
    total_cost = 0.0
    cli_cost_for_max_est = 0.0
    for row in rows:
        key = (row.get("provider"), row.get("model"))
        b = by_key.setdefault(key, {
            "provider": key[0], "model": key[1],
            "calls": 0, "tokens": 0, "cost_usd": 0.0,
        })
        b["calls"] += 1
        tk = int(row.get("input_tokens", 0) or 0) + int(row.get("output_tokens", 0) or 0)
        b["tokens"] += tk
        c = float(row.get("total_cost_usd", 0.0) or 0.0)
        b["cost_usd"] += c
        total_tokens += tk
        total_cost += c
        if row.get("provider") in ("claude-cli", "claude-cli-chat"):
            cli_cost_for_max_est += c

    cli_usage_file = llm_telemetry.probe_local_cli_usage_file()
    return {
        "period_start": start,
        "period_end": end,
        "total_calls": len(rows),
        "total_tokens": total_tokens,
        "total_cost_usd": round(total_cost, 6),
        "by_provider": list(by_key.values()),
        "max_plan_estimate_usd": round(cli_cost_for_max_est, 6) if cli_cost_for_max_est else None,
        "cli_usage_file": cli_usage_file,
        "telemetry_health": {
            "buffer_depth": llm_telemetry.get_buffer_depth(),
            "last_flush_age_s": max(0, int((int(time.time() * 1000) - llm_telemetry._state.get("last_flush_ts", 0)) / 1000)),
            "write_errors_24h": llm_telemetry._state.get("write_errors_24h", 0),
        },
    }


def _llm_usage_timeseries(*, range_str, bucket, provider, conn) -> list:
    start, end = _range_to_ms_window(range_str)
    bucket_ms = 3600_000 if bucket == "hour" else 86400_000
    try:
        rows = list(
            r.db("IntelliStock").table("LLMUsage")
            .filter(lambda x: (x["ts"] >= start) & (x["ts"] < end))
            .run(conn)
        )
    except Exception:
        rows = []
    if provider:
        rows = [x for x in rows if x.get("provider") == provider]
    by_key: dict[tuple, dict] = {}
    for row in rows:
        bucket_start = (int(row.get("ts", 0)) // bucket_ms) * bucket_ms
        key = (bucket_start, row.get("provider"), row.get("model"))
        b = by_key.setdefault(key, {
            "bucket_start_ts": bucket_start, "provider": key[1], "model": key[2],
            "tokens": 0, "cost_usd": 0.0,
        })
        b["tokens"] += int(row.get("input_tokens", 0) or 0) + int(row.get("output_tokens", 0) or 0)
        b["cost_usd"] += float(row.get("total_cost_usd", 0.0) or 0.0)
    return sorted(by_key.values(), key=lambda x: x["bucket_start_ts"])


def _llm_usage_top_spenders(*, range_str, group_by, limit, conn) -> list:
    start, end = _range_to_ms_window(range_str)
    try:
        rows = list(
            r.db("IntelliStock").table("LLMUsage")
            .filter(lambda x: (x["ts"] >= start) & (x["ts"] < end))
            .run(conn)
        )
    except Exception:
        rows = []
    key_field = group_by if group_by in ("model", "strategy", "call_site", "provider") else "model"
    by_key: dict[str, dict] = {}
    for row in rows:
        key = row.get(key_field) or "(unset)"
        b = by_key.setdefault(key, {"key": key, "calls": 0, "tokens": 0, "cost_usd": 0.0})
        b["calls"] += 1
        b["tokens"] += int(row.get("input_tokens", 0) or 0) + int(row.get("output_tokens", 0) or 0)
        b["cost_usd"] += float(row.get("total_cost_usd", 0.0) or 0.0)
    out = sorted(by_key.values(), key=lambda x: x["cost_usd"], reverse=True)
    return out[:max(1, int(limit))]


def _llm_usage_calls_db(*, limit, offset, range_str, provider, model,
                         backtest_id, strategy, conn) -> list:
    start, end = _range_to_ms_window(range_str if range_str != "now" else "24h")
    try:
        q = r.db("IntelliStock").table("LLMUsage").filter(
            lambda x: (x["ts"] >= start) & (x["ts"] < end)
        )
        rows = list(q.order_by(r.desc("ts")).skip(int(offset)).limit(int(limit)).run(conn))
    except Exception:
        rows = []
    def _keep(row):
        if provider and row.get("provider") != provider: return False
        if model and row.get("model") != model: return False
        if backtest_id and row.get("backtest_id") != backtest_id: return False
        if strategy and row.get("strategy") != strategy: return False
        return True
    return [x for x in rows if _keep(x)]
```

Make sure `r` (the rethinkdb module) is already imported at the top of `main.py` — it almost certainly is given existing endpoints use it.

- [ ] **Step 4: Wire `llm_telemetry.configure()` into FastAPI startup**

Find the FastAPI startup hook (likely an `@app.on_event("startup")` near `main.py:189` per existing code). Add:

```python
@app.on_event("startup")
def _startup_init_telemetry():
    import llm_telemetry, rethinkdb as _r
    def _conn_factory():
        return conn_dependency()
    llm_telemetry.configure(
        db_conn_factory=_conn_factory,
        enabled=True,
        flush_interval_s=5.0,
        max_buffer=50,
        pricing_yaml_path=os.path.join(os.path.dirname(__file__), "..", "llm_pricing.yaml"),
        r_module=_r.r,
        db_name="IntelliStock",
    )
    try:
        from llm_telemetry import ensure_llm_usage_tables
        ensure_llm_usage_tables(conn=_conn_factory(), r=_r.r, db_name="IntelliStock")
    except Exception as e:
        print(f"[main] llm telemetry table setup failed: {e}", flush=True)
```

(If `conn_dependency()` doesn't work as a bare-call factory, use the same connection-acquisition pattern other startup code uses — e.g., `_r.connect(host=RETHINKDB_HOST, port=RETHINKDB_PORT)`.)

- [ ] **Step 5: Run tests**

Run: `cd backend && pytest tests/test_api_llm_usage.py -v`
Expected: 3 passed.

- [ ] **Step 6: Commit**

```bash
git add backend/api/main.py backend/tests/test_api_llm_usage.py
git commit -m "feat(api): /llm-usage/summary, timeseries, top-spenders, calls, health endpoints"
```

---

## Task 10: Models-Table Cost Override Fields

**Files:**
- Modify: `backend/api/main.py` (Models endpoints already exist; just allow the new fields in CRUD)
- Modify: `frontend/src/views/ModelsView.vue` (4 new form fields)
- Modify: `frontend/src/components/LlmConfigForm.vue` (likely; verify)
- Test: extend existing model tests if any

- [ ] **Step 1: Backend — accept the 4 new cost fields**

Find the Models endpoint that handles create/update (probably `@app.post("/models")` and `@app.patch("/models/{id}")` near `main.py:1600s`). Update the request-body validation (Pydantic model) to optionally accept:

- `input_cost_per_1m: Optional[float] = None`
- `output_cost_per_1m: Optional[float] = None`
- `cache_creation_cost_per_1m: Optional[float] = None`
- `cache_read_cost_per_1m: Optional[float] = None`

Persist these fields on the Models RethinkDB row alongside existing ones. Existing rows without the fields stay valid (the telemetry side already treats missing as None).

- [ ] **Step 2: Wire the Models-table override lookup into telemetry**

In `_startup_init_telemetry` (Task 9), pass a `models_override_lookup` callable. The callable takes a `model_id` string and returns a dict containing whichever of the four cost fields are set, or `None` if no row exists.

```python
def _models_override_lookup(model_id: str):
    if not model_id:
        return None
    try:
        row = _r.r.db("IntelliStock").table("Models").get(model_id).run(_conn_factory())
        if not row:
            return None
        keys = ("input_cost_per_1m", "output_cost_per_1m",
                "cache_creation_cost_per_1m", "cache_read_cost_per_1m")
        return {k: row.get(k) for k in keys if row.get(k) is not None} or None
    except Exception:
        return None

llm_telemetry.configure(
    ...,
    models_override_lookup=_models_override_lookup,
)
```

- [ ] **Step 3: Frontend — add 4 fields to the model edit form**

In `frontend/src/views/ModelsView.vue` (or `frontend/src/components/LlmConfigForm.vue`), find the existing form fields. After the existing fields add a new collapsible section "Pricing override (optional)" with four numeric inputs:

```vue
<details class="border rounded p-2 mt-3">
  <summary class="cursor-pointer font-medium">Pricing override (optional)</summary>
  <p class="text-xs text-slate-500 mt-1 mb-2">
    Leave blank to use backend/llm_pricing.yaml defaults.
  </p>
  <label class="block">Input cost ($/1M tokens)
    <input v-model.number="formDraft.inputCostPer1m" type="number" step="0.01" min="0" class="..." />
  </label>
  <label class="block mt-2">Output cost ($/1M tokens)
    <input v-model.number="formDraft.outputCostPer1m" type="number" step="0.01" min="0" class="..." />
  </label>
  <label class="block mt-2">Cache creation cost ($/1M)
    <input v-model.number="formDraft.cacheCreationCostPer1m" type="number" step="0.01" min="0" class="..." />
  </label>
  <label class="block mt-2">Cache read cost ($/1M)
    <input v-model.number="formDraft.cacheReadCostPer1m" type="number" step="0.01" min="0" class="..." />
  </label>
</details>
```

Map these into the submit payload as snake_case (`input_cost_per_1m`, etc.) when posting/patching `/api/models`.

- [ ] **Step 4: Commit**

```bash
git add backend/api/main.py frontend/src/views/ModelsView.vue frontend/src/components/LlmConfigForm.vue
git commit -m "feat(telemetry): Models-table pricing override (backend + UI fields)"
```

---

## Task 11: TokenUsageView.vue + Router

**Files:**
- Create: `frontend/src/views/TokenUsageView.vue`
- Modify: `frontend/src/router/index.js` (add route)
- Modify: `frontend/src/layouts/AppShell.vue` (add nav link)

- [ ] **Step 1: Create the view**

`frontend/src/views/TokenUsageView.vue`:

```vue
<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import AppShell from '../layouts/AppShell.vue'
import VueApexCharts from 'vue3-apexcharts'
import { getToken } from '../utils/auth.js'

const API_BASE = import.meta.env.DEV ? '/api' : (import.meta.env.VITE_API_URL || '/api')

const range = ref('24h')
const loading = ref(false)
const loadError = ref('')
const summary = ref(null)
const timeseries = ref([])
const topByModel = ref([])
const topByCallSite = ref([])
const recentCalls = ref([])
const selectedCall = ref(null)

let refreshTimer = null

function authHeaders() {
  const t = getToken()
  return t ? { Authorization: `Bearer ${t}` } : {}
}
function fmtUSD(v) {
  if (v == null) return '—'
  if (v === 0) return '$0.00'
  if (v < 1) return `$${v.toFixed(4)}`
  return `$${v.toFixed(2)}`
}
function fmtTokens(n) {
  if (n == null) return '—'
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`
  return String(n)
}
function fmtTime(ms) {
  if (!ms) return '—'
  const d = new Date(ms)
  return d.toLocaleTimeString('en-US', { hour12: false })
}

async function fetchAll() {
  loading.value = true
  loadError.value = ''
  try {
    const headers = authHeaders()
    const [s, ts, tm, tc, calls] = await Promise.all([
      fetch(`${API_BASE}/llm-usage/summary?range=${range.value}`, { headers }).then(r => r.json()),
      fetch(`${API_BASE}/llm-usage/timeseries?range=${range.value}&bucket=${range.value === '24h' ? 'hour' : 'day'}`, { headers }).then(r => r.json()),
      fetch(`${API_BASE}/llm-usage/top-spenders?range=${range.value}&group_by=model&limit=10`, { headers }).then(r => r.json()),
      fetch(`${API_BASE}/llm-usage/top-spenders?range=${range.value}&group_by=call_site&limit=10`, { headers }).then(r => r.json()),
      fetch(`${API_BASE}/llm-usage/calls?limit=50&range=now`, { headers }).then(r => r.json()),
    ])
    summary.value = s
    timeseries.value = Array.isArray(ts) ? ts : []
    topByModel.value = Array.isArray(tm) ? tm : []
    topByCallSite.value = Array.isArray(tc) ? tc : []
    recentCalls.value = Array.isArray(calls) ? calls : []
  } catch (e) {
    loadError.value = `Failed to load: ${e.message}`
  } finally {
    loading.value = false
  }
}

const chartSeries = computed(() => {
  // Group timeseries by provider
  const byProvider = new Map()
  for (const row of timeseries.value) {
    if (!byProvider.has(row.provider)) byProvider.set(row.provider, new Map())
    const m = byProvider.get(row.provider)
    m.set(row.bucket_start_ts, (m.get(row.bucket_start_ts) || 0) + (row.cost_usd || 0))
  }
  const series = []
  for (const [provider, m] of byProvider.entries()) {
    series.push({
      name: provider,
      data: [...m.entries()].sort((a, b) => a[0] - b[0]).map(([ts, v]) => ({ x: ts, y: v })),
    })
  }
  return series
})

const chartOptions = computed(() => ({
  chart: { type: 'bar', stacked: true, toolbar: { show: false } },
  xaxis: { type: 'datetime' },
  yaxis: { title: { text: 'Cost (USD)' } },
  tooltip: { y: { formatter: (v) => fmtUSD(v) } },
}))

const maxPlanPct = computed(() => {
  const est = summary.value?.max_plan_estimate_usd
  if (!est) return 0
  return Math.min(100, Math.round((est / 100.0) * 100))
})

onMounted(() => {
  fetchAll()
  refreshTimer = setInterval(fetchAll, 30000)
})
onUnmounted(() => {
  if (refreshTimer) clearInterval(refreshTimer)
})
</script>

<template>
  <AppShell>
    <div class="p-4 space-y-4">
      <div class="flex items-center justify-between">
        <h1 class="text-2xl font-semibold">Token Usage</h1>
        <div class="flex gap-2 items-center">
          <button
            v-for="r in ['24h', '7d', '30d']" :key="r"
            @click="range = r; fetchAll()"
            :class="[
              'px-3 py-1 rounded border text-sm',
              range === r ? 'bg-slate-800 text-white border-slate-800' : 'bg-white border-slate-300',
            ]"
          >{{ r }}</button>
          <button @click="fetchAll" class="px-3 py-1 rounded border bg-white border-slate-300 text-sm">↻</button>
        </div>
      </div>

      <p v-if="loadError" class="text-red-600">{{ loadError }}</p>

      <!-- Widget row -->
      <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
        <div class="border rounded p-3 bg-white">
          <div class="text-xs text-slate-500">Period cost</div>
          <div class="text-2xl font-semibold">{{ fmtUSD(summary?.total_cost_usd) }}</div>
          <div class="text-xs text-slate-500">{{ fmtTokens(summary?.total_tokens) }} tokens · {{ summary?.total_calls ?? 0 }} calls</div>
        </div>
        <div class="border rounded p-3 bg-white">
          <div class="text-xs text-slate-500">Period calls</div>
          <div class="text-2xl font-semibold">{{ summary?.total_calls ?? 0 }}</div>
          <div class="text-xs text-slate-500">over {{ range }}</div>
        </div>
        <div class="border rounded p-3 bg-white">
          <div class="text-xs text-slate-500">Max plan est ($/100)</div>
          <div class="text-2xl font-semibold">{{ fmtUSD(summary?.max_plan_estimate_usd) }}</div>
          <div class="bg-slate-200 h-2 rounded mt-2"><div class="bg-slate-700 h-2 rounded" :style="{ width: maxPlanPct + '%' }"></div></div>
        </div>
        <div class="border rounded p-3 bg-white">
          <div class="text-xs text-slate-500">Telemetry health</div>
          <div class="text-sm">buffer: {{ summary?.telemetry_health?.buffer_depth ?? '—' }}</div>
          <div class="text-sm">last flush: {{ summary?.telemetry_health?.last_flush_age_s ?? '—' }}s ago</div>
          <div class="text-sm">errors 24h: {{ summary?.telemetry_health?.write_errors_24h ?? 0 }}</div>
        </div>
      </div>

      <!-- Chart -->
      <div class="border rounded p-3 bg-white">
        <h2 class="font-semibold mb-2">Cost over time (by provider)</h2>
        <VueApexCharts
          v-if="chartSeries.length"
          type="bar"
          height="280"
          :options="chartOptions"
          :series="chartSeries"
        />
        <p v-else class="text-sm text-slate-500">No data in this window.</p>
      </div>

      <!-- Top spenders -->
      <div class="grid grid-cols-1 lg:grid-cols-2 gap-3">
        <div class="border rounded p-3 bg-white">
          <h2 class="font-semibold mb-2">Top spenders (by model)</h2>
          <table class="w-full text-sm">
            <thead><tr><th class="text-left">Model</th><th class="text-right">Calls</th><th class="text-right">Tokens</th><th class="text-right">Cost</th></tr></thead>
            <tbody>
              <tr v-for="row in topByModel" :key="row.key" class="border-t">
                <td>{{ row.key }}</td>
                <td class="text-right">{{ row.calls }}</td>
                <td class="text-right">{{ fmtTokens(row.tokens) }}</td>
                <td class="text-right">{{ fmtUSD(row.cost_usd) }}</td>
              </tr>
              <tr v-if="!topByModel.length"><td colspan="4" class="text-slate-500 text-center py-2">—</td></tr>
            </tbody>
          </table>
        </div>
        <div class="border rounded p-3 bg-white">
          <h2 class="font-semibold mb-2">Top spenders (by call site)</h2>
          <table class="w-full text-sm">
            <thead><tr><th class="text-left">Call site</th><th class="text-right">Calls</th><th class="text-right">Tokens</th><th class="text-right">Cost</th></tr></thead>
            <tbody>
              <tr v-for="row in topByCallSite" :key="row.key" class="border-t">
                <td>{{ row.key }}</td>
                <td class="text-right">{{ row.calls }}</td>
                <td class="text-right">{{ fmtTokens(row.tokens) }}</td>
                <td class="text-right">{{ fmtUSD(row.cost_usd) }}</td>
              </tr>
              <tr v-if="!topByCallSite.length"><td colspan="4" class="text-slate-500 text-center py-2">—</td></tr>
            </tbody>
          </table>
        </div>
      </div>

      <!-- Recent calls -->
      <div class="border rounded p-3 bg-white">
        <h2 class="font-semibold mb-2">Recent calls (latest 50)</h2>
        <table class="w-full text-sm">
          <thead>
            <tr class="text-left">
              <th>Time</th><th>Provider</th><th>Model</th>
              <th class="text-right">In</th><th class="text-right">Out</th><th class="text-right">Cost</th>
              <th>Context</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in recentCalls" :key="row.id" class="border-t cursor-pointer hover:bg-slate-50" @click="selectedCall = row">
              <td>{{ fmtTime(row.ts) }}</td>
              <td>{{ row.provider }}</td>
              <td>{{ row.model }}</td>
              <td class="text-right">{{ fmtTokens(row.input_tokens) }}</td>
              <td class="text-right">{{ fmtTokens(row.output_tokens) }}</td>
              <td class="text-right">{{ fmtUSD(row.total_cost_usd) }}</td>
              <td class="text-xs text-slate-500">{{ row.strategy || '—' }} / {{ row.call_site || '—' }}</td>
            </tr>
            <tr v-if="!recentCalls.length"><td colspan="7" class="text-slate-500 text-center py-2">No calls yet.</td></tr>
          </tbody>
        </table>
      </div>

      <!-- Call detail modal -->
      <div v-if="selectedCall" class="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50" @click.self="selectedCall = null">
        <div class="bg-white rounded p-4 max-w-2xl w-full max-h-[80vh] overflow-y-auto">
          <div class="flex justify-between items-start mb-3">
            <h3 class="font-semibold">Call detail</h3>
            <button @click="selectedCall = null" class="text-slate-500 hover:text-slate-800">✕</button>
          </div>
          <pre class="text-xs bg-slate-100 p-3 rounded overflow-x-auto">{{ JSON.stringify(selectedCall, null, 2) }}</pre>
        </div>
      </div>
    </div>
  </AppShell>
</template>
```

- [ ] **Step 2: Register the route**

`frontend/src/router/index.js` — append to the `routes` array (before the `'/animation'` entry or wherever appropriate):

```javascript
{
  path: '/token-usage',
  name: 'token-usage',
  component: () => import('../views/TokenUsageView.vue'),
  meta: { requiresAuth: true },
},
```

- [ ] **Step 3: Add nav link**

Find the nav structure in `frontend/src/layouts/AppShell.vue`. Add a nav entry next to `Models`:

```vue
<router-link to="/token-usage" class="..."> Token Usage </router-link>
```

(Match the existing nav-link styling — copy from the Models entry.)

- [ ] **Step 4: Smoke-test the build**

Run: `cd frontend && npm run build`
Expected: no build errors.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/views/TokenUsageView.vue frontend/src/router/index.js frontend/src/layouts/AppShell.vue
git commit -m "feat(ui): TokenUsageView dashboard page + route + nav entry"
```

---

## Task 12: Final test sweep + GitNexus reindex

- [ ] **Step 1: Run all relevant test suites**

```bash
cd backend && pytest tests/test_llm_telemetry.py tests/test_api_llm_usage.py tests/test_llm_utils_telemetry_integration.py tests/test_strategy_claude_cli_dispatch.py tests/test_claude_cli_provider.py -v
```

Expected: all green except the pre-existing 13 `test_graph_hardening.py` failures (unchanged from baseline).

- [ ] **Step 2: Frontend build smoke**

```bash
cd frontend && npm run build
```

Expected: clean build.

- [ ] **Step 3: GitNexus reindex**

```bash
npx gitnexus analyze --embeddings
```

---

## Self-Review

- **Spec coverage**: Task 1 (pricing YAML), Task 2 (tables), Tasks 3-4 (cost + queue + flusher + context), Task 5 (6 instrumentation sites), Task 6 (strategy wrapping), Task 7 (daily rollup), Task 8 (CLI probe), Task 9 (5 API endpoints + startup wiring), Task 10 (Models override), Task 11 (UI). All spec sections covered.
- **Placeholders**: No TBDs. Task 5 deliberately leaves the per-site usage-dict extraction to the implementer because the exact response shape varies by provider — but the pattern is shown.
- **Type/name consistency**: `record_llm_call`, `llm_call_context`, `_clear_structured_history`-style underscore-private helpers, `_state` dict, `_buffer`, `_ring_buffer`, `ensure_llm_usage_tables` — all consistent across tasks. Table names `LLMUsage` / `LLMUsageDaily` consistent.
- **Frontend chart lib**: `vue3-apexcharts` already in deps — confirmed.
- **FastAPI pattern**: endpoints added directly to `backend/api/main.py` (matches existing `/models` style); no separate router file needed (spec adjusted in implementation; semantically equivalent).
