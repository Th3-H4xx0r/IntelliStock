"""Eagerly create the secondary indices that ``clear_instance_state``
will lazily create on first use. Running this once eliminates the
"first preview is slow because indices are being built" UX.

Fields covered (taken verbatim from ``build_targets`` in
``backend/clear_instance_state.py``):

  GraphNexusTradeContexts   →  instance_id, base_instance_id
  GraphNexusOutcomes        →  instance_id, base_instance_id
  NexusStrategyCache        →  instance_id
  GraphNexusDiscoveredStocks→  instance_id
  GraphNexusMarketTrends    →  instance_id
  GraphNexusTradeOutcomes   →  instance_id
  GraphNexusOutcomeSeries   →  instance_id
  GraphNexusAnalystPanel    →  instance_id

PK ("id") tables (NexusRuntimeState, LiveState, GraphNexusRotationCooldown,
GraphNexusLearningCache, GraphNexusDiscoverySnapshots) need NO additional
indices — RethinkDB always indexes the primary key.

Idempotent: existing indices are skipped, missing tables are skipped
with a notice. Re-runs are safe and cheap (a few index_list calls per
table).

Dry-run by default — pass ``--apply`` to actually create the indices.

Usage:
  python scripts/create_clear_state_indices.py                  # dry-run
  python scripts/create_clear_state_indices.py --apply          # create
  python scripts/create_clear_state_indices.py --apply --host rethinkdb --port 28015
"""

from __future__ import annotations

import argparse
import os
import sys
import time


DB_NAME = "IntelliStock"


# (table_name, fields_to_index) — kept in sync with
# backend/clear_instance_state.py's build_targets. When adding to
# either list, update the other.
INDEX_TARGETS: list[tuple[str, tuple[str, ...]]] = [
    ("GraphNexusTradeContexts",    ("instance_id", "base_instance_id")),
    ("GraphNexusOutcomes",         ("instance_id", "base_instance_id")),
    ("NexusStrategyCache",         ("instance_id",)),
    ("GraphNexusDiscoveredStocks", ("instance_id",)),
    ("GraphNexusMarketTrends",     ("instance_id",)),
    ("GraphNexusTradeOutcomes",    ("instance_id",)),
    ("GraphNexusOutcomeSeries",    ("instance_id",)),
    ("GraphNexusAnalystPanel",     ("instance_id",)),
]


def main(apply: bool, host: str, port: int) -> int:
    try:
        from rethinkdb import RethinkDB
    except ImportError:
        print("ERROR: rethinkdb driver not installed. `pip install rethinkdb`.",
              file=sys.stderr)
        return 2
    r = RethinkDB()
    try:
        conn = r.connect(host=host, port=port)
    except Exception as e:
        print(f"ERROR: connect({host}:{port}): {e}", file=sys.stderr)
        return 3

    try:
        try:
            existing_tables = set(r.db(DB_NAME).table_list().run(conn))
        except Exception as e:
            print(f"ERROR: list tables in db={DB_NAME!r}: {e}", file=sys.stderr)
            return 4

        total_created = 0
        total_skipped = 0
        total_missing_tables = 0
        verb = "WOULD CREATE" if not apply else "CREATING    "

        for table, fields in INDEX_TARGETS:
            if table not in existing_tables:
                print(f"SKIP {table}: table does not exist")
                total_missing_tables += 1
                continue
            try:
                existing_idx = set(
                    r.db(DB_NAME).table(table).index_list().run(conn)
                )
            except Exception as e:
                print(f"WARN {table}: index_list failed: {e}")
                continue
            for field in fields:
                if field in existing_idx:
                    print(f"SKIP  {table}.{field}: index already exists")
                    total_skipped += 1
                    continue
                print(f"{verb} {table}.{field}")
                if apply:
                    t0 = time.time()
                    try:
                        r.db(DB_NAME).table(table).index_create(field).run(conn)
                        r.db(DB_NAME).table(table).index_wait(field).run(conn)
                        elapsed = time.time() - t0
                        print(f"           -> created in {elapsed:.2f}s")
                        total_created += 1
                    except Exception as e:
                        print(f"           -> FAILED: {e}")

        print()
        print(f"Summary: tables_missing={total_missing_tables} "
              f"indices_already_present={total_skipped} "
              f"indices_{'created' if apply else 'to_create'}="
              f"{total_created if apply else 'see WOULD CREATE lines above'}")
        if not apply:
            print()
            print("Dry-run only. Re-run with --apply to create the indices.")
        return 0
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _parse_args():
    p = argparse.ArgumentParser(
        description=(
            "Eagerly create the secondary indices used by "
            "backend/clear_instance_state.py."
        ),
    )
    p.add_argument("--apply", action="store_true",
                   help="actually create the indices (default: dry-run)")
    p.add_argument("--host",
                   default=os.environ.get("RETHINKDB_HOST", "localhost"),
                   help="RethinkDB host (default: $RETHINKDB_HOST or localhost)")
    p.add_argument("--port", type=int,
                   default=int(os.environ.get("RETHINKDB_PORT", "28015")),
                   help="RethinkDB port (default: $RETHINKDB_PORT or 28015)")
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    sys.exit(main(apply=args.apply, host=args.host, port=args.port))
