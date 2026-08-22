#!/usr/bin/env python3
"""Retention sweeper driven by the schema registry.

Every RetentionSpec whose env var is set gets a batched, indexed, ranged
delete. Retention is OFF for every cache table until the operator sets the
env var -- the registry's default_days is None everywhere except
LearningObservations, which already has a 90-day policy in
backend/self_learning/retention.py.

Three rules, all load-bearing:

  * the retention floor is 1 day (a stored 0 would delete the table);
  * a row whose timestamp will not parse is NEVER deleted;
  * no statement is unindexed, and no statement is unbounded.

The cache tables store their timestamp as an ISO STRING, not a timestamptz: a
text->timestamptz cast is only STABLE, so PG rejects it in a generated column
AND in an expression index. There is therefore no index that answers
``(cached_at)::timestamptz < cutoff`` directly, and filtering on
``doc ->> 'cached_at'`` -- which is what this module did first -- is a
sequential scan of the whole table PER BATCH: 40 scans of 392,157 rows to
sweep GraphNexusLLMPromptCache once.

So the predicate is two-layered. A **text** range on the STORED generated
column drives the index and prunes the scan; the exact timestamptz comparison
then decides, so the semantics are unchanged. The text bound is the cutoff
plus :data:`_TEXT_BOUND_SLACK`, which is wider than the widest real UTC offset
(+/-14h), so the prefilter can never exclude a row the exact predicate would
have deleted. It is a superset, always.

The guard is not theoretical. Sampled live 2026-08-22, read-only:
``GraphNexusLLMPromptCache.cached_at`` and ``AlpacaBarsCache.cached_at`` are
ISO-8601 with a ``Z``; ``LearningObservations.as_of`` is naive ISO; but
``LLMUsage.ts`` holds **epoch milliseconds** ('1780059356069'), for which
``pg_input_is_valid(..., 'timestamptz')`` is false. A bare text comparison
would have found every one of those 300,291 rows below any ISO cutoff and
deleted the entire table. The cast guard is what stops it -- at the cost of
that window being inert, which is reported rather than hidden.
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

BATCH = 10000

#: Wider than the widest real UTC offset (+/-14:00), so a text bound of
#: ``cutoff + slack`` is always a superset of "this instant is before cutoff".
_TEXT_BOUND_SLACK = dt.timedelta(hours=28)


def _cutoff_iso(days: int) -> str:
    return (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)).isoformat()


def retention_days(spec_):
    """Days to keep, or None when retention is OFF for this table.

    ``RetentionSpec.days()`` is the registry's own accessor: the env var wins,
    then ``default_days``, with a floor of 1 day, and a non-integer env value
    raises rather than silently disabling the sweep.
    """
    if getattr(spec_, "retention", None) is None:
        return None
    return spec_.retention.days()


def configured_tables() -> list:
    """Every table the registry gives a RetentionSpec, sorted."""
    return sorted(n for n, s in schema.TABLES.items() if s.retention is not None)


def _column(field: str) -> str:
    if '"' in field:
        raise ValueError("illegal retention field %r" % field)
    return '"%s"' % field


def _predicate(spec_, cutoff: str):
    """``(where_sql, params)`` -- indexed, and exact about parseability."""
    col = _column(spec_.retention.field)
    if spec_.retention.is_column:
        # PriceHistory's retention field "ts" is a real timestamptz COLUMN.
        # Filtering on doc->>'ts' there is always NULL, so the sweep reported
        # deleted:0 forever while main() happily kept calling it.
        return "%s IS NOT NULL AND %s < %%s::timestamptz" % (col, col), (cutoff,)
    bound = (dt.datetime.fromisoformat(cutoff) + _TEXT_BOUND_SLACK).isoformat()
    return ("{c} IS NOT NULL "
            'AND {c} COLLATE "C" < %s '                 # indexed, prunes the scan
            "AND pg_input_is_valid({c}, 'timestamptz') "
            "AND ({c})::timestamptz < %s::timestamptz"  # exact, decides
            .format(c=col), (bound, cutoff))


def _query(sql: str, params: tuple) -> list:
    with dbpool.cursor() as cur:
        cur.execute(sql, params)
        return list(cur.fetchall())


def _execute(sql: str, params: tuple) -> int:
    """The single place a DELETE is issued, so a test can watch every one."""
    with dbpool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            n = cur.rowcount
        conn.commit()
    return n


def prune_table(table: str, *, dry_run: bool = False, batch: int = BATCH) -> dict:
    spec_ = schema.spec(table)
    if spec_.retention is None:
        return {"table": table, "deleted": 0, "skipped": "no retention spec"}
    days = retention_days(spec_)
    if days is None:
        return {"table": table, "deleted": 0, "skipped": "retention off"}
    cutoff = _cutoff_iso(days)
    where, params = _predicate(spec_, cutoff)
    q = schema.quoted(table)
    if dry_run:
        rows = _query("SELECT count(*) AS n FROM %s WHERE %s" % (q, where), params)
        return {"table": table, "would_delete": rows[0]["n"], "cutoff": cutoff}
    # Batched on the PRIMARY KEY, never on ctid: a ctid is unique only WITHIN
    # a partition, so on a partitioned table the subquery could name a tuple
    # id that matches a different row in a sibling partition.
    pk = ", ".join('"%s"' % c for c in spec_.pk)
    sql = ("DELETE FROM {q} WHERE ({pk}) IN "
           "(SELECT {pk} FROM {q} WHERE {w} LIMIT {n})".format(
               q=q, pk=pk, w=where, n=int(batch)))
    deleted = 0
    while True:
        n = _execute(sql, params)
        deleted += n
        if n < batch:
            break
    return {"table": table, "deleted": deleted, "cutoff": cutoff}


def sweep(table: str, *, dry_run: bool = False, batch: int = BATCH) -> dict:
    """The name the rest of the tree already calls. Same function."""
    return prune_table(table, dry_run=dry_run, batch=batch)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Registry-driven retention sweeper")
    ap.add_argument("--tables", default="",
                    help="comma-separated subset (default: every table with a spec)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--batch", type=int, default=BATCH)
    args = ap.parse_args(argv)
    names = ([t.strip() for t in args.tables.split(",") if t.strip()]
             or configured_tables())
    rc = 0
    for name in sorted(names):
        try:
            print(prune_table(name, dry_run=args.dry_run, batch=args.batch))
        except Exception as exc:               # keep sweeping the other tables
            print({"table": name, "error": "%s: %s" % (type(exc).__name__, exc)})
            rc = 1
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
