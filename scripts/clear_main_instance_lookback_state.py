"""One-shot: clear per-instance lookback + decision + cache state so the next
broker boot starts cleanly. Phase 1 extension covers the per-instance tables
that could leak old-strategy state into a new strategy on the same
instance_id.

What this SCRIPT CLEARS (only for the chosen --instance, default 'main'):
- GraphNexusTradeContexts: lookback resume-date markers
- GraphNexusOutcomes: per-day outcome snapshots
- NexusRuntimeState: cached V32 peak / blacklist / momentum watchlist
- LiveState: live snapshot for the instance
- NexusStrategyCache: LIVE-origin rows only (backtest-origin SNAPSHOTS preserved)
- GraphNexusDiscoveredStocks: discovered-stock state (active/sold flags)
- GraphNexusMarketTrends: market trend state machine
- GraphNexusRotationCooldown: rotation cooldown timer
- GraphNexusTradeOutcomes: per-instance trade outcomes (SONA learning input)
- GraphNexusLearningCache: cached learning summaries
- GraphNexusDiscoverySnapshots: discovery snapshots
- GraphNexusOutcomeSeries: outcome time-series
- GraphNexusAnalystPanel: panel decisions

What this SCRIPT does NOT touch:
- LiveOrderWAL: globally-scoped (no instance_id field); operator clears
  manually if needed. See docs/runbooks/live-launch-checklist.md.

What this SCRIPT PRESERVES (shared caches + backtest snapshots):
- All shared news/sentiment/article caches (article-hash + provider + model keyed)
- Alpaca article cache, Benzinga bulk cache, FinBERT scoring cache
- Company/Macro article LLM classification caches
- Sentiment fingerprint cache
- Overlay bars cache
- Neo4j graph data
- NexusStrategyCache rows with origin="backtest" (the snapshots used at live boot)

Dry run by default. Pass --apply to actually delete.

Usage:
  python scripts/clear_main_instance_lookback_state.py                # dry-run for instance 'main'
  python scripts/clear_main_instance_lookback_state.py --apply        # apply to 'main'
  python scripts/clear_main_instance_lookback_state.py --instance foo --apply  # apply to 'foo'
"""

from __future__ import annotations

import argparse
import os
import sys

INSTANCE_ID = "main"  # default; CLI --instance overrides at runtime
DB_NAME = "IntelliStock"


