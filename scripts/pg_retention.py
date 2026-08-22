#!/usr/bin/env python3
"""Retention sweeper driven by the schema registry.

Every RetentionSpec whose env var is set gets a batched, indexed, ranged
delete. Retention is OFF for every cache table until the operator sets the
env var -- the registry's default_days is None everywhere except
LearningObservations, which already has a 90-day policy in
backend/self_learning/retention.py.

Two rules carried over from that module, both load-bearing:
  * the retention floor is 1 day (a stored 0 would delete the table);
  * a row whose timestamp will not parse is NEVER deleted.

The cache tables store their timestamp as an ISO STRING, not a timestamptz: a
text->timestamptz cast is only STABLE, so PG rejects it in a generated column.
The cast therefore lives here, in the WHERE clause, guarded by
pg_input_is_valid so an unparseable value can never match.
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "backend"))

from db import pool as dbpool          # noqa: E402
from db import schema                  # noqa: E402


def _cutoff_iso(days: int) -> str:
    return (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)).isoformat()


def sweep(table: str, *, dry_run: bool = False, batch: int = 10000) -> dict:
    spec_ = schema.spec(table)
    if spec_.retention is None:
        return {"table": table, "deleted": 0, "skipped": "no retention spec"}
    days = spec_.retention.days()
    if days is None:
        return {"table": table, "deleted": 0, "skipped": "retention off"}
    cutoff = _cutoff_iso(days)
    field = spec_.retention.field
    q = schema.quoted(table)
    if spec_.retention.is_column:
        # PriceHistory's retention field "ts" is a real timestamptz COLUMN.
        # Filtering on doc->>'ts' there is always NULL, so the sweep reported
        # deleted:0 forever while main() happily kept calling it.
        where = '"%s" IS NOT NULL AND "%s" < %%s::timestamptz' % (field, field)
        params = (cutoff,)
    else:
        # The predicate is deliberately explicit about parseability: a row
        # whose timestamp is not a valid timestamptz never matches, so it is
        # never deleted. pg_input_is_valid is PG16+; PG17 is the target.
        where = ("doc ->> %s IS NOT NULL "
                 "AND pg_input_is_valid(doc ->> %s, 'timestamptz') "
                 "AND (doc ->> %s)::timestamptz < %s::timestamptz")
        params = (field, field, field, cutoff)
    if dry_run:
        with dbpool.cursor() as cur:
            cur.execute("SELECT count(*) AS n FROM %s WHERE %s" % (q, where), params)
            return {"table": table, "would_delete": cur.fetchone()["n"],
                    "cutoff": cutoff}
    # Batched on the PRIMARY KEY, never on ctid: a ctid is unique only WITHIN
    # a partition, so on a partitioned table the subquery could name a tuple
    # id that matches a different row in a sibling partition.
    pk = ", ".join('"%s"' % c for c in spec_.pk)
    deleted = 0
    while True:
        with dbpool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM {q} WHERE ({pk}) IN "
                    "(SELECT {pk} FROM {q} WHERE {w} LIMIT {n})".format(
                        q=q, pk=pk, w=where, n=int(batch)), params)
                n = cur.rowcount
            conn.commit()
        deleted += n
        if n < batch:
            break
    return {"table": table, "deleted": deleted, "cutoff": cutoff}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tables", default="",
                    help="comma-separated subset (default: every table with a spec)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--batch", type=int, default=10000)
    args = ap.parse_args(argv)
    names = ([t.strip() for t in args.tables.split(",") if t.strip()]
             or [n for n, s in schema.TABLES.items() if s.retention is not None])
    rc = 0
    for name in sorted(names):
        try:
            print(sweep(name, dry_run=args.dry_run, batch=args.batch))
        except Exception as exc:               # keep sweeping the other tables
            print({"table": name, "error": "%s: %s" % (type(exc).__name__, exc)})
            rc = 1
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
