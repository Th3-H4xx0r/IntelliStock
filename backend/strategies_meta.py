"""
Read available strategies from backend/strategies/*.py and parse schema + description
from header comments (INTELLISTOCK_SCHEMA:, INTELLISTOCK_DESCRIPTION:).
Used by GET /strategies/available API.
"""
from __future__ import annotations

import json
import os

# Strategies folder: same parent as this file, then "strategies"
_BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
STRATEGIES_DIR = os.path.join(_BACKEND_DIR, "strategies")

# Header comment markers (read from first N lines of each file)
_SCHEMA_MARKER = "INTELLISTOCK_SCHEMA:"
_DESC_MARKER = "INTELLISTOCK_DESCRIPTION:"
_HEADER_LINES = 80

# Strategy modules to exclude from available list (demo/test only or superseded)
_EXCLUDED_STRATEGIES = {"alwaysbuy", "example", "news"}


def _module_to_class_name(module_name: str) -> str:
    """Convert module name to PascalCase class name (e.g. tiered_risk -> TieredRisk)."""
    return "".join(w.capitalize() for w in module_name.split("_"))


def _parse_header_meta(content: str) -> tuple[dict | None, str]:
    """
    Parse INTELLISTOCK_SCHEMA: and INTELLISTOCK_DESCRIPTION: from comment lines.
    Returns (schema_dict or None, description_str).
    """
    schema = None
    description_lines = []
    in_description = False
    for line in content.splitlines()[:_HEADER_LINES]:
        stripped = line.strip()
        if _SCHEMA_MARKER in stripped:
            idx = stripped.find(_SCHEMA_MARKER)
            rest = stripped[idx + len(_SCHEMA_MARKER) :].strip()
            try:
                schema = json.loads(rest) if rest else None
            except json.JSONDecodeError:
                schema = None
            in_description = False
        elif _DESC_MARKER in stripped:
            idx = stripped.find(_DESC_MARKER)
            rest = stripped[idx + len(_DESC_MARKER) :].strip()
            description_lines = [rest] if rest else []
            in_description = True
        elif in_description and (stripped.startswith("#") or not stripped):
            if stripped.startswith("#"):
                desc_part = stripped.lstrip("#").strip()
                if desc_part:
                    description_lines.append(desc_part)
            if stripped and not stripped.startswith("#"):
                in_description = False
    description = " ".join(description_lines).strip() if description_lines else ""
    return schema, description


def _merge_schema_config_conditions(schema: dict) -> dict:
    """Expose legacy schema conditions as top-level config fields for the UI/API."""
    merged = dict(schema or {})
    raw_config = merged.get("config") if isinstance(merged.get("config"), dict) else {}
    raw_conditions = merged.get("conditions") if isinstance(merged.get("conditions"), dict) else {}
    config = dict(raw_conditions)
    config.update(raw_config)
    merged["config"] = config
    # Keep legacy key for compatibility, but the editor should no longer surface it separately.
    merged["conditions"] = {}
    return merged


def _read_strategy_file(path: str) -> str:
    """Read a strategy file's text; return "" on any error (treated as no header)."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return ""


def _build_strategy_entry(
    module_name: str,
    content: str,
    *,
    default_execution_scope: str,
    require_schema: bool,
    description_fallback,
) -> dict | None:
    """Parse one strategy file's header and build its available-list entry.

    Shared by the flat (equity) loop and the crypto-subpackage loop so header
    parsing, the required-key defaults, and the schema/config merge live in ONE
    place — future header/schema changes apply to both automatically.

    - ``default_execution_scope``: "per_symbol" (equity, run per symbol) or
      "run_once" (crypto, run once per loop and emit per-ticker scores).
    - ``require_schema``: when True a file WITHOUT an ``INTELLISTOCK_SCHEMA``
      header returns ``None`` (used for crypto so helper modules like
      ``core``/``discovery`` are skipped). When False a headerless file falls
      back to a synthesized default schema (equity behavior).
    - ``description_fallback(class_name) -> str``: description used when the
      header has none.

    Returns the entry dict, or ``None`` when the file should be skipped.
    """
    class_name = _module_to_class_name(module_name)
    schema, description = _parse_header_meta(content)
    if not isinstance(schema, dict):
        if require_schema:
            return None  # helper module (no schema header) — not a strategy
        # Default schema when adding this strategy as a sub-strategy in DB
        schema = {
            "strategy": class_name,
            "weight": 0.5,
            "execution_position": 0,
            "decision_phase": "pre",
            "execution_scope": default_execution_scope,
            "conditions": {},
            "config": {},
        }
    else:
        # Ensure required keys; strategy name should match class.
        schema.setdefault("strategy", class_name)
        schema.setdefault("weight", 0.5)
        schema.setdefault("execution_position", 0)
        schema.setdefault("decision_phase", "pre")  # "pre" = run before final decision (voting); "post" = run after (order size, pricing, etc.)
        schema.setdefault("execution_scope", default_execution_scope)  # "per_symbol" = run per symbol; "run_once" = run once per loop, output scores per ticker
        schema.setdefault("conditions", schema.get("conditions") or {})
        schema.setdefault("config", schema.get("config") or {})
    schema = _merge_schema_config_conditions(schema)
    return {
        "id": module_name,
        "name": class_name,
        "schema": schema,
        "description": description or description_fallback(class_name),
    }


def _iter_strategy_entries(dirpath, *, default_execution_scope, require_schema, description_fallback):
    """Yield built entries for every strategy .py file in ``dirpath`` (sorted)."""
    for fn in sorted(os.listdir(dirpath)):
        if not fn.endswith(".py") or fn == "__init__.py":
            continue
        module_name = fn[:-3]
        if module_name in _EXCLUDED_STRATEGIES:
            continue
        content = _read_strategy_file(os.path.join(dirpath, fn))
        entry = _build_strategy_entry(
            module_name,
            content,
            default_execution_scope=default_execution_scope,
            require_schema=require_schema,
            description_fallback=description_fallback,
        )
        if entry is not None:
            yield entry


def get_available_strategies() -> list[dict]:
    """
    Discover strategy .py files in backend/strategies, parse schema and description
    from header comments. Returns list of:
    { "id": module_name, "name": PascalCase name for DB, "schema": {...}, "description": "..." }
    Sorted by id (module name).
    """
    if not os.path.isdir(STRATEGIES_DIR):
        return []
    result = list(_iter_strategy_entries(
        STRATEGIES_DIR,
        default_execution_scope="per_symbol",
        require_schema=False,
        description_fallback=lambda cn: f"Strategy {cn} (no description in header).",
    ))
    # Crypto strategies live in the strategies/crypto/ subpackage (same-codebase,
    # kind="crypto"). Include only files with an explicit INTELLISTOCK_SCHEMA
    # header (require_schema=True) so the core.py/discovery.py helpers are NOT
    # listed as strategies.
    _crypto_dir = os.path.join(STRATEGIES_DIR, "crypto")
    if os.path.isdir(_crypto_dir):
        result.extend(_iter_strategy_entries(
            _crypto_dir,
            default_execution_scope="run_once",
            require_schema=True,
            description_fallback=lambda cn: f"Crypto strategy {cn}.",
        ))
    return result
