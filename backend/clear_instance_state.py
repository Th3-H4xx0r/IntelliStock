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

from db import store
from db.store import P


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
        # Discovery / trend / outcome state is namespaced under the SCOPED
        # instance id ("main|<config-hash>"), NOT bare "main". Match the exact
        # id (legacy bare rows) OR the "<id>|" prefix (current scoped rows).
        # 2026-05-25 CRITICAL: exact-only matched 0 scoped rows, so a full
        # clear was a silent no-op for these tables.
        ("GraphNexusDiscoveredStocks", [
            ("instance_id", instance_id, "exact"),
            ("instance_id", f"{instance_id}|", "prefix"),
        ]),
        ("GraphNexusMarketTrends", [
            ("instance_id", instance_id, "exact"),
            ("instance_id", f"{instance_id}|", "prefix"),
        ]),
        # Active-event state (scoped instance_id "main|<hash>"). 2026-05-25
        # bug-sweep: these were missing, so a full clear left stale active
        # events that the strategy reloads on the next backtest restart.
        ("GraphNexusActiveEvents", [
            ("instance_id", instance_id, "exact"),
            ("instance_id", f"{instance_id}|", "prefix"),
        ]),
        ("GraphNexusActiveEventHistory", [
            ("instance_id", instance_id, "exact"),
            ("instance_id", f"{instance_id}|", "prefix"),
        ]),
        ("GraphNexusActiveEventMaintenance", [
            ("instance_id", instance_id, "exact"),
            ("instance_id", f"{instance_id}|", "prefix"),
        ]),
        # Per-instance PK tables (id == instance_id or "<id>|<scope>"):
        ("GraphNexusRotationCooldown", [
            ("id", instance_id, "exact"),
            ("id", f"{instance_id}|", "prefix"),
        ]),
        ("GraphNexusTradeOutcomes", [
            ("instance_id", instance_id, "exact"),
            ("instance_id", f"{instance_id}|", "prefix"),
        ]),
        # GraphNexusLearningCache holds the cleanup-gate marker
        # ("cleanup_done|<scoped-id>") and the co-holdings cache
        # ("inst_co_holdings|<scoped-id>|<period>"). Both embed the instance id
        # AFTER a prefix, so "<id>"/"<id>|" miss them — leaving the gate marker
        # alive, which is why the strategy kept logging "already cleaned,
        # config unchanged" after a full clear (2026-05-25 fix).
        ("GraphNexusLearningCache", [
            ("id", instance_id, "exact"),
            ("id", f"{instance_id}|", "prefix"),
            ("id", f"cleanup_done|{instance_id}", "exact"),
            ("id", f"cleanup_done|{instance_id}|", "prefix"),
            ("id", f"inst_co_holdings|{instance_id}|", "prefix"),
        ]),
        ("GraphNexusDiscoverySnapshots", [
            ("id", instance_id, "exact"),
        ]),
        ("GraphNexusOutcomeSeries", [
            ("instance_id", instance_id, "exact"),
            ("instance_id", f"{instance_id}|", "prefix"),
        ]),
        ("GraphNexusAnalystPanel", [
            ("instance_id", instance_id, "exact"),
            ("instance_id", f"{instance_id}|", "prefix"),
        ]),
        # 2026-05-28: LiveBootAudit (one row per broker boot, id pattern
        # "<instance>|<iso-timestamp>"; instance_id field also present).
        ("LiveBootAudit", [
            ("instance_id", instance_id, "exact"),
            ("id", f"{instance_id}|", "prefix"),
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


def _prefix_selection(table: str, field: str, val: str):
    """Prefix scan on ``table.field``.

    The old code used ``between(val, val + "\uffff", right_bound="closed")``,
    the "￿ sorts after every UTF-8 character RethinkDB will see" trick. Under
    ``COLLATE "C"`` the range form and the ``LIKE escaped || '%'`` form select
    exactly the same rows, and ``test_clear_instance_state_prefix.py`` asserts
    that on every target.

    ``id`` keeps the range form because ``id`` is a real column with a bytewise
    B-tree, so the scan stays index-backed; every other field goes through the
    predicate, whose ``_pfx`` (``text_pattern_ops``) index covers the same shape.
    """
    if field == "id":
        return store.between(table, str(val), str(val) + "\uffff",
                             right_bound="closed")
    return store.filter(table, P.field(field).default("").starts_with(str(val)))


def _criterion_predicate(criterion):
    """One criterion as a Predicate, or None when it names nothing we know."""
    field, val, mode = criterion
    if mode == "special":
        if field == "origin_not_backtest":
            return P.field("origin").default("").ne("backtest")
        return None
    if mode == "exact":
        return P.field(field).eq(val)
    if mode == "prefix":
        # `_` `%` and `\` in the prefix are escaped by the store; the old regex
        # form escaped `|` as `[|]`, which LIKE does not need.
        return P.field(field).default("").starts_with(str(val))
    if mode == "contains":
        return P.field(field).default("").match(str(val))
    return None


def _indexed_selection(conn, r, table: str, criteria, combine: str):
    """Build the cheapest correct selection for the given criteria.

    ``conn``/``r`` are accepted and ignored -- the store takes its own pooled
    connection per operation -- so ``execute``'s signature and the CLI script's
    call shape are unchanged.

    Strategy:
      * Single criterion → the indexed primitive directly (``get_all`` for an
        exact match on the primary key, a prefix range on ``id``, otherwise a
        predicate over the generated column's ``_pfx``/B-tree index).
      * Multiple criteria → ONE selection whose WHERE is the criteria OR-ed or
        AND-ed together. This replaces the old two-pass "materialise every
        matching primary key into a Python set, then get_all(*ids)" dance: SQL
        OR does not double-count a row that matches two criteria, so the
        de-duplication that dance existed for is free, and the count and the
        delete are one statement each instead of N+1 round trips.

        The old empty-set branch returned ``get_all("__no_match_sentinel__")``
        so ``.count()``/``.delete()`` still answered cleanly. It is gone: a
        Selection that matches nothing already counts 0 and deletes nothing.

    ``_ensure_index`` is gone too -- ``db/schema.py`` declares every field
    referenced here (see ``prefix_fields``/``indexed_fields``) and
    ``ensure_schema()`` creates them.

    Returns the Selection, or ``None`` if no criteria resolved.
    """
    if not criteria:
        return None

    if len(criteria) == 1:
        return _single_criterion_selection(conn, r, table, criteria[0])

    predicate = None
    for criterion in criteria:
        term = _criterion_predicate(criterion)
        if term is None:
            continue
        if predicate is None:
            predicate = term
        elif combine == "and":
            predicate = predicate & term
        else:
            predicate = predicate | term
    if predicate is None:
        return None
    return store.filter(table, predicate)


def _single_criterion_selection(conn=None, r=None, table: str = "", criterion=None):
    """Build the cheapest selection for a single criterion.

    ``conn``/``r`` are accepted and ignored; they stay leading so the existing
    positional call shape is unchanged.
    """
    field, val, mode = criterion
    if mode == "special":
        # No indexed primitive for special predicates; filter the whole table.
        predicate = _criterion_predicate(criterion)
        return None if predicate is None else store.filter(table, predicate)
    if mode == "exact":
        if field == "id":
            # The primary key: hit the id COLUMN, as get_all(val) did.
            return store.Selection(table).where(
                "id = %s", (store.coerce_id(table, val),))
        return store.filter(table, P.field(field).eq(val))
    if mode == "prefix":
        return _prefix_selection(table, field, val)
    return None


def _build_filter(r, criteria, combine: str = "or"):
    """Combine the criteria into one Predicate. ``r`` is accepted and ignored."""
    expr = None
    for criterion in criteria:
        this = _criterion_predicate(criterion)
        if this is None:
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
    ``apply=True`` performs the deletes and ``deleted`` reflects what the
    store reports.

    ``conn`` is accepted and ignored: the store takes its own pooled
    connection per operation. It stays first and positional so the CLI script
    and the API endpoint call this unchanged.
    """
    r = None

    targets = build_targets(instance_id, scope)
    existing_tables = set(store.table_list())

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

        # Build the cheapest correct selection (indexed where possible,
        # filter() fallback for multi-criterion OR). _indexed_selection
        # also auto-creates secondary indices on the referenced fields
        # so subsequent runs (and the rest of the codebase) benefit.
        selection = _indexed_selection(conn, r, table, criteria, combine_mode)
        if selection is None:
            rows.append({
                "table": table, "would_delete": 0, "deleted": 0,
                "skipped": True, "reason": "no criteria",
            })
            continue

        try:
            count = int(store.count(selection) or 0)
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
                # A Selection is immutable and lazy, so unlike a ReQL cursor
                # it is reusable: the same object counts and then deletes.
                res = store.delete(table, selection)
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
