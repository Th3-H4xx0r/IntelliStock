"""Copy-verify-switch migration for encrypted credential fields.

The default invocation is read-only.  Database updates require both
``--apply`` and a new, mode-0600 ``--backup-file``; the backup is written
before any rows are switched.  Console output deliberately contains counts
and SHA-256 row-ID hashes, never credential values.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import stat
import sys
from typing import Iterable, Mapping

from credential_audit import AUDITED_TABLES, SECRET_FIELDS_BY_TABLE, scan_secret_fields
from secret_store import decrypt_required, encrypt, is_encrypted
from strategy_secret_boundary import scrub_inline_strategy_secrets


def build_encrypted_patch(row: dict, *, fields: tuple[str, ...]) -> dict:
    """Return the credential-only encrypted replacement patch for one row."""
    patch = {}
    for field in fields:
        value = row.get(field)
        if value in (None, ""):
            continue
        patch[field] = value if is_encrypted(value) else encrypt(str(value))
    return patch


def verify_patch(patch: dict, *, fields: tuple[str, ...]) -> None:
    """Verify every non-empty patch field decrypts as a required secret."""
    for field in fields:
        value = patch.get(field)
        if value in (None, ""):
            continue
        decrypt_required(value, field=field)


def build_strategy_scrub_patch(row: dict) -> dict:
    """Return a patch that removes every legacy inline strategy credential."""
    original = row.get("strategies")
    if not isinstance(original, list):
        return {}
    cleaned = scrub_inline_strategy_secrets(original, reject_material=False)
    return {"strategies": cleaned} if cleaned != original else {}


def _rows_from_db() -> tuple[object, object, dict[str, list[dict]]]:
    """Load the allowlisted tables.  This function is not called in dry tests."""
    from rethinkdb import RethinkDB

    r = RethinkDB()
    conn = r.connect(
        host=os.environ.get("RETHINKDB_HOST", "localhost"),
        port=int(os.environ.get("RETHINKDB_PORT", "28015")),
        timeout=10,
    )
    database = r.db("IntelliStock")
    available = set(database.table_list().run(conn))
    rows = {
        table: list(database.table(table).run(conn)) if table in available else []
        for table in AUDITED_TABLES
    }
    return r, conn, rows


def _load_snapshot(path: Path) -> dict[str, list[dict]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("snapshot must contain an object keyed by table")
    return {
        table: [row for row in rows if isinstance(row, dict)]
        for table, rows in payload.items()
        if isinstance(rows, list)
    }


def _write_backup(path: Path, rows_by_table: Mapping[str, Iterable[dict]]) -> None:
    """Atomically create a private backup before the switch step."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(rows_by_table, handle, sort_keys=True)
            handle.write("\n")
    except Exception:
        try:
            path.unlink()
        except OSError:
            pass
        raise
    if stat.S_IMODE(path.stat().st_mode) != 0o600:
        raise RuntimeError("backup file must have mode 0600")


def _patches(rows_by_table: Mapping[str, Iterable[dict]]) -> list[tuple[str, object, dict]]:
    prepared: list[tuple[str, object, dict]] = []
    for table, fields in SECRET_FIELDS_BY_TABLE.items():
        for row in rows_by_table.get(table, ()):
            patch = build_encrypted_patch(row, fields=fields)
            verify_patch(patch, fields=fields)
            if patch:
                prepared.append((table, row.get("id"), patch))
    for row in rows_by_table.get("Strategies", ()):
        patch = build_strategy_scrub_patch(row)
        if patch:
            prepared.append(("Strategies", row.get("id"), patch))
    return prepared


def _print_inventory(rows_by_table: Mapping[str, Iterable[dict]], *, dry_run: bool) -> None:
    findings = scan_secret_fields(rows_by_table)
    plaintext = sum(not finding.encrypted for finding in findings)
    encrypted = len(findings) - plaintext
    print(json.dumps({
        "mode": "dry-run" if dry_run else "apply",
        "credential_fields": len(findings),
        "plaintext_fields": plaintext,
        "encrypted_fields": encrypted,
        "row_id_hashes": sorted({finding.row_id_hash for finding in findings}),
    }, sort_keys=True))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="perform the switch step")
    parser.add_argument("--backup-file", type=Path, help="new private 0600 backup path required with --apply")
    parser.add_argument("--snapshot", type=Path, help="read rows from JSON instead of RethinkDB (dry-run only)")
    args = parser.parse_args(argv)

    if args.apply and args.backup_file is None:
        parser.error("--apply requires --backup-file")
    if args.apply and args.snapshot is not None:
        parser.error("--snapshot is dry-run only")

    if args.snapshot is not None:
        rows_by_table = _load_snapshot(args.snapshot)
        _print_inventory(rows_by_table, dry_run=True)
        return 0

    r, conn, rows_by_table = _rows_from_db()
    try:
        _print_inventory(rows_by_table, dry_run=not args.apply)
        if not args.apply:
            return 0
        patches = _patches(rows_by_table)
        _write_backup(args.backup_file, rows_by_table)
        for table, row_id, patch in patches:
            r.db("IntelliStock").table(table).get(row_id).update(patch).run(conn)
        print(json.dumps({"mode": "apply", "rows_switched": len(patches)}, sort_keys=True))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(f"migration failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