def _build_targets(instance_id: str):
    """Return the list of (table_name, criteria) or (table_name, criteria, combine)
    tuples for the given instance.

    criteria entries are (field_name, value, match_mode) where match_mode is
    one of "exact", "prefix", "contains", "special". Special pseudo-fields:
      - "origin_not_backtest": row must not have origin == "backtest"
        (used to keep ``backtest`` snapshots while clearing ``live`` and
        legacy rows that pre-date the origin field).

    The optional 3rd element is ``"or"`` (default, back-compat) or ``"and"``.
    Multiple criteria within a table combine per that mode.
    """
    return [
        # ----- Original 4 -----
        ("GraphNexusTradeContexts", [
            ("instance_id", f"{instance_id}|", "prefix"),
            ("base_instance_id", instance_id, "exact"),
        ]),
        ("GraphNexusOutcomes", [
            ("instance_id", f"{instance_id}|", "prefix"),
            ("base_instance_id", instance_id, "exact"),
        ]),
        ("NexusRuntimeState", [
            ("id", f"{instance_id}:", "prefix"),
        ]),
        ("LiveState", [
            ("id", instance_id, "exact"),
        ]),
        # ----- Phase 1 additions (10 more tables) -----
        # NexusStrategyCache: per-instance live-origin (and legacy-origin) rows
        # only. MUST AND-combine the instance filter with the origin filter so
        # we never touch another instance's rows AND never delete backtest
        # snapshots. The previous OR-only filter on origin=live would delete
        # every live-origin row across ALL instances (production safety bug
        # from the 2026-05-21 bug-sweep). ``origin_not_backtest`` covers both
        # origin="live" and legacy rows that pre-date the origin field.
        ("NexusStrategyCache", [
            ("instance_id", instance_id, "exact"),
            ("origin_not_backtest", None, "special"),
        ], "and"),
        # LiveOrderWAL is intentionally globally-scoped: one broker process
        # per host -> one WAL. WALStore.insert does NOT stamp ``instance_id``,
        # so a per-instance filter would match 0 rows. WAL cleanup is the
        # operator's manual responsibility (see live-launch-checklist.md).
        # Removed from per-instance cleanup in the 2026-05-21 bug-sweep.
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
        # GraphNexusRotationCooldown / GraphNexusLearningCache /
        # GraphNexusDiscoverySnapshots: ``id`` is the per-instance PK; there
        # is no separate ``instance_id`` field. RotationCooldown ids are also
        # scoped ("main|<hash>"), so add the prefix.
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


def _build_filter(r, criteria, combine: str = "or"):
    """Build a rethinkdb `filter(...)` expression combining the criteria.

    combine="or" (default, back-compat) -> any match
    combine="and"                       -> all match
    """
    expr = None
    for field, val, mode in criteria:
        if mode == "special":
            if field == "origin_not_backtest":
                # Row must NOT have origin == "backtest". Treats missing
                # origin as "not backtest" (legacy rows pre-date the field).
                this = r.row["origin"].default("") != "backtest"
            else:
                continue
        else:
            row_field = r.row[field]
            if mode == "exact":
                this = row_field.eq(val)
            elif mode == "prefix":
                this = row_field.default("").match(f"^{val.replace('|', '[|]')}")
            elif mode == "contains":
                this = row_field.default("").match(val)
            else:
                continue
        if expr is None:
            expr = this
        elif combine == "and":
            expr = expr & this
        else:
            expr = expr | this
    return expr


def main(apply: bool, instance_id: str) -> int:
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

    targets = _build_targets(instance_id)
    total_to_delete = 0
    summary: list = []
    try:
        existing_tables = set(r.db(DB_NAME).table_list().run(conn))
        for entry in targets:
            # Entries are (table, criteria) or (table, criteria, combine_mode).
            if len(entry) == 3:
                table, criteria, combine_mode = entry
            else:
                table, criteria = entry
                combine_mode = "or"
            if table not in existing_tables:
                print(f"SKIP  {table}: table does not exist.")
                summary.append((table, 0, "skipped"))
                continue
            expr = _build_filter(r, criteria, combine=combine_mode)
            if expr is None:
                print(f"SKIP  {table}: no criteria resolved.")
                summary.append((table, 0, "skipped"))
                continue
            count = int(r.db(DB_NAME).table(table).filter(expr).count().run(conn) or 0)
            total_to_delete += count
            verb = "WILL DELETE" if not apply else "DELETING   "
            print(f"{verb} {count:>5} rows from {table}")
            deleted = 0
            if apply and count > 0:
                res = r.db(DB_NAME).table(table).filter(expr).delete().run(conn)
                deleted = int((res or {}).get("deleted", 0) or 0)
                print(f"           -> deleted={deleted}")
            summary.append((table, count, "deleted" if apply else "would_delete"))
    finally:
        try:
            conn.close()
        except Exception:
            pass

    print(f"\n--- Summary (instance='{instance_id}') ---")
    for table, count, status in summary:
        print(f"  {table:40s} {count:>6} ({status})")
    print(f"  {'TOTAL':40s} {total_to_delete:>6}")

    if not apply:
        print(
            f"\nDRY RUN. {total_to_delete} row(s) would be deleted across {len(targets)} tables. "
            "Re-run with --apply to execute."
        )
    else:
        print(
            f"\nDone. Cleared per-instance lookback + decision + cache state for '{instance_id}' "
            f"across {len(targets)} tables. Backtest-origin NexusStrategyCache snapshots PRESERVED. "
            "Restart the live broker - the next boot will use the snapshot (if available) or run "
            "a full lookback."
        )
    return 0


def _parse_args():
    p = argparse.ArgumentParser(description="Clear per-instance lookback + decision + cache state.")
    p.add_argument("--apply", action="store_true", help="actually delete (default: dry-run)")
    p.add_argument("--instance", default=INSTANCE_ID, help=f"instance_id to clear (default: {INSTANCE_ID})")
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    raise SystemExit(main(args.apply, args.instance))
