"""Reusable instance-state clearing.

Used by:
  * ``scripts/clear_main_instance_lookback_state.py`` — CLI one-shot for ops
  * ``backend/interactive_utils.action_clear_instance_state`` — API endpoint
    that powers the per-instance "Clear data" UI button.

Two scopes:

  ``lookback_only``
    Wipes ONLY the lookback resume-state tables — the per-day markers
    GraphNexusAnalysis writes during historic lookback. Re-running a
    backtest after this forces the strategy to redo its lookback work
    from scratch, but per-instance trade state, discovery state, market
    trends, and outcome history are preserved.

  ``strategy_cache_only``
    Wipes ONLY ``NexusStrategyCache`` rows scoped to this instance with
    a non-backtest origin (i.e. the DB-persisted "strategy cache" that
    the broker reads at boot to restore live-mode runtime state).
    Backtest snapshots are PRESERVED. Use this when you want to start
    the next live boot with a fresh strategy cache without redoing
    lookback work.

  ``full_instance``
    Mirrors the CLI script — drops every per-instance table this module
    knows about, INCLUDING NexusStrategyCache (non-backtest rows only).
    Shared caches (article/sentiment/finbert/macro/company
    classification caches, Benzinga bulk cache, Neo4j data, and
    NexusStrategyCache rows with ``origin="backtest"``) are preserved.

Both scopes are scoped per-instance: a wipe on ``instance_id="main"``
never touches another instance's rows.

Both scopes support dry-run (``apply=False``, the default). Dry-run
returns the same shape the API endpoint will use — the operator can
review per-table counts before committing.
"""

from __future__ import annotations

from typing import Any


DB_NAME = "IntelliStock"


# Tables that hold per-instance lookback resume state. These are the
# tables the strategy reads to decide whether a lookback day has
# already been processed.
LOOKBACK_RESUME_TABLES = ("GraphNexusTradeContexts", "GraphNexusOutcomes")


def _lookback_only_targets(instance_id: str):
    """Resume markers only — narrowest possible wipe."""
    return [
        ("GraphNexusTradeContexts", [
            ("instance_id", f"{instance_id}|", "prefix"),
            ("base_instance_id", instance_id, "exact"),
        ]),
        ("GraphNexusOutcomes", [
            ("instance_id", f"{instance_id}|", "prefix"),
            ("base_instance_id", instance_id, "exact"),
        ]),
    ]


def _full_instance_targets(instance_id: str):
    """Every per-instance table the original script clears.

    Mirrors ``scripts/clear_main_instance_lookback_state.py:_build_targets``
    one-for-one. Do not edit one without the other — both must match.
    """
    return [
        # Lookback resume state (also in lookback_only):
        ("GraphNexusTradeContexts", [
            ("instance_id", f"{instance_id}|", "prefix"),
            ("base_instance_id", instance_id, "exact"),
        ]),
        ("GraphNexusOutcomes", [
            ("instance_id", f"{instance_id}|", "prefix"),
            ("base_instance_id", instance_id, "exact"),
        ]),
        # V32 runtime state:
        ("NexusRuntimeState", [
            ("id", f"{instance_id}:", "prefix"),
        ]),
        # Live snapshot:
        ("LiveState", [
            ("id", instance_id, "exact"),
        ]),
        # NexusStrategyCache: per-instance live-origin (and legacy) rows
        # only. AND-combine the instance filter with the origin filter
        # so we never touch another instance's rows AND never delete
        # backtest snapshots.
        ("NexusStrategyCache", [
            ("instance_id", instance_id, "exact"),
            ("origin_not_backtest", None, "special"),
        ], "and"),
        # Discovery + market trend state:
        ("GraphNexusDiscoveredStocks", [
            ("instance_id", instance_id, "exact"),
        ]),
        ("GraphNexusMarketTrends", [
            ("instance_id", instance_id, "exact"),
        ]),
        # Per-instance PK tables (id == instance_id):
        ("GraphNexusRotationCooldown", [
            ("id", instance_id, "exact"),
        ]),
        ("GraphNexusTradeOutcomes", [
            ("instance_id", instance_id, "exact"),
        ]),
        ("GraphNexusLearningCache", [
            ("id", instance_id, "exact"),
            ("id", f"{instance_id}|", "prefix"),
        ]),
        ("GraphNexusDiscoverySnapshots", [
            ("id", instance_id, "exact"),
        ]),
        ("GraphNexusOutcomeSeries", [
            ("instance_id", instance_id, "exact"),
        ]),
        ("GraphNexusAnalystPanel", [
            ("instance_id", instance_id, "exact"),
        ]),
    ]


