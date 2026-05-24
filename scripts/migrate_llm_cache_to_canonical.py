#!/usr/bin/env python3
"""Re-key Nexus article-LLM cache rows to the canonical model-identity scheme.

The article doc cache used to key on the raw provider + model string + a
provider-specific effort suffix:

    OLD: {schema}|{article_hash}|{provider}|{model_ref}|{prompt_version}

The new scheme drops the provider and uses canonical_model_cache_key (so the
same underlying model under different provider/name strings shares cache, and
the reasoning level is always in the key):

    NEW: {schema}|{article_hash}|{canonical}|{prompt_version}

This script re-keys existing rows so a Bedrock config immediately reuses an
Azure-built cache. It uses the SAME _auto_normalize_model helper the runtime
lookup uses, so migrated ids match runtime keys exactly.

Run dry first:   python3 scripts/migrate_llm_cache_to_canonical.py --host REDACTED-HOST
Then apply:      python3 scripts/migrate_llm_cache_to_canonical.py --host REDACTED-HOST --apply
Cleanup orphans: python3 scripts/migrate_llm_cache_to_canonical.py --host REDACTED-HOST --apply --cleanup
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
from llm_utils import _auto_normalize_model  # noqa: E402

TABLES = ("GraphNexusNewsLLMCompany", "GraphNexusNewsLLMMacro")
_BATCH = 500  # rows per insert/delete round-trip (tables can hold tens of thousands)
# Effort suffixes the old model_ref could carry (llm_model_reference upper-cased
# the effort, e.g. 'gpt-oss-120b-MEDIUM'). Matched case-insensitively.
_EFFORT_SUFFIXES = ("-low", "-medium", "-high", "-on")


def _canonical_from_old_id(old_id):
    """Map an OLD 5-part cache id to its canonical id, or None if not the old
    scheme (already-canonical ids have 4 pipe-parts and are skipped)."""
    if not isinstance(old_id, str):
        return None
    parts = old_id.split("|")
    if len(parts) != 5:
        return None  # already canonical (4 parts) or unexpected — skip
    schema, article_hash, _provider, model_ref, prompt_version = parts
    model, effort = model_ref, ""
    low = model_ref.lower()
    for suf in _EFFORT_SUFFIXES:
        if low.endswith(suf):
            effort = suf[1:]
            model = model_ref[: -len(suf)]
            break
    base = _auto_normalize_model(model)
    canonical = f"{base}@{effort}" if effort else base
    return f"{schema}|{article_hash}|{canonical}|{prompt_version}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="REDACTED-HOST")
    ap.add_argument("--port", type=int, default=28015)
    ap.add_argument("--db", default="IntelliStock")
    ap.add_argument("--apply", action="store_true", help="write changes (default: dry-run)")
    ap.add_argument("--cleanup", action="store_true", help="delete old ids after re-keying")
    args = ap.parse_args()

    from rethinkdb import RethinkDB
    r = RethinkDB()
    conn = r.connect(host=args.host, port=args.port, db=args.db, timeout=10)
    existing = set(r.db(args.db).table_list().run(conn))
    for table in TABLES:
        if table not in existing:
            print(f"{table}: not present, skipping")
            continue
        # Materialize first (don't iterate a cursor while writing to the same
        # table), then batch — these tables can hold tens of thousands of rows.
        rows = list(r.db(args.db).table(table).run(conn))
        new_rows = []
        old_ids = []
        skipped = 0
        for row in rows:
            old_id = row.get("id")
            new_id = _canonical_from_old_id(old_id)
            if not new_id or new_id == old_id:
                skipped += 1
                continue
            if not args.apply:
                print(f"  {old_id}  ->  {new_id}")
            nr = dict(row)
            nr["id"] = new_id
            new_rows.append(nr)
            old_ids.append(old_id)
        if args.apply:
            for i in range(0, len(new_rows), _BATCH):
                r.db(args.db).table(table).insert(new_rows[i:i + _BATCH], conflict="replace").run(conn)
            if args.cleanup:
                # Old ids are 5-part (carry a provider segment); new ids are
                # 4-part — they can never collide, so deleting old ids never
                # removes a row we just wrote.
                for i in range(0, len(old_ids), _BATCH):
                    r.db(args.db).table(table).get_all(*old_ids[i:i + _BATCH]).delete().run(conn)
        print(f"{table}: {len(new_rows)} re-keyed, {skipped} unchanged "
              f"({'APPLIED' if args.apply else 'DRY-RUN'}{', cleaned' if args.cleanup and args.apply else ''})")
    conn.close()


if __name__ == "__main__":
    main()
