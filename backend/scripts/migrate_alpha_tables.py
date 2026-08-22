"""Dry-run-first migration for benchmark-alpha RethinkDB tables and indexes.

Creates ``AlphaEvents`` and ``AlphaState`` plus the ``LiveOrderWAL``
secondary indexes (``instance_id``, ``client_order_id``) needed for
instance-scoped and client-order-prefix queries. Prints table/index names and
status only — never row payloads.

Usage (repo root, .env supplies RETHINKDB_HOST):
    python3 -m scripts.migrate_alpha_tables --dry-run   # default
    python3 -m scripts.migrate_alpha_tables --apply
"""
import argparse
import os
import sys

_BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_ROOT not in sys.path:
    sys.path.insert(0, _BACKEND_ROOT)

DB_NAME = "IntelliStock"

# Task 7 record tables share the compound query shapes run_asof and
# instance_origin_asof; the production backend maps those names to their
# compound fields in create_index.
RECORD_TABLES = (
    "AlphaPredictions", "AlphaGates", "AlphaAllocations", "AlphaOrderIntents",
    "AlphaBrokerOrders", "AlphaFills", "AlphaPortfolioSnapshots",
    "AlphaCashActivities", "AlphaOutcomes", "AlphaIncidents",
    "AlphaExperiments", "AlphaPromotions",
)
TABLES = ("AlphaEvents", "AlphaState") + RECORD_TABLES
COMPOUND_INDEX_FIELDS = {
    "run_asof": ("run_id", "as_of"),
    "instance_origin_asof": ("instance_id", "origin", "as_of"),
}
INDEXES = {
    "AlphaEvents": ("kind",),
    "LiveOrderWAL": ("instance_id", "client_order_id"),
    **{table: ("run_asof", "instance_origin_asof") for table in RECORD_TABLES},
}

# Task 8 retention policy (days; None = retain indefinitely unless policy
# changes). Enforcement runs as a scheduled operator job that consumes this
# mapping; this script only declares it.
RETENTION_DAYS = {
    "AlphaPredictions": 400,           # raw candidate predictions
    "AlphaEvents:mark_health": 30,     # high-frequency mark-health events
    "AlphaAllocations": None,
    "AlphaOrderIntents": None,
    "AlphaBrokerOrders": None,
    "AlphaFills": None,
    "AlphaPromotions": None,
}


def plan_migration(existing_tables, existing_indexes):
    """Pure migration planner: returns the ordered list of actions needed."""
    actions = []
    # Every index target must exist first (audit 2026-07-18: LiveOrderWAL was
    # indexed but never created, crashing --apply on a fresh database).
    for table in TABLES + tuple(t for t in INDEXES if t not in TABLES):
        if table not in existing_tables:
            actions.append({"action": "create_table", "table": table})
    for table, indexes in INDEXES.items():
        present = set(existing_indexes.get(table, set()))
        for index in indexes:
            if index not in present:
                actions.append({"action": "create_index", "table": table,
                                "index": index})
    return actions


class RethinkMigrationBackend:
    def __init__(self, host=None, port=None):
        try:
            from dotenv import load_dotenv
            load_dotenv(os.path.join(os.path.dirname(_BACKEND_ROOT), ".env"))
        except Exception:
            pass
        from db import store as _store
        from db import schema as _schema
        self._r = _store
        self._schema = _schema
        self._conn = None

    def list_tables(self):
        return set(self._r.table_list())

    def list_indexes(self, table):
        try:
            return set(self._r.index_list(table))
        except Exception:
            return set()

    def create_table(self, table):
        # R25: db.schema owns the DDL, indexes included, and is idempotent.
        self._schema.ensure_schema(tables=[table])

    def create_index(self, table, index):
        # R25: the index set is declared in db.schema, and CREATE INDEX is
        # synchronous, so there is no index_wait to do.
        self._schema.ensure_schema(tables=[table])


def retention_plan(now):
    """Executable form of RETENTION_DAYS: [(table, cutoff_iso, filter_kind)].

    ``filter_kind`` is "as_of" for record tables and "created_at:mark_health"
    for the AlphaEvents mark-health subset. Tables with ``None`` retention are
    excluded — they are retained indefinitely unless policy changes."""
    from datetime import timedelta
    plan = []
    for key, days in RETENTION_DAYS.items():
        if days is None:
            continue
        cutoff = (now - timedelta(days=int(days))).isoformat()
        if ":" in key:
            table, subset = key.split(":", 1)
            plan.append((table, cutoff, f"created_at:{subset}"))
        else:
            plan.append((key, cutoff, "as_of"))
    return plan


def main(argv=None, backend=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--dry-run", action="store_true", default=True)
    group.add_argument("--apply", action="store_true")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", default=None)
    args = parser.parse_args(argv)

    if backend is None:
        backend = RethinkMigrationBackend(host=args.host, port=args.port)

    existing_tables = set(backend.list_tables())
    known = existing_tables | set(INDEXES.keys())
    existing_indexes = {t: set(backend.list_indexes(t))
                        for t in known if t in existing_tables}
    actions = plan_migration(existing_tables, existing_indexes)

    mode = "APPLY" if args.apply else "DRY-RUN"
    if not actions:
        print(f"[{mode}] nothing to do — all tables and indexes present")
        return 0
    for action in actions:
        label = f"table {action['table']}" if action["action"] == "create_table" \
            else f"index {action['table']}.{action['index']}"
        if args.apply:
            if action["action"] == "create_table":
                backend.create_table(action["table"])
            else:
                backend.create_index(action["table"], action["index"])
            print(f"[{mode}] created {label}")
        else:
            print(f"[{mode}] would create {label}")
    print(f"[{mode}] {len(actions)} action(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
