"""One-shot: clear the 'main' instance's lookback-resume + outcome state
so the next broker boot re-runs the full 120-day historic lookback cleanly.

What this SCRIPT CLEARS (only for base_instance_id='main'):
- GraphNexusTradeContexts rows: the "processed-date" markers consulted by
  `_historic_lookback_resume_dates`. Deleting these forces the next
  `_run_live_historic_lookback` to process every day in the 120-day window.
- GraphNexusOutcomes rows: the per-day outcome snapshots. Junk outcomes
  from the earlier failed lookback (time_increment="1d" TypeError,
  _unrealized_pct UnboundLocalError) are removed so the new run writes
  clean ones.
- NexusRuntimeState rows: any cached V32 peak / blacklist / momentum
  watchlist state scoped to main — so the main-loop starts from zero.
- LiveState row: the live snapshot for main (will be re-upserted).

What this SCRIPT PRESERVES (shared caches):
- Alpaca article cache (date-keyed, shared across instances)
- Benzinga bulk cache (date-range-keyed, shared)
- FinBERT scoring cache (hash-keyed, content-addressed)
- Company/Macro article LLM classification caches
- Sentiment fingerprint cache
- Overlay bars cache
- Neo4j graph data

Dry run by default. Pass --apply to actually delete.

Usage:
  python scripts/clear_main_instance_lookback_state.py           # dry-run
  python scripts/clear_main_instance_lookback_state.py --apply   # delete
"""

from __future__ import annotations

import os
import sys

INSTANCE_ID = "main"
DB_NAME = "IntelliStock"

# Tables to target with a scope-or-instance filter. Each entry is:
#   (table_name, list_of_filter_criteria_tuples)
# Filter tuple = (field_name, value_prefix_or_exact, match_mode)
# match_mode ∈ {"exact", "prefix", "contains"}
TARGETS = [
    # Resume-date markers — clearing forces 120-day lookback to re-run
    ("GraphNexusTradeContexts", [
        ("instance_id", f"{INSTANCE_ID}|", "prefix"),
        ("base_instance_id", INSTANCE_ID, "exact"),
    ]),
    # Outcome snapshots — clean slate for the rebuilt lookback
    ("GraphNexusOutcomes", [
        ("instance_id", f"{INSTANCE_ID}|", "prefix"),
        ("base_instance_id", INSTANCE_ID, "exact"),
    ]),
    # Runtime state (peak watermarks, blacklists, cooldowns)
    ("NexusRuntimeState", [
        ("id", f"{INSTANCE_ID}:", "prefix"),
    ]),
    # Live snapshot — will be recreated on next broker tick
    ("LiveState", [
        ("id", INSTANCE_ID, "exact"),
    ]),
]


def _build_filter(r, criteria):
    """Build a rethinkdb `filter(...)` expression that matches ANY criterion."""
    expr = None
    for field, val, mode in criteria:
        row_field = r.row[field]
        if mode == "exact":
            this = row_field.eq(val)
        elif mode == "prefix":
            this = row_field.default("").match(f"^{val.replace('|', '[|]')}")
        elif mode == "contains":
            this = row_field.default("").match(val)
        else:
            continue
        expr = this if expr is None else expr | this
    return expr


def main(apply: bool) -> int:
    try:
        from rethinkdb import RethinkDB  # type: ignore
    except ImportError:
        print("ERROR: rethinkdb driver not installed. `pip install rethinkdb`.", file=sys.stderr)
        return 2
    r = RethinkDB()
    host = os.environ.get("RETHINKDB_HOST", "localhost")
    port = int(os.environ.get("RETHINKDB_PORT", "28015"))
    try:
        conn = r.connect(host=host, port=port)
    except Exception as e:
        print(f"ERROR: connect({host}:{port}): {e}", file=sys.stderr)
        return 3

    total_to_delete = 0
    try:
        existing_tables = set(r.db(DB_NAME).table_list().run(conn))
        for table, criteria in TARGETS:
            if table not in existing_tables:
                print(f"SKIP  {table}: table does not exist.")
                continue
            expr = _build_filter(r, criteria)
            if expr is None:
                print(f"SKIP  {table}: no criteria resolved.")
                continue
            count = int(r.db(DB_NAME).table(table).filter(expr).count().run(conn) or 0)
            total_to_delete += count
            verb = "WILL DELETE" if not apply else "DELETING   "
            print(f"{verb} {count:>5} rows from {table}")
            if apply and count > 0:
                res = r.db(DB_NAME).table(table).filter(expr).delete().run(conn)
                deleted = int((res or {}).get("deleted", 0) or 0)
                print(f"           -> deleted={deleted}")
    finally:
        try:
            conn.close()
        except Exception:
            pass

    if not apply:
        print(
            f"\nDRY RUN. {total_to_delete} row(s) would be deleted for instance '{INSTANCE_ID}'. "
            "Re-run with --apply to execute."
        )
    else:
        print(
            f"\nDone. Cleared lookback + outcome + runtime state for '{INSTANCE_ID}'. "
            "Restart the main live broker — the next boot will re-run the 120-day lookback."
        )
    return 0


if __name__ == "__main__":
    apply = "--apply" in sys.argv[1:]
    raise SystemExit(main(apply))
