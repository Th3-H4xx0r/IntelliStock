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
    missing or unparseable - telemetry continues with cost_source="unknown"
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
