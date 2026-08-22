"""Read-only pre-flight inspector for a live trading instance.

Connects to Postgres to resolve the instance's brokerage + WAL, then
queries the brokerage account directly to print exactly what an adapter
boot under ``clean_room_mode=True`` WOULD adopt as strategy-owned vs
quarantine as external. Does NOT boot the broker daemon, submit orders,
or mutate any DB / brokerage state.

Usage:
  python3 scripts/inspect_broker_state.py --instance main

Exit codes:
  0  -- successfully inspected and printed report (broker may still be dirty)
  2  -- instance / brokerage not found, or unknown brokerage_type
  3  -- DB connection failure
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone

# Load env (incl. INTELLISTOCK_CRED_KEY for credential decryption) from .env
# files BEFORE the os.environ checks below. Mirrors backend/api/main.py:15-17
# so the inspector uses the same key the broker daemon uses.
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_BACKEND_DIR = os.path.join(_REPO_ROOT, "backend")
try:
    from dotenv import load_dotenv as _load_dotenv
    _load_dotenv(os.path.join(_BACKEND_DIR, ".env"))
    _load_dotenv(os.path.join(_REPO_ROOT, ".env"))
except ImportError:
    # python-dotenv not installed; operator must export the env var manually.
    pass


def _connect_db():
    """R26: the store pools its own connection per operation."""
    from db import store as _store
    return _store, None


def _resolve_instance(r, conn, instance_id: str):
    inst = r.get("Instances", instance_id)
    if inst is None:
        return None, None
    brokerage_id = inst.get("brokerage_id")
    if not brokerage_id:
        return inst, None
    bra = r.get("BrokerageAccounts", brokerage_id)
    return inst, bra


def _scan_wal_rows_for_instance(r, conn, cid_prefix: str, retention_days: int) -> list[dict]:
    """Pull WAL rows for the instance's CID prefix. Naive scan (no index)
    — acceptable for a single-instance WAL with thousands of rows."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).isoformat()
    out: list[dict] = []
    for row in r.iter("LiveOrderWAL"):
        cid = row.get("client_order_id") or ""
        if not cid.startswith(cid_prefix):
            continue
        if not row.get("filled_qty"):
            continue
        ts = row.get("updated_at_utc") or row.get("created_at_utc")
        if ts and ts < cutoff:
            continue
        out.append(row)
    return out


def _fetch_broker_positions_and_cash(brokerage_row: dict):
    """Read-only broker call: positions + cash + open orders. Mirrors the
    adapter's REST refresh path without booting the adapter.

    IMPORTANT: BrokerageAccounts rows store Fernet-encrypted credentials per
    backend/secret_store.py. broker.py decrypts them at boot via
    _load_live_credentials_from_db (alpaca_key, alpaca_secret). We replicate
    that here so the broker API call uses the actual secret, not Fernet
    ciphertext. Requires INTELLISTOCK_CRED_KEY env to be set.
    """
    btype = (brokerage_row.get("brokerage_type") or "").lower()
    if btype == "alpaca":
        try:
            from alpaca.trading.client import TradingClient
        except ImportError as e:
            raise SystemExit(
                f"alpaca-py not installed; cannot inspect Alpaca broker state ({e})."
            )
        # Stock inspection obeys the same strict encrypted boundary as runtime.
        from secret_store import decrypt_required
        _ak = decrypt_required(
            brokerage_row.get("alpaca_key"),
            field="alpaca_key",
        )
        _as = decrypt_required(
            brokerage_row.get("alpaca_secret"),
            field="alpaca_secret",
        )
        client = TradingClient(
            api_key=_ak,
            secret_key=_as,
            paper=bool(brokerage_row.get("alpaca_paper", True)),
        )
        positions = client.get_all_positions() or []
        account = client.get_account()
        cash = float(getattr(account, "cash", 0.0) or 0.0)
        equity = float(getattr(account, "equity", 0.0) or 0.0)
        return (
            [
                {
                    "symbol": p.symbol,
                    "qty": float(p.qty),
                    "market_value": float(p.market_value or 0.0),
                }
                for p in positions
            ],
            cash,
            equity,
        )
    raise SystemExit(f"unknown brokerage_type {btype!r} on this instance")


