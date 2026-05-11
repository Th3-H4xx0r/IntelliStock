"""One-shot migration: re-encrypt plaintext credentials in BrokerageAccounts.

Reads every row in the BrokerageAccounts table, identifies fields that aren't
Fernet-tagged (plaintext legacy), and re-writes them as Fernet ciphertext.
Idempotent: running twice is a no-op (already-encrypted rows are skipped).

Prerequisites:
  INTELLISTOCK_CRED_KEY env var set to a Fernet key (base64, 44 chars).
  Generate one via: python -c "from backend.secret_store import generate_key; print(generate_key())"
  Store outside the repo (OS keyring, systemd LoadCredential, AWS KMS, etc).

Usage:
  INTELLISTOCK_CRED_KEY=... python scripts/encrypt_brokerage_credentials.py
  INTELLISTOCK_CRED_KEY=... python scripts/encrypt_brokerage_credentials.py --dry-run

The dry-run prints a per-row summary without writing anything.
"""

from __future__ import annotations

import argparse
import os
import sys


_BACKEND = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

# Load .env files the same way backend/broker.py and backend/credential_service.py
# do so INTELLISTOCK_CRED_KEY can live in either root .env or backend/.env.
try:
    from dotenv import load_dotenv
    _PROJECT_ROOT = os.path.dirname(_BACKEND)
    load_dotenv(os.path.join(_BACKEND, ".env"))
    load_dotenv(os.path.join(_PROJECT_ROOT, ".env"))
except ImportError:
    pass


ENCRYPTED_FIELDS = (
    "alpaca_key",
    "alpaca_secret",
    "robinhood_access_token",
    "robinhood_refresh_token",
    "robinhood_device_token",
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Re-encrypt plaintext BrokerageAccounts credentials using Fernet."
    )
    parser.add_argument("--dry-run", action="store_true", help="Print changes without writing.")
    parser.add_argument("--host", default=os.environ.get("RETHINKDB_HOST", "localhost"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("RETHINKDB_PORT", "28015")))
    args = parser.parse_args(argv)

    if not os.environ.get("INTELLISTOCK_CRED_KEY"):
        print("FATAL: INTELLISTOCK_CRED_KEY env var is required.", file=sys.stderr)
        print(
            "Generate one via: python -c 'from backend.secret_store import generate_key; print(generate_key())'",
            file=sys.stderr,
        )
        return 2

    from rethinkdb import RethinkDB
    from secret_store import encrypt, is_encrypted

    r = RethinkDB()
    conn = r.connect(host=args.host, port=args.port)

    try:
        rows = list(r.db("IntelliStock").table("BrokerageAccounts").run(conn))
    except Exception as e:
        print(f"FATAL: could not read BrokerageAccounts: {e}", file=sys.stderr)
        return 3

    print(f"Found {len(rows)} brokerage account(s).")
    total_encrypted = 0
    total_skipped = 0

    for row in rows:
        rid = row.get("id") or "<no-id>"
        account_name = row.get("account_name") or rid
        patch: dict[str, str] = {}
        for field in ENCRYPTED_FIELDS:
            val = row.get(field)
            if not val:
                continue
            if is_encrypted(val):
                total_skipped += 1
                continue
            try:
                patch[field] = encrypt(val)
            except Exception as e:
                print(f"  [{account_name}] FAILED to encrypt {field}: {e}", file=sys.stderr)
                continue

        if not patch:
            print(f"  [{account_name}] nothing to migrate")
            continue

        fields_list = ", ".join(sorted(patch))
        if args.dry_run:
            print(f"  [{account_name}] would encrypt: {fields_list}")
        else:
            try:
                r.db("IntelliStock").table("BrokerageAccounts").get(rid).update(patch).run(conn)
                total_encrypted += len(patch)
                print(f"  [{account_name}] encrypted: {fields_list}")
            except Exception as e:
                print(f"  [{account_name}] WRITE FAILED: {e}", file=sys.stderr)

    conn.close()
    print(
        f"\nSummary: {total_encrypted} field(s) newly encrypted, "
        f"{total_skipped} already-encrypted field(s) skipped."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