def _strategy_cache_only_targets(instance_id: str):
    """NexusStrategyCache (the DB-backed strategy cache) only.

    AND-combine instance filter with the origin filter so we never
    touch another instance's rows AND never delete the backtest-origin
    snapshots that live-boot recovery relies on.
    """
    return [
        ("NexusStrategyCache", [
            ("instance_id", instance_id, "exact"),
            ("origin_not_backtest", None, "special"),
        ], "and"),
    ]


def build_targets(instance_id: str, scope: str = "lookback_only"):
    """Return the list of table targets for the given scope.

    Each target is ``(table_name, criteria)`` or
    ``(table_name, criteria, combine_mode)``. ``combine_mode`` defaults
    to ``"or"`` for back-compat with the original script.

    Raises ValueError on unknown scope so the caller can return 400
    rather than silently wiping the wrong thing.
    """
    instance_id = (instance_id or "").strip()
    if not instance_id:
        raise ValueError("instance_id is required")
    if scope == "lookback_only":
        return _lookback_only_targets(instance_id)
    if scope == "strategy_cache_only":
        return _strategy_cache_only_targets(instance_id)
    if scope == "full_instance":
        return _full_instance_targets(instance_id)
    raise ValueError(f"unknown scope: {scope!r}")


def _build_filter(r, criteria, combine: str = "or"):
    """Build a RethinkDB filter expression combining the criteria."""
    expr = None
    for field, val, mode in criteria:
        if mode == "special":
            if field == "origin_not_backtest":
                this = r.row["origin"].default("") != "backtest"
            else:
                continue
        else:
            row_field = r.row[field]
            if mode == "exact":
                this = row_field.eq(val)
            elif mode == "prefix":
                this = row_field.default("").match(
                    f"^{str(val).replace('|', '[|]')}"
                )
            elif mode == "contains":
                this = row_field.default("").match(str(val))
            else:
                continue
        if expr is None:
            expr = this
        elif combine == "and":
            expr = expr & this
        else:
            expr = expr | this
    return expr


def execute(conn, *, instance_id: str, scope: str, apply: bool) -> dict[str, Any]:
    """Count (and optionally delete) rows per target table.

    Returns a dict shaped:
      {
        "instance_id": "main",
        "scope": "lookback_only",
        "apply": False,
        "tables": [
          {"table": "GraphNexusTradeContexts", "would_delete": 85,
           "deleted": 0, "skipped": False, "reason": None},
          ...
        ],
        "total_would_delete": 85,
        "total_deleted": 0,
      }

    ``apply=False`` is a dry run — ``deleted`` is 0 on every row.
    ``apply=True`` performs the deletes and ``deleted`` reflects what
    RethinkDB reports.
    """
    try:
        from rethinkdb import RethinkDB
    except ImportError as e:
        raise RuntimeError("rethinkdb driver not installed") from e
    r = RethinkDB()

    targets = build_targets(instance_id, scope)
    existing_tables = set(r.db(DB_NAME).table_list().run(conn))

    rows: list[dict[str, Any]] = []
    total_would_delete = 0
    total_deleted = 0

    for entry in targets:
        if len(entry) == 3:
            table, criteria, combine_mode = entry
        else:
            table, criteria = entry
            combine_mode = "or"

        if table not in existing_tables:
            rows.append({
                "table": table, "would_delete": 0, "deleted": 0,
                "skipped": True, "reason": "table does not exist",
            })
            continue

        expr = _build_filter(r, criteria, combine=combine_mode)
        if expr is None:
            rows.append({
                "table": table, "would_delete": 0, "deleted": 0,
                "skipped": True, "reason": "no criteria",
            })
            continue

        try:
            count = int(
                r.db(DB_NAME).table(table).filter(expr).count().run(conn) or 0
            )
        except Exception as e:
            rows.append({
                "table": table, "would_delete": 0, "deleted": 0,
                "skipped": True, "reason": f"count failed: {e}",
            })
            continue

        total_would_delete += count
        deleted = 0
        if apply and count > 0:
            try:
                res = r.db(DB_NAME).table(table).filter(expr).delete().run(conn)
                deleted = int((res or {}).get("deleted", 0) or 0)
                total_deleted += deleted
            except Exception as e:
                rows.append({
                    "table": table, "would_delete": count, "deleted": 0,
                    "skipped": True, "reason": f"delete failed: {e}",
                })
                continue

        rows.append({
            "table": table, "would_delete": count, "deleted": deleted,
            "skipped": False, "reason": None,
        })

    return {
        "instance_id": instance_id,
        "scope": scope,
        "apply": bool(apply),
        "tables": rows,
        "total_would_delete": total_would_delete,
        "total_deleted": total_deleted,
    }