def main():
    p = argparse.ArgumentParser(description="Read-only pre-flight inspector for live instances")
    p.add_argument("--instance", required=True, help="Instances row id (e.g. main)")
    p.add_argument("--retention-days", type=int, default=180)
    args = p.parse_args()

    # Make backend importable (for secret_store + classifier).
    # Must happen BEFORE any decryption call.
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

    # The decrypt helper requires INTELLISTOCK_CRED_KEY; without it the broker
    # tokens cannot be decrypted and any subsequent broker API call will 401.
    if not os.environ.get("INTELLISTOCK_CRED_KEY"):
        print(
            "ERROR: INTELLISTOCK_CRED_KEY env var is not set. The brokerage credentials "
            "are Fernet-encrypted in the DB and cannot be decrypted without it. "
            "Export the same INTELLISTOCK_CRED_KEY the broker daemon uses, e.g.:\n"
            "    export INTELLISTOCK_CRED_KEY=<base64-Fernet-key>\n"
            "    python3 scripts/inspect_broker_state.py --instance main",
            file=sys.stderr,
        )
        return 3

    try:
        r, conn = _connect_db()
    except Exception as e:
        print(f"ERROR: DB connect failed: {e}", file=sys.stderr)
        return 3
    try:
        inst, bra = _resolve_instance(r, conn, args.instance)
        if inst is None:
            print(f"ERROR: Instances row '{args.instance}' not found", file=sys.stderr)
            return 2
        if bra is None:
            print(f"ERROR: brokerage_id missing on Instances row '{args.instance}'", file=sys.stderr)
            return 2

        from broker_adapters._classifier import classify_broker_positions, derive_cid_prefix

        cid_prefix = derive_cid_prefix(args.instance)

        print(f"Instance: {args.instance}  (broker: {bra.get('brokerage_type')})")
        print(
            f"Account:  {bra.get('alpaca_account_number')}"
            f"  ({bra.get('account_name')})"
        )
        if (bra.get("brokerage_type") or "").lower() == "alpaca":
            print(f"Paper:    {bra.get('alpaca_paper')}")

        # WAL scan
        wal_rows = _scan_wal_rows_for_instance(r, conn, cid_prefix, args.retention_days)
        print(
            f"LiveOrderWAL filled rows (last {args.retention_days}d, prefix {cid_prefix!r}): "
            f"{len(wal_rows)}"
        )

        # Broker reality
        positions, cash, equity = _fetch_broker_positions_and_cash(bra)
        print(f"Broker cash:     ${cash:,.2f}")
        print(f"Broker equity:   ${equity:,.2f}")
        print(f"Broker positions: {len(positions)} total")

        # Classify
        owned, external, _trades = classify_broker_positions(
            positions=positions,
            wal_rows=wal_rows,
            instance_id=args.instance,
            cid_prefix=cid_prefix,
            retention_days=args.retention_days,
        )

        for sym, qty in sorted(owned.items()):
            print(f"  - {sym:<6}  {qty:>10.4f}sh   STRATEGY-OWNED  (matched WAL)")
        for sym, info in sorted(external.items()):
            print(
                f"  - {sym:<6}  {info['qty']:>10.4f}sh   EXTERNAL        "
                f"({info.get('note', '')})"
            )

        print()
        print(
            f"Verdict: under clean_room_mode=True, the strategy would adopt "
            f"{len(owned)} position(s) and quarantine {len(external)}. "
            "Manually flatten any externals via the broker UI if you want them "
            "gone before boot, or use scripts/migrate_external_position.py "
            "to adopt one as strategy-owned."
        )
        return 0
    finally:
        try:
            conn.close()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
