"""One-time: purge LLM-hallucinated tickers from GraphNexusDiscoveredStocks.

Context: LLM sentiment/event-maintenance occasionally emits company
names ("ADOBE" for Adobe Inc., which really trades as "ADBE") or pure
hallucinations ("DLUX", "BGN", "ACVF", "ABAC") that got stored in the
DISCOVERED_TABLE before the write-path ticker validator landed. Even
though live discovery now filters on read, the legacy rows are still in
the DB and the cleanup saves disk space + makes ad-hoc queries sane.

Dry-run by default. Pass --apply to actually delete.

2026-04-23 F4: the old `--instance <name>` filter used ReQL equality,
which missed composite instance identifiers like `main|<hash>` that the
strategy writes for per-model runtime scoping. Now matches exact OR
composite (`^<instance>($|\\|)`) with regex-escaping for safety.

Usage:
  python scripts/purge_bad_discovered_tickers.py                  # dry-run
  python scripts/purge_bad_discovered_tickers.py --apply          # delete
  python scripts/purge_bad_discovered_tickers.py --instance main  # scope
"""

from __future__ import annotations

import argparse
import os
import re
import sys

DB_NAME = "IntelliStock"
DISCOVERED_TABLE = "GraphNexusDiscoveredStocks"


def main(apply: bool, instance_id: str | None) -> int:
    try:
        from db import store as RethinkDB
    except ImportError:
        print("ERROR: the db.store package is not importable.", file=sys.stderr)
        return 2
    # Allow importing ticker_universe from backend/.
    _here = os.path.dirname(os.path.abspath(__file__))
    _backend = os.path.normpath(os.path.join(_here, "..", "backend"))
    if _backend not in sys.path:
        sys.path.insert(0, _backend)
    try:
        from ticker_universe import is_valid_us_ticker
    except ImportError as e:
        print(f"ERROR: could not import ticker_universe from {_backend}: {e}", file=sys.stderr)
        return 3

    r = RethinkDB
    host = os.environ.get("RETHINKDB_HOST", "localhost")
    port = int(os.environ.get("RETHINKDB_PORT", "28015"))
    try:
        conn = None
    except Exception as e:
        print(f"ERROR: connect({host}:{port}): {e}", file=sys.stderr)
        return 4

    try:
        tables = list(r.table_list())
        if DISCOVERED_TABLE not in tables:
            print(f"SKIP: table {DISCOVERED_TABLE} does not exist.")
            return 0
        # Pull all rows we'd consider — optionally scoped to one instance.
        # F4 (2026-04-23): match exact instance OR composite `<instance>|<hash>`.
        # re.escape so metacharacters in the instance name (rare but possible)
        # don't become wildcards; .default("") so rows missing instance_id
        # don't raise. `^<escaped>($|\\|)` matches "main" and "main|a87b..."
        # but NOT "main2", "maintenance", etc.
        # The base id matches "main" and "main|<hash>" but never "main2".
        query = DISCOVERED_TABLE
        if instance_id:
            _f = r.P.field("instance_id").default("")
            query = r.filter(query, _f.eq(instance_id)
                             | _f.starts_with(instance_id + "|"))
        all_docs = list(r.run(query))
        by_ticker: dict[str, list] = {}
        for doc in all_docs:
            t = str(doc.get("ticker") or "").strip().upper()
            if not t:
                continue
            by_ticker.setdefault(t, []).append(doc.get("id"))
        bad_tickers = sorted(t for t in by_ticker if not is_valid_us_ticker(t))
        if not bad_tickers:
            print(f"No hallucinated tickers found (scanned {len(all_docs)} rows).")
            return 0
        total_rows = sum(len(by_ticker[t]) for t in bad_tickers)
        print(f"Found {len(bad_tickers)} hallucinated ticker(s) across {total_rows} row(s):")
        for t in bad_tickers:
            print(f"  {t:<8} {len(by_ticker[t])} row(s)")
        if not apply:
            print(f"\nDRY RUN. Re-run with --apply to delete {total_rows} row(s).")
            return 0
        deleted = 0
        for t in bad_tickers:
            _pred = r.P.field("ticker").eq(t)
            if instance_id:
                _f = r.P.field("instance_id").default("")
                _pred = _pred & (_f.eq(instance_id)
                                 | _f.starts_with(instance_id + "|"))
            res = r.delete(DISCOVERED_TABLE, r.filter(DISCOVERED_TABLE, _pred))
            deleted += int((res or {}).get("deleted", 0) or 0)
        print(f"\nDone. Deleted {deleted} row(s) from {DISCOVERED_TABLE}.")
        return 0
    finally:
        try:
            conn.close()
        except Exception:
            pass


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="actually delete (default: dry-run)")
    ap.add_argument("--instance", default=None, help="scope to one instance_id (default: all)")
    args = ap.parse_args()
    raise SystemExit(main(args.apply, args.instance))
