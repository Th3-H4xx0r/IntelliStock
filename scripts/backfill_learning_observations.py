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
_CRYPTO_HINTS = ("crypto", "coin")


def _venue_for(doc) -> str:
    """Crypto and equity are different targets. A finding's id hashes its
    target, so mislabelling collapses two venues onto one thread."""
    kind = str((doc or {}).get("kind") or (doc or {}).get("instance_kind") or "")
    if any(hint in kind.lower() for hint in _CRYPTO_HINTS):
        return "crypto"
    tickers = (doc or {}).get("tickers") or []
    if tickers and all("/" in str(t) for t in tickers):
        return "crypto"
    return "equity"


def _run_time(doc) -> str:
    """Order by the RUN's time, not the backfill's — stamping "now" collapses
    every historical run onto one instant in the feed."""
    for key in ("completed_at", "end_date", "timestamp", "_last_active"):
        value = (doc or {}).get(key)
        if value:
            return str(value)
    return ""


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
        venue = _venue_for(doc)
        result = process_backtest_document(doc, detected_at=now, venue=venue)
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
                             target=result["target"],
                             observed_at=_run_time(doc))
            store.put_findings(conn, result["findings"])

    print(f"\ntotal observations={total_obs} findings={total_find}")
    if not args.apply:
        print("DRY RUN — nothing written. Re-run with --apply.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
