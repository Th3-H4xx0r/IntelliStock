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


def _ensure_index(conn, r, table: str, field: str) -> bool:
    """Create a secondary index on ``table.field`` if missing.

    Returns True when the index exists (created or already there),
    False when creation failed. Idempotent across processes — the
    create call will race-fail safely.

    PK fields ("id") are always indexed by RethinkDB itself; we
    short-circuit those.
    """
    if field == "id":
        return True
    try:
        existing = set(
            r.db(DB_NAME).table(table).index_list().run(conn)
        )
    except Exception:
        return False
    if field in existing:
        return True
    try:
        r.db(DB_NAME).table(table).index_create(field).run(conn)
        r.db(DB_NAME).table(table).index_wait(field).run(conn)
        return True
    except Exception:
        # Race with another process creating the same index is fine —
        # if the second create raises, the first either already
        # finished or is still pending. The index_wait above would have
        # waited; if not, the subsequent count() may briefly fall back
        # to scan, which is correct, just slower.
        try:
            existing_after = set(
                r.db(DB_NAME).table(table).index_list().run(conn)
            )
            return field in existing_after
        except Exception:
            return False


def _indexed_selection(conn, r, table: str, criteria, combine: str):
    """Build the cheapest correct selection for the given criteria.

    Strategy:
      * Single criterion, exact match on a field → ``get_all`` with an
        index (auto-created if missing). PK ``id`` uses the primary
        index for free.
      * Single criterion, prefix match → ``between(prefix, prefix+max)``
        with an index. PK ``id`` uses the primary index for free.
      * combine="and" with N criteria → use the first criterion as the
        indexed base (per the rules above), then chain ``.filter()``
        with the remaining criteria. Index narrows the scan to the
        instance's rows first; the chained filter then prunes the
        origin field (or whatever else).
      * combine="or" with N criteria → fall back to ``.filter()`` with
        an OR expression. RethinkDB has no clean indexed-union path
        without materialising IDs in Python; the OR cases in our
        targets (instance_id-prefix OR base_instance_id-exact) span
        two fields and ``.filter()`` is still markedly faster than the
        ID-materialisation alternative for the row counts we see in
        production. We do still ``_ensure_index`` on each field so the
        next single-criterion query benefits.

    Returns the selection (anything you can call ``.count()`` /
    ``.delete()`` on), or ``None`` if no criteria resolved.
    """
    if not criteria:
        return None

    # Pre-create indices for every field referenced — costs nothing
    # when they already exist, and the OR-fallback path leaves indices
    # ready for future single-criterion queries.
    for field, _val, mode in criteria:
        if mode == "special":
            continue
        _ensure_index(conn, r, table, field)

    # Multi-criterion OR. We want to avoid the table scan that the
    # plain ``.filter()`` does, but RethinkDB's .union() doesn't
    # deduplicate, so a row that matches both criteria is counted twice.
    # Strategy: if EVERY criterion is indexable (exact / prefix on a
    # field where _single_criterion_selection succeeds), materialise
    # the matching primary keys from each branch into a Python set,
    # then expose the de-duped set via .get_all(*ids). This streams
    # through O(matching_rows) instead of O(table_rows), and the
    # count + delete operate on the same de-duped set.
    if combine == "or" and len(criteria) > 1:
        sub_selections = []
        all_indexable = True
        for c in criteria:
            sel = _single_criterion_selection(conn, r, table, c)
            if sel is None or c[2] == "special":
                all_indexable = False
                break
            sub_selections.append(sel)
        if all_indexable:
            try:
                ids: set = set()
                for sel in sub_selections:
                    for doc in sel.pluck("id").run(conn):
                        rid = doc.get("id") if isinstance(doc, dict) else None
                        if rid is not None:
                            ids.add(rid)
                if not ids:
                    # Return an empty selection that still answers
                    # .count() (== 0) and .delete() (no-op) cleanly.
                    return r.db(DB_NAME).table(table).get_all(
                        "__no_match_sentinel__"
                    )
                # ``get_all`` accepts variadic args; the spread keeps
                # the PK-set lookup as a single index traversal.
                return r.db(DB_NAME).table(table).get_all(*ids)
            except Exception:
                # Any failure (e.g. cursor drained twice, network) falls
                # back to the table scan — correctness wins over speed.
                pass
        expr = _build_filter(r, criteria, combine="or")
        if expr is None:
            return None
        return r.db(DB_NAME).table(table).filter(expr)

    # Single criterion — use the indexed primitive directly.
    if len(criteria) == 1:
        return _single_criterion_selection(
            conn, r, table, criteria[0]
        )

    # combine == "and" with multiple criteria: pick the first
    # non-special criterion as the indexed base, then filter the rest.
    base_idx = next(
        (i for i, c in enumerate(criteria) if c[2] != "special"),
        None,
    )
    if base_idx is None:
        # All criteria are "special" (only origin_not_backtest today).
        # Filter the whole table.
        expr = _build_filter(r, criteria, combine=combine)
        if expr is None:
            return None
        return r.db(DB_NAME).table(table).filter(expr)

    base_selection = _single_criterion_selection(
        conn, r, table, criteria[base_idx]
    )
    remaining = [c for i, c in enumerate(criteria) if i != base_idx]
    if not remaining:
        return base_selection
    expr = _build_filter(r, remaining, combine="and")
    if expr is None:
        return base_selection
    return base_selection.filter(expr)


def _single_criterion_selection(conn, r, table: str, criterion):
    """Build the cheapest selection for a single criterion."""
    field, val, mode = criterion
    tbl = r.db(DB_NAME).table(table)
    if mode == "special":
        # No "get_all"-style indexed primitive for special predicates;
        # filter against the whole table.
        if field == "origin_not_backtest":
            return tbl.filter(lambda row: row["origin"].default("") != "backtest")
        return None
    if mode == "exact":
        if field == "id":
            return tbl.get_all(val)
        return tbl.get_all(val, index=field)
    if mode == "prefix":
        # between() with a high sentinel above any printable codepoint —
        # ￿ sorts after every UTF-8 character RethinkDB will see
        # in our instance_id strings.
        high = str(val) + "￿"
        if field == "id":
            return tbl.between(val, high, right_bound="closed")
        return tbl.between(val, high, index=field, right_bound="closed")
    return None


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
            count = int(selection.count().run(conn) or 0)
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
                # Rebuild the selection — RethinkDB's count() consumed
                # the cursor on the original. Cheap: indices already
                # primed, no second index_create round-trip.
                delete_selection = _indexed_selection(
                    conn, r, table, criteria, combine_mode,
                )
                res = delete_selection.delete().run(conn)
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
