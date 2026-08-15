#!/usr/bin/env python3
"""Backfill LearningObservations from BacktestResults already on disk.

Phase 1's sources are documents that already exist, so the subsystem starts with
real history instead of an empty table. Writes are content-keyed and idempotent,
so re-running this is safe.

    python3 scripts/backfill_learning_observations.py            # dry run
    python3 scripts/backfill_learning_observations.py --apply
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_REPO, "backend"))

from rethinkdb import RethinkDB                                   # noqa: E402

from self_learning import store                                   # noqa: E402
from self_learning.pipeline import process_backtest_document      # noqa: E402

r = RethinkDB()
DB_NAME = "IntelliStock"
_TERMINAL = frozenset({"completed", "complete", "finished", "done"})


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0,
                        help="process at most N runs (0 = all)")
    parser.add_argument("--apply", action="store_true",
                        help="write; omit for a dry run")
    args = parser.parse_args()

    conn = store.get_conn()
    store.ensure_tables(conn)
    now = datetime.now(timezone.utc).isoformat()

    rows = list(r.db(DB_NAME).table("BacktestResults").pluck("id", "status").run(conn))
    done = [row for row in rows
            if str(row.get("status") or "").strip().lower() in _TERMINAL]
    done.sort(key=lambda d: str(d.get("id")), reverse=True)
    if args.limit:
        done = done[:args.limit]

    print(f"{len(done)} completed run(s) to process "
          f"({'APPLY' if args.apply else 'DRY RUN'})")
    total_obs = total_find = 0
    for row in done:
        doc = r.db(DB_NAME).table("BacktestResults").get(row["id"]).run(conn)
        if not doc:
            continue
        result = process_backtest_document(doc, detected_at=now)
        if not result["observations"]:
            continue
        total_obs += len(result["observations"])
        total_find += len(result["findings"])
        summary = result["summary"]
        print(f"  run {result['run_id']:>8}  {result['target']:<34} "
              f"obs={len(result['observations']):>5} "
              f"buys={summary['buy_executed']}/{summary['buy_decided']} "
              f"findings={len(result['findings'])}")
        for finding in result["findings"]:
            print(f"      [{finding.severity}] {finding.title}")
        if args.apply:
            store.put_observations(conn, result["observations"])
            store.put_funnel(conn, result["run_id"], summary,
                             target=result["target"], observed_at=now)
            store.put_findings(conn, result["findings"])

    print(f"\ntotal observations={total_obs} findings={total_find}")
    if not args.apply:
        print("DRY RUN — nothing written. Re-run with --apply.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
