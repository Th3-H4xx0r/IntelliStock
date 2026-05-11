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


def get_available_strategies() -> list[dict]:
    """
    Discover strategy .py files in backend/strategies, parse schema and description
    from header comments. Returns list of:
    { "id": module_name, "name": PascalCase name for DB, "schema": {...}, "description": "..." }
    Sorted by id (module name).
    """
    if not os.path.isdir(STRATEGIES_DIR):
        return []
    result = []
    for fn in sorted(os.listdir(STRATEGIES_DIR)):
        if not fn.endswith(".py") or fn == "__init__.py":
            continue
        module_name = fn[:-3]
        if module_name in _EXCLUDED_STRATEGIES:
            continue
        class_name = _module_to_class_name(module_name)
        path = os.path.join(STRATEGIES_DIR, fn)
        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception:
            content = ""
        schema, description = _parse_header_meta(content)
        # Default schema when adding this strategy as a sub-strategy in DB
        if not isinstance(schema, dict):
            schema = {
                "strategy": class_name,
                "weight": 0.5,
                "execution_position": 0,
                "decision_phase": "pre",
                "execution_scope": "per_symbol",
                "conditions": {},
                "config": {},
            }
        else:
            # Ensure required keys; strategy name should match class
            schema.setdefault("strategy", class_name)
            schema.setdefault("weight", 0.5)
            schema.setdefault("execution_position", 0)
            schema.setdefault("decision_phase", "pre")  # "pre" = run before final decision (voting); "post" = run after (order size, pricing, etc.)
            schema.setdefault("execution_scope", "per_symbol")  # "per_symbol" = run per symbol; "run_once" = run once per loop, output scores per ticker
            schema.setdefault("conditions", schema.get("conditions") or {})
            schema.setdefault("config", schema.get("config") or {})
        schema = _merge_schema_config_conditions(schema)
        result.append({
            "id": module_name,
            "name": class_name,
            "schema": schema,
            "description": description or f"Strategy {class_name} (no description in header).",
        })
    return result
