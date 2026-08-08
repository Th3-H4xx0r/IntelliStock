#!/usr/bin/env python3
"""Reset the mutable active-event state before a backtest, KEEP its cache.

The operator asked why `active_event_maintenance` burns ~$1.47 of a $3.43 run
when "events do not change between backtests". Measured across bt 820236 and
bt 718249 (same window, same instance, same cash):

    date        820236 (current, candidates)   718249 (current, candidates)
    2026-01-01            (6, 24)                       (5, 24)
    2026-01-02           (23, 21)                      (21, 21)
    2026-01-05           (40, 20)                      (39, 20)
    2026-01-06           (56, 20)                      (52, 20)

CANDIDATES ARE IDENTICAL EVERY SINGLE DAY. Only `current` differs, and it
differs from day one. So the LLM, the discovery and the news are all
reproducible; the cache is not broken and it is not misconfigured.

`GraphNexusActiveEvents` is persistent, shared, mutable state. Every run appends
to it, so the next run starts from whatever the last one left behind. The
maintenance cache is keyed on (scope, date) but VALIDATED on
`current_events_fingerprint` + `candidate_fingerprint`, so a different
`current_events` is a legitimate miss — the inputs really did change. Worse, it
compounds: `current_events` on day N is the maintenance OUTPUT of day N-1, so
one difference on day 1 invalidates every day after it. 42 invocations, 42 LLM
batches, 0 cache hits.

Clearing BOTH tables does not help — it just guarantees a cold recompute. The
fix is asymmetric:

    CLEAR   GraphNexusActiveEvents, GraphNexusActiveEventHistory
    KEEP    GraphNexusActiveEventMaintenance

Then every run replays from the same cold baseline, day 1's fingerprint matches
the cached doc, its reused output feeds day 2, and the chain holds for the whole
window. Genuinely-changed inputs (new window, new model, new prompt version, new
salt) still miss and still recompute, because they change the fingerprint or the
scope — which is exactly the requested behaviour: use the cache whenever
possible, recompute when something actually changed.

Dry-run by default.

    python3 scripts/reset_backtest_event_state.py --instance v2-let-run-core
    python3 scripts/reset_backtest_event_state.py --instance v2-let-run-core --apply
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from rethinkdb import RethinkDB

r = RethinkDB()

DB_NAME = "IntelliStock"
REPO = Path(__file__).resolve().parent.parent

# Mutable per-run state: this is what makes current_events differ.
CLEAR_TABLES = ("GraphNexusActiveEvents", "GraphNexusActiveEventHistory")
# The cache we are trying to HIT. Never cleared here.
KEEP_TABLES = ("GraphNexusActiveEventMaintenance",)


def _load_env() -> None:
    env = REPO / ".env"
    if not env.is_file():
        return
    for line in env.read_text(encoding="utf-8", errors="ignore").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def conn(timeout: int = 30):
    _load_env()
    return r.connect(
        host=os.environ.get("RETHINKDB_HOST"),
        port=int(os.environ.get("RETHINKDB_PORT", 28015)),
        db=os.environ.get("RETHINKDB_DB", DB_NAME),
        timeout=timeout,
    )


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--instance", required=True,
                    help="base instance id, e.g. v2-let-run-core")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args(argv)

    c = conn()
    tables = set(r.db(DB_NAME).table_list().run(c))
    prefix = str(a.instance).strip()

    total = 0
    for table in CLEAR_TABLES:
        if table not in tables:
            print(f"  {table:36} (absent)")
            continue
        # instance_id is scope-suffixed ("<base>|<scope_id>"), so match on the
        # BASE id with a prefix match, never equality.
        expr = r.row["instance_id"].match(f"^{prefix}(\\||$)")
        n = int(r.db(DB_NAME).table(table).filter(expr).count().run(c) or 0)
        total += n
        print(f"  {table:36} {n:6} row(s) {'-> DELETE' if a.apply else '(dry-run)'}")
        if a.apply and n:
            r.db(DB_NAME).table(table).filter(expr).delete().run(c)

    for table in KEEP_TABLES:
        if table not in tables:
            continue
        n = int(r.db(DB_NAME).table(table).count().run(c) or 0)
        print(f"  {table:36} {n:6} row(s) KEPT — this is the cache we want to hit")

    print("")
    if a.apply:
        print(f"cleared {total} row(s). The next run replays from a cold event "
              f"baseline, so the maintenance cache should now HIT.")
    else:
        print(f"dry-run: {total} row(s) would be cleared. Re-run with --apply.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
