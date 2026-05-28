"""Operator action: adopt an external broker position as strategy-owned by
writing a synthetic LiveOrderWAL row.

WHY THIS IS NEEDED:
Clean-room mode (broker_adapters/_classifier.py) treats any broker
position without a matching filled WAL row as EXTERNAL — quarantined,
never sold by the strategy. If you have a pre-existing position you
WANT the strategy to manage, this script writes a synthetic WAL row
(status=filled, source=migrated) so the next adapter refresh classifies
it as strategy-owned with the entry price you specify.

Usage:
  RETHINKDB_HOST=REDACTED-IP python3 scripts/migrate_external_position.py \\
    --instance main --ticker AAPL --qty 50.0 --avg-price 184.70           # dry-run
  RETHINKDB_HOST=REDACTED-IP python3 scripts/migrate_external_position.py \\
    --instance main --ticker AAPL --qty 50.0 --avg-price 184.70 --apply   # write
"""
from __future__ import annotations

import argparse
import os
import sys
import uuid
from datetime import datetime, timezone

# Match backend/api/main.py: load .env so operator doesn't have to export by hand.
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_BACKEND_DIR = os.path.join(_REPO_ROOT, "backend")
try:
    from dotenv import load_dotenv as _load_dotenv
    _load_dotenv(os.path.join(_BACKEND_DIR, ".env"))
    _load_dotenv(os.path.join(_REPO_ROOT, ".env"))
except ImportError:
    pass


def _connect_db():
    from rethinkdb import RethinkDB
    r = RethinkDB()
    host = os.environ.get("RETHINKDB_HOST", "localhost")
    port = int(os.environ.get("RETHINKDB_PORT", "28015"))
    return r, r.connect(host=host, port=port, db="IntelliStock")


def _derive_cid_prefix(instance_id: str) -> str:
    """Mirror broker_adapters/_client_order_id.py::_safe(instance, 8)."""
    import re
    safe = re.sub(r"[^A-Za-z0-9]", "", instance_id or "")[:8] or "x"
    return f"{safe}-"


def main():
    p = argparse.ArgumentParser(
        description="Adopt a pre-existing broker position as strategy-owned"
    )
    p.add_argument("--instance", required=True, help="Instances row id (e.g. main)")
    p.add_argument("--ticker", required=True, help="Symbol to adopt (e.g. AAPL)")
    p.add_argument("--qty", type=float, required=True, help="Shares to adopt")
    p.add_argument("--avg-price", type=float, required=True, help="Entry price for this position")
    p.add_argument(
        "--apply",
        action="store_true",
        help="Actually insert the row (default: dry-run prints what would be written)",
    )
    args = p.parse_args()

    cid_prefix = _derive_cid_prefix(args.instance)
    cid = f"{cid_prefix}MIGRATED-{uuid.uuid4().hex[:8]}"
    now = datetime.now(timezone.utc)
    row = {
        "id": cid,                          # primary key on LiveOrderWAL is the cid
        "client_order_id": cid,
        "broker_order_id": f"MIGRATED-{uuid.uuid4().hex[:12]}",
        "state": "filled",
        "symbol": args.ticker.upper(),
        "side": "BUY",
        "qty": float(args.qty),
        "notional": float(args.qty) * float(args.avg_price),
        "filled_qty": float(args.qty),
        "filled_avg_price": float(args.avg_price),
        "reject_reason": None,
        "created_at_utc": now.isoformat(),
        "updated_at_utc": now.isoformat(),
        # Non-standard fields for forensics:
        "source": "migrated",
        "instance_id": args.instance,
        "note": (
            "Synthetic LiveOrderWAL row written by migrate_external_position.py "
            "to adopt a pre-existing broker position into strategy management. "
            "On next adapter refresh, classifier matches this row against the "
            "broker position and moves it from _external_positions into _positions."
        ),
    }

    print("Will insert into LiveOrderWAL:")
    for k in sorted(row):
        print(f"  {k} = {row[k]!r}")
    if not args.apply:
        print("\nDRY RUN. Re-run with --apply to write.")
        return 0

    r, conn = _connect_db()
    try:
        res = r.table("LiveOrderWAL").insert(row, conflict="error").run(conn)
        print(f"\nInserted: {res}")
        if (res or {}).get("errors", 0) > 0:
            print(
                "Insert reported errors — likely a duplicate cid. "
                "Re-run with a unique --ticker or wait a moment for the uuid to differ.",
                file=sys.stderr,
            )
            return 1
        return 0
    finally:
        try:
            conn.close()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
