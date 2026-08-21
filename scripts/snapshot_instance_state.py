#!/usr/bin/env python3
"""Export / restore an instance's decision-steering state, byte-faithfully.

    # after a warmup run
    python3 scripts/snapshot_instance_state.py export v2-conv-trt --out /tmp/warm_d.state.json

    # before the next arm (instance must be CLEARED first)
    python3 scripts/snapshot_instance_state.py restore v2-conv-trt --in /tmp/warm_d.state.json --apply

Why this exists: cold backtests strip the discovery pool (they understate the
strategy), warm state carried across unrelated runs is contaminated, and the
same config cold-run hours apart trades a different book (shared caches drift
with wall-clock time). The warm-but-clean protocol is: build the pool INSIDE
the experiment with a warmup run over the weeks before the window, snapshot it
once, and restore the identical snapshot for every arm. Both arms then start
from the same rich pool — `compare_arm_starts(..., require_cold=False)`
returns IDENTICAL_WARM by construction, not coincidence.

The export set is EXACTLY what `clear_instance_state.build_targets(id,
"full_instance")` would delete — the two must agree or restore is partial, so
the target list is imported from that module rather than copied.

Restore REFUSES to run onto a non-empty state (clear first): merging a
snapshot into leftovers is exactly the contamination this tool exists to end.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "backend"))
sys.path.insert(0, str(_REPO / "scripts"))

from pull_backtest_logs import _load_dotenv  # noqa: E402

_load_dotenv(_REPO)

from rethinkdb import RethinkDB  # noqa: E402

import clear_instance_state as cis  # noqa: E402

DB = "IntelliStock"


def _conn():
    r = RethinkDB()
    return r, r.connect(host=os.environ["RETHINKDB_HOST"],
                        port=int(os.environ.get("RETHINKDB_PORT", "28015")),
                        timeout=30)


def _targets(instance_id):
    out = []
    for t in cis.build_targets(instance_id, "full_instance"):
        table, criteria = t[0], t[1]
        combine = t[2] if len(t) > 2 else "or"
        out.append((table, criteria, combine))
    return out


def _select_rows(r, conn, table, criteria, combine):
    pred = cis._build_filter(r, criteria, combine)
    return list(r.db(DB).table(table).filter(pred).run(conn))


def _digest(tables):
    h = hashlib.sha256()
    for name in sorted(tables):
        for row in sorted(tables[name],
                          key=lambda x: str(x.get("id", ""))):
            h.update(json.dumps(row, sort_keys=True, default=str)
                     .encode("utf-8"))
    return h.hexdigest()


def do_export(instance_id, out_path):
    r, conn = _conn()
    tables, counts = {}, {}
    for table, criteria, combine in _targets(instance_id):
        try:
            rows = _select_rows(r, conn, table, criteria, combine)
        except Exception as exc:
            # A missing table is a real difference between environments —
            # record it loudly rather than silently exporting nothing.
            print(f"  {table}: EXPORT FAILED ({type(exc).__name__}: {exc})")
            rows = []
        tables[table] = rows
        counts[table] = len(rows)
        print(f"  {table}: {len(rows)} row(s)")
    doc = {
        "instance_id": instance_id,
        "counts": counts,
        "digest": _digest(tables),
        "tables": tables,
    }
    Path(out_path).write_text(json.dumps(doc, default=str))
    total = sum(counts.values())
    print(f"exported {total} row(s) across {len(tables)} table(s)")
    print(f"digest {doc['digest']}")
    print(f"wrote {out_path}")
    return 0


def do_restore(instance_id, in_path, apply):
    doc = json.loads(Path(in_path).read_text())
    if doc.get("instance_id") != instance_id:
        print(f"REFUSED: snapshot is for {doc.get('instance_id')!r}, "
              f"not {instance_id!r}")
        return 2
    r, conn = _conn()
    # Refuse to restore onto leftovers — that re-creates cross-run
    # contamination with extra steps.
    dirty = []
    for table, criteria, combine in _targets(instance_id):
        try:
            pred = cis._build_filter(r, criteria, combine)
            n = r.db(DB).table(table).filter(pred).count().run(conn)
        except Exception:
            n = 0
        if n:
            dirty.append((table, n))
    if dirty:
        print("REFUSED: instance state is not empty — clear it first "
              "(clear-state full_instance). Residue:")
        for table, n in dirty:
            print(f"  {table}: {n} row(s)")
        return 2
    total = 0
    for table, rows in doc["tables"].items():
        if not rows:
            continue
        if not apply:
            print(f"  would insert {len(rows)} row(s) into {table}")
            total += len(rows)
            continue
        res = r.db(DB).table(table).insert(rows, conflict="error").run(conn)
        errs = int(res.get("errors", 0) or 0)
        if errs:
            print(f"  {table}: {errs} INSERT ERROR(S) — "
                  f"{str(res.get('first_error'))[:160]}")
            return 2
        total += int(res.get("inserted", 0) or 0)
        print(f"  {table}: inserted {res.get('inserted', 0)}")
    if not apply:
        print(f"DRY RUN — {total} row(s) would be restored. Re-run with --apply.")
        return 0
    # Verify: re-export and compare digests, so "restored" is a checked claim.
    tables = {}
    for table, criteria, combine in _targets(instance_id):
        try:
            tables[table] = _select_rows(r, conn, table, criteria, combine)
        except Exception:
            tables[table] = []
    got = _digest(tables)
    want = doc.get("digest")
    if got != want:
        print(f"RESTORE DIGEST MISMATCH: want {want[:16]}… got {got[:16]}…")
        return 2
    print(f"restored {total} row(s); digest verified {got[:16]}…")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("action", choices=["export", "restore"])
    p.add_argument("instance_id")
    p.add_argument("--out", help="export: output file")
    p.add_argument("--in", dest="in_path", help="restore: snapshot file")
    p.add_argument("--apply", action="store_true",
                   help="restore: actually insert (default dry-run)")
    a = p.parse_args(argv)
    if a.action == "export":
        if not a.out:
            p.error("export requires --out")
        return do_export(a.instance_id, a.out)
    if not a.in_path:
        p.error("restore requires --in")
    return do_restore(a.instance_id, a.in_path, a.apply)


if __name__ == "__main__":
    sys.exit(main())
