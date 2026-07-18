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

TABLES = ("AlphaEvents", "AlphaState")
INDEXES = {
    "AlphaEvents": ("kind",),
    "LiveOrderWAL": ("instance_id", "client_order_id"),
}


def plan_migration(existing_tables, existing_indexes):
    """Pure migration planner: returns the ordered list of actions needed."""
    actions = []
    for table in TABLES:
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
        from rethinkdb import RethinkDB
        self._r = RethinkDB()
        self._conn = self._r.connect(
            host=host or os.environ.get("RETHINKDB_HOST", "localhost"),
            port=int(port or os.environ.get("RETHINKDB_PORT", "28015") or 28015),
            timeout=20,
        )

    def list_tables(self):
        return set(self._r.db(DB_NAME).table_list().run(self._conn))

    def list_indexes(self, table):
        try:
            return set(self._r.db(DB_NAME).table(table).index_list().run(self._conn))
        except Exception:
            return set()

    def create_table(self, table):
        self._r.db(DB_NAME).table_create(table).run(self._conn)

    def create_index(self, table, index):
        self._r.db(DB_NAME).table(table).index_create(index).run(self._conn)
        self._r.db(DB_NAME).table(table).index_wait(index).run(self._conn)


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
