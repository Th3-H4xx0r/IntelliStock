"""One-shot: wipe all data associated with a single backtest_id so it can be
cleanly re-run after a code-fix.

What it touches (scoped to the chosen --backtest-id):
- LLMUsage: all telemetry rows where backtest_id matches
- BacktestInstances: the queue/runner row (id == bid)
- BacktestResults: the results doc (id == bid)
- NexusStrategyCache: rows for the backtest's scoped runtime instance_id
  (e.g. "main|<scope_id>") -- ALL origins including the backtest snapshot
- Per-instance state tables (14 of them, same set as
  clear_main_instance_lookback_state.py): scoped to the same instance_id

The script first resolves the scoped runtime instance_id by:
  1. Reading the BacktestResults doc and pulling fields like runtime_instance_id
     / scoped_instance_id (whichever is present), OR
  2. Falling back to: SELECT DISTINCT instance_id FROM LLMUsage WHERE
     backtest_id == <bid>. (This catches cases where a partially failed
     backtest never wrote the field to BacktestResults but did emit LLM
     telemetry rows tagged with the scoped instance.)

Dry-run by default. Pass --apply to actually delete.

Usage:
  python scripts/clear_backtest_state.py --backtest-id 531126
  python scripts/clear_backtest_state.py --backtest-id 531126 --apply
  python scripts/clear_backtest_state.py --backtest-id 531126 --apply --skip-llm-usage
"""

from __future__ import annotations

import argparse
import os
import sys

DB_NAME = "IntelliStock"


def _resolve_instance_ids(r, conn, bid: int) -> list[str]:
    """Find every scoped runtime_instance_id ever associated with this backtest.

    We check BacktestResults first (authoritative if present) and union with
    distinct instance_id from LLMUsage (catches partial-run cases).
    Returns a deduped, sorted list. Empty list means no per-instance state to
    clean.
    """
    discovered: set[str] = set()

    try:
        existing_tables = set(r.db(DB_NAME).table_list().run(conn))
    except Exception:
        existing_tables = set()

    if "BacktestResults" in existing_tables:
        try:
            doc = r.db(DB_NAME).table("BacktestResults").get(bid).run(conn)
            if isinstance(doc, dict):
                for key in (
                    "runtime_instance_id",
                    "scoped_instance_id",
                    "instance_id",
                    "broker_instance_id",
                ):
                    val = doc.get(key)
                    if isinstance(val, str) and val.strip():
                        discovered.add(val.strip())
        except Exception as e:
            print(f"WARN: BacktestResults.get({bid}) failed: {e}", file=sys.stderr)

    if "LLMUsage" in existing_tables:
        try:
            cursor = (
                r.db(DB_NAME)
                .table("LLMUsage")
                .filter(
                    (r.row["backtest_id"].default("").coerce_to("string") == str(bid))
                    | (r.row["backtest_id"].default("").coerce_to("string") == str(int(bid)))
                )
                .pluck("instance_id")
                .run(conn)
            )
            for row in cursor:
                val = row.get("instance_id") if isinstance(row, dict) else None
                if isinstance(val, str) and val.strip():
                    discovered.add(val.strip())
        except Exception as e:
            print(f"WARN: LLMUsage distinct instance_id query failed: {e}", file=sys.stderr)

    return sorted(discovered)


