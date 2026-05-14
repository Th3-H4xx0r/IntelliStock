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
      1. cost_usd_override (envelope) -> cost_source="envelope"
      2. models_override (Models-table fields) -> cost_source="models_override"
      3. pricing_yaml entry for `model` -> cost_source="yaml"
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
        # Models table provides at least one field - use it (others fall through
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
                pass  # noqa - iterate below
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
