"""Derive the tunable surface from what strategies already declare.

All 29 strategy modules carry an `INTELLISTOCK_SCHEMA:` header that
`strategies_meta` already parses. Reading it here is what makes the subsystem
strategy-agnostic: RSI's four tunables and graph_nexus_analysis's ~300 come
through one code path, and a strategy written next year is discovered for free
because it declares its own schema.
"""
from __future__ import annotations

from self_learning.types import Lever

# A schema value of "<optional>" marks a credential slot, not a tunable. Tuning
# an API key is not a strategy change; it is an outage.
_PLACEHOLDER = "<optional>"

_SECRETISH = ("api_key", "password", "secret", "token", "_user", "_uri",
              "endpoint", "base_url")


def _value_type(value) -> str:
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "list"
    if isinstance(value, dict):
        return "dict"
    return "null"


def _is_tunable(key: str, value) -> bool:
    if isinstance(value, str) and value.strip() == _PLACEHOLDER:
        return False
    lowered = str(key).lower()
    return not any(marker in lowered for marker in _SECRETISH)


def levers_from_schema(strategy_id: str, schema) -> list:
    """Every lever class a single declared schema exposes."""
    if not isinstance(schema, dict) or not schema:
        return []
    out = []
    for key, value in (schema.get("config") or {}).items():
        if not _is_tunable(key, value):
            continue
        out.append(Lever(strategy_id=strategy_id, kind="config", key=str(key),
                         value_type=_value_type(value), default=value))
    out.append(Lever(strategy_id=strategy_id, kind="weight", key="weight",
                     value_type="number", default=schema.get("weight")))
    out.append(Lever(strategy_id=strategy_id, kind="execution_position",
                     key="execution_position", value_type="number",
                     default=schema.get("execution_position")))
    # Presence in the document's `strategies` list is itself a lever.
    out.append(Lever(strategy_id=strategy_id, kind="membership",
                     key="present", value_type="bool", default=True))
    return out


def lever_surface(strategies: list) -> list:
    """Flatten `strategies_meta.get_available_strategies()` into levers."""
    out = []
    for entry in (strategies or []):
        if not isinstance(entry, dict):
            continue
        out.extend(levers_from_schema(str(entry.get("id") or ""),
                                      entry.get("schema")))
    return out
