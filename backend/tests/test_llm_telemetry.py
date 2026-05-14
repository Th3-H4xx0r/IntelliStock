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