def _build_per_instance_targets(instance_id: str):
    """Same 14-table set as clear_main_instance_lookback_state, BUT we wipe
    NexusStrategyCache rows for ALL origins for this backtest's instance_id
    (including the backtest snapshot) -- because the whole backtest is being
    rerun, the snapshot is no longer the source of truth.
    """
    base = instance_id.split("|", 1)[0] if "|" in instance_id else instance_id
    return [
        ("GraphNexusTradeContexts", [
            ("instance_id", instance_id, "exact"),
            ("base_instance_id", base, "exact"),
        ]),
        ("GraphNexusOutcomes", [
            ("instance_id", instance_id, "exact"),
            ("base_instance_id", base, "exact"),
        ]),
        ("NexusRuntimeState", [
            ("id", f"{instance_id}:", "prefix"),
        ]),
        ("LiveState", [
            ("id", instance_id, "exact"),
        ]),
        # ALL origins for this specific scoped instance -- includes backtest snapshot.
        ("NexusStrategyCache", [
            ("instance_id", instance_id, "exact"),
        ]),
        ("GraphNexusDiscoveredStocks", [
            ("instance_id", instance_id, "exact"),
        ]),
        ("GraphNexusMarketTrends", [
            ("instance_id", instance_id, "exact"),
        ]),
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


def _build_filter(r, criteria, combine: str = "or"):
    expr = None
    for field, val, mode in criteria:
        row_field = r.row[field]
        if mode == "exact":
            this = row_field.eq(val)
        elif mode == "prefix":
            this = row_field.default("").match(f"^{val.replace('|', '[|]')}")
        else:
            continue
        if expr is None:
            expr = this
        elif combine == "and":
            expr = expr & this
        else:
            expr = expr | this
    return expr


def _delete_with_count(r, conn, table: str, expr, apply: bool) -> tuple[int, int]:
    """Returns (matched_count, deleted_count). deleted=0 in dry-run."""
    count = int(r.db(DB_NAME).table(table).filter(expr).count().run(conn) or 0)
    if not apply or count == 0:
        return count, 0
    res = r.db(DB_NAME).table(table).filter(expr).delete().run(conn)
    deleted = int((res or {}).get("deleted", 0) or 0)
    return count, deleted


def main(apply: bool, bid: int, skip_llm_usage: bool, skip_results: bool) -> int:
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

    summary: list[tuple[str, int, int, str]] = []  # (table_or_section, matched, deleted, note)

    try:
        existing = set(r.db(DB_NAME).table_list().run(conn))

        # 1. Resolve instance_ids.
        instance_ids = _resolve_instance_ids(r, conn, bid)
        print(f"\n--- Backtest {bid}: resolved instance_id(s) ---")
        if instance_ids:
            for iid in instance_ids:
                print(f"  {iid}")
        else:
            print("  (none found in BacktestResults or LLMUsage)")
        print()

        # 2. LLMUsage telemetry rows.
        if skip_llm_usage:
            print("SKIP  LLMUsage (--skip-llm-usage)")
            summary.append(("LLMUsage", 0, 0, "skipped"))
        elif "LLMUsage" not in existing:
            print("SKIP  LLMUsage: table does not exist.")
            summary.append(("LLMUsage", 0, 0, "table_missing"))
        else:
            expr = (
                (r.row["backtest_id"].default("").coerce_to("string") == str(bid))
                | (r.row["backtest_id"].default("").coerce_to("string") == str(int(bid)))
            )
            matched, deleted = _delete_with_count(r, conn, "LLMUsage", expr, apply)
            verb = "WILL DELETE" if not apply else "DELETING   "
            print(f"{verb} {matched:>6} rows from LLMUsage (backtest_id={bid})")
            if apply and matched:
                print(f"           -> deleted={deleted}")
            summary.append(("LLMUsage", matched, deleted, "deleted" if apply else "would_delete"))

        # 3. Per-instance state for each resolved instance.
        # SAFETY: per-instance filters (e.g. instance_id="main", or prefix
        # "main|") would match LIVE data and other backtests if the resolved
        # ID is the BARE base ("main") rather than the scoped form
        # ("main|<scope_id>"). This guards against catastrophic cross-run
        # deletion when an older backtest pre-dates the scoping feature
        # (CRITICAL safety bug from the 2026-05-22 bt531126 wipe attempt
        # which would have deleted 15164 GraphNexusTradeContexts rows
        # belonging to live + other backtests).
        for iid in instance_ids:
            if "|" not in iid:
                print(
                    f"\n--- SKIPPING per-instance wipes for {iid!r} ---\n"
                    f"  Reason: bare base instance_id (no '|<scope_id>' suffix).\n"
                    f"  Wiping per-instance tables by '{iid}' would also delete\n"
                    f"  LIVE state and other backtests' state. This backtest's\n"
                    f"  per-instance writes (if any) are commingled with that\n"
                    f"  shared bucket and must be left in place. Wipe LLMUsage\n"
                    f"  + BacktestResults only."
                )
                summary.append((f"per_instance[{iid}]", 0, 0, "skipped_unscoped"))
                continue
            print(f"\n--- Per-instance state for {iid} ---")
            for table, criteria in _build_per_instance_targets(iid):
                if table not in existing:
                    print(f"SKIP  {table}: table does not exist.")
                    summary.append((f"{table}[{iid}]", 0, 0, "table_missing"))
                    continue
                expr = _build_filter(r, criteria, combine="or")
                if expr is None:
                    summary.append((f"{table}[{iid}]", 0, 0, "no_criteria"))
                    continue
                matched, deleted = _delete_with_count(r, conn, table, expr, apply)
                verb = "WILL DELETE" if not apply else "DELETING   "
                print(f"{verb} {matched:>5} rows from {table}")
                if apply and matched:
                    print(f"           -> deleted={deleted}")
                summary.append((f"{table}[{iid}]", matched, deleted, "deleted" if apply else "would_delete"))

        # 4. BacktestInstances + BacktestResults.
        if skip_results:
            print("\nSKIP  BacktestInstances + BacktestResults (--skip-results)")
            summary.append(("BacktestInstances", 0, 0, "skipped"))
            summary.append(("BacktestResults", 0, 0, "skipped"))
        else:
            print()
            for table in ("BacktestInstances", "BacktestResults"):
                if table not in existing:
                    print(f"SKIP  {table}: table does not exist.")
                    summary.append((table, 0, 0, "table_missing"))
                    continue
                doc = r.db(DB_NAME).table(table).get(bid).run(conn)
                if doc is None:
                    print(f"SKIP  {table}: no doc with id={bid}.")
                    summary.append((table, 0, 0, "not_found"))
                    continue
                if apply:
                    r.db(DB_NAME).table(table).get(bid).delete().run(conn)
                    print(f"DELETED  {table}/{bid}")
                    summary.append((table, 1, 1, "deleted"))
                else:
                    print(f"WILL DELETE  {table}/{bid}")
                    summary.append((table, 1, 0, "would_delete"))
    finally:
        try:
            conn.close()
        except Exception:
            pass

    total_matched = sum(m for _, m, _, _ in summary)
    total_deleted = sum(d for _, _, d, _ in summary)
    print(f"\n--- Summary (backtest_id={bid}) ---")
    for table, matched, deleted, status in summary:
        print(f"  {table:60s} matched={matched:>6} deleted={deleted:>6} ({status})")
    print(f"  {'TOTAL':60s} matched={total_matched:>6} deleted={total_deleted:>6}")

    if not apply:
        print(
            f"\nDRY RUN. {total_matched} row(s) would be deleted. "
            "Re-run with --apply to execute."
        )
    else:
        print(f"\nDone. Backtest {bid} state cleared.")
    return 0


def _parse_args():
    p = argparse.ArgumentParser(description="Wipe all state for a single backtest_id.")
    p.add_argument("--backtest-id", type=int, required=True, help="backtest id to wipe")
    p.add_argument("--apply", action="store_true", help="actually delete (default: dry-run)")
    p.add_argument("--skip-llm-usage", action="store_true",
                   help="preserve LLMUsage telemetry rows (e.g. for post-mortem)")
    p.add_argument("--skip-results", action="store_true",
                   help="preserve BacktestInstances + BacktestResults rows")
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    raise SystemExit(main(args.apply, args.backtest_id, args.skip_llm_usage, args.skip_results))
