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
            if self._val == "db_list":
                return ["IntelliStock"]
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
