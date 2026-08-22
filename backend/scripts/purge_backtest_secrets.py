"""Purge plaintext credentials from historical BacktestResults.strategy_schema.

Dry-run by default: mutation requires BOTH ``--apply`` and
``--confirm-table BacktestResults``. Output contains row IDs, redaction counts,
and status only — never old or new secret-bearing payloads.

Production execution additionally requires a fresh encrypted database backup
governed by the same secret-access controls (operator responsibility; this
script cannot verify it).

Usage (repo root, .env supplies RETHINKDB_HOST):
    python3 -m scripts.purge_backtest_secrets                # dry run
    python3 -m scripts.purge_backtest_secrets --apply --confirm-table BacktestResults
"""
import argparse
import json
import os
import sys

_BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_ROOT not in sys.path:
    sys.path.insert(0, _BACKEND_ROOT)

from persistence_safety import REDACTION_MARKER, assert_secret_free, sanitize_snapshot

TABLE = "BacktestResults"
BATCH_SIZE = 100


def _count_redactions(value):
    if isinstance(value, dict):
        if value.get("redacted") is True and value.get("source") == "runtime_secret":
            return 1
        return sum(_count_redactions(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return sum(_count_redactions(v) for v in value)
    if isinstance(value, str):
        return value.count("<redacted>")
    return 0


def sanitize_backtest_row(row):
    """Return (patch, redacted_field_count) for one BacktestResults row.

    The patch replaces only ``strategy_schema``; rows without one (or already
    clean rows) produce an empty patch.
    """
    schema = (row or {}).get("strategy_schema")
    if schema is None:
        return {}, 0
    clean = sanitize_snapshot(schema)
    # Only NEW redactions count: an already-purged row (markers in place) is
    # an idempotent no-op, not perpetually "dirty" (audit 2026-07-18).
    count = _count_redactions(clean) - _count_redactions(schema)
    if count <= 0:
        return {}, 0
    return {"strategy_schema": clean}, count


class RethinkBackend:
    """Streaming primary-key iteration over the production table."""

    def __init__(self, host=None, port=None):
        try:
            from dotenv import load_dotenv
            load_dotenv(os.path.join(os.path.dirname(_BACKEND_ROOT), ".env"))
        except Exception:
            pass
        from db import store as _store
        self._r = _store
        self._conn = None
        # Audit 2026-07-18: defaulted to "test" while every other module
        # (interactive_utils, server, rethink_store) defaults "IntelliStock".
        self._db = os.environ.get("RETHINKDB_DB", "IntelliStock")

    def _table(self):
        return TABLE

    def iter_rows(self, batch_size):
        last_id = None
        while True:
            sel = self._table()
            if last_id is not None:
                sel = self._r.between(sel, last_id, self._r.MAXVAL,
                                      index="id", left_bound="open")
            batch = list(self._r.pluck(self._r.run(self._r.limit(
                self._r.order_by(sel, index="id"), batch_size)),
                "id", "strategy_schema"))
            if not batch:
                return
            for row in batch:
                yield row
            last_id = batch[-1]["id"]

    def update_row(self, row_id, patch, *, durability):
        # r.literal prevents ReQL's recursive object merge from RETAINING
        # nested secret fields under the redaction marker (audit 2026-07-18).
        wrapped = {key: self._r.Literal(value) if isinstance(value, dict) else value
                   for key, value in patch.items()}
        self._r.update(self._table(), row_id, wrapped)

    def get_row(self, row_id):
        row = self._r.get(self._table(), row_id)
        return self._r.pluck([row] if row else [], "id", "strategy_schema")[0] \
            if row else None


def main(argv=None, backend=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true",
                        help="Write sanitized schemas. Default is dry-run (read-only).")
    parser.add_argument("--confirm-table", default=None,
                        help=f"Must be exactly '{TABLE}' for --apply to proceed.")
    parser.add_argument("--batch-size", type=int, default=BATCH_SIZE)
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", default=None)
    args = parser.parse_args(argv)

    if args.apply and args.confirm_table != TABLE:
        print(f"REFUSED: --apply requires --confirm-table {TABLE}. No rows were read or written.",
              file=sys.stderr)
        return 2

    if backend is None:
        backend = RethinkBackend(host=args.host, port=args.port)

    mode = "APPLY" if args.apply else "DRY-RUN"
    scanned = 0
    dirty = []
    for row in backend.iter_rows(args.batch_size):
        scanned += 1
        patch, count = sanitize_backtest_row(row)
        if count == 0:
            continue
        dirty.append((row["id"], count))
        print(f"[{mode}] row={row['id']} redacted_fields={count}")
        if args.apply:
            backend.update_row(row["id"], patch, durability="hard")
            verified = backend.get_row(row["id"])
            assert_secret_free((verified or {}).get("strategy_schema"))
            print(f"[{mode}] row={row['id']} verified secret-free after write")

    total = sum(count for _, count in dirty)
    print(f"[{mode}] scanned={scanned} rows_with_secrets={len(dirty)} "
          f"redacted_fields={total} updates_written={len(dirty) if args.apply else 0}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
