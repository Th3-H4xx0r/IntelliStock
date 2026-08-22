"""Purge poisoned rows from GraphNexusOverlayBarsCache (2026-07-19).

Forensics (docs/superpowers/specs/2026-07-19-bear-neutral-regime-safety-design.md)
found the shared per-symbol bars cache serving rows that blind the V31 regime
detector, sector price context, and price-trend detection for any backtest
window earlier than the row's own coverage:
- rows with EMPTY bars persist forever (SPY empty since 2026-04-25), and
- rows truncated to a recent window (QQQ et al. starting 2026-04-30) shadow
  healthy proxies for earlier sim dates.

Deletes rows where bars is empty OR the first bar is after --cutoff
(default 2026-01-01). Rows refetch on next use. Dry-run by default.
"""
import argparse
import json
import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

TABLE = "GraphNexusOverlayBarsCache"


def _conn():
    """R26: the store pools its own connection per operation; the pair is kept
    so the call site below reads as it did."""
    from dotenv import load_dotenv
    load_dotenv(os.path.join(_REPO, ".env"))
    from db import store as _store
    return _store, None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--cutoff", default="2026-01-01",
                        help="Delete rows whose first bar is after this date.")
    args = parser.parse_args(argv)

    r, conn = _conn()
    rows = r.iter(TABLE)   # a cursor: this table is large
    doomed = []
    kept = 0
    for doc in rows:
        bars = doc.get("bars") or []
        first = str(bars[0].get("t", bars[0].get("date", "")))[:10] if bars else ""
        last = str(bars[-1].get("t", bars[-1].get("date", "")))[:10] if bars else ""
        if not bars or (first and first > args.cutoff):
            doomed.append(doc["id"])
            print(f"[{'DELETE' if args.apply else 'DRY-RUN'}] {doc['id']:6s} "
                  f"n={len(bars):4d} first={first or '-'} last={last or '-'} "
                  f"cached_at={str(doc.get('cached_at', ''))[:19]}")
        else:
            kept += 1
    if args.apply and doomed:
        deleted = 0
        for _id in doomed:
            deleted += int((r.delete(TABLE, _id) or {}).get("deleted", 0))
        print(f"DELETED {deleted} row(s); kept {kept}.")
    else:
        print(f"Would delete {len(doomed)} row(s); keep {kept}. "
              "Re-run with --apply to execute.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
