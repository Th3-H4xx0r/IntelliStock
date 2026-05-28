"""Read-only pre-flight inspector for a live trading instance.

Connects to RethinkDB to resolve the instance's brokerage + WAL, then
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
    from rethinkdb import RethinkDB
    r = RethinkDB()
    host = os.environ.get("RETHINKDB_HOST", "localhost")
    port = int(os.environ.get("RETHINKDB_PORT", "28015"))
    conn = r.connect(host=host, port=port, db="IntelliStock")
    return r, conn


def _resolve_instance(r, conn, instance_id: str):
    inst = r.table("Instances").get(instance_id).run(conn)
    if inst is None:
        return None, None
    brokerage_id = inst.get("brokerage_id")
    if not brokerage_id:
        return inst, None
    bra = r.table("BrokerageAccounts").get(brokerage_id).run(conn)
    return inst, bra


def _scan_wal_rows_for_instance(r, conn, cid_prefix: str, retention_days: int) -> list[dict]:
    """Pull WAL rows for the instance's CID prefix. Naive scan (no index)
    — acceptable for a single-instance WAL with thousands of rows."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).isoformat()
    out: list[dict] = []
    for row in r.table("LiveOrderWAL").run(conn):
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


def _decrypt_or_passthrough(value):
    """Decrypt a Fernet-encrypted secret_store value, or pass through if it's
    plaintext (legacy). Mirrors backend/broker.py:_load_live_credentials_from_db
    + _load_robinhood_extras_from_db which both call secret_store.decrypt
    transparently. Without this, the inspector would feed encrypted ciphertext
    to the broker as a bearer token and get a 401 JWT verification failed.
    """
    if value is None or value == "":
        return value
    # backend/ on sys.path was inserted in main()
    from secret_store import decrypt
    try:
        return decrypt(value)
    except Exception:
        # If decrypt fails, fall back to passthrough (legacy plaintext rows).
        # The broker itself does the same when the field has no fernet: tag.
        return value


def _fetch_broker_positions_and_cash(brokerage_row: dict):
    """Read-only broker call: positions + cash + open orders. Mirrors the
    adapter's REST refresh path without booting the adapter.

    IMPORTANT: BrokerageAccounts rows store Fernet-encrypted credentials per
    backend/secret_store.py. broker.py decrypts them at boot via
    _load_live_credentials_from_db (alpaca_key, alpaca_secret,
    robinhood_access_token, robinhood_refresh_token) and
    _load_robinhood_extras_from_db (robinhood_device_token). We replicate
    that here so the broker API call uses the actual bearer token, not the
    Fernet ciphertext. Requires INTELLISTOCK_CRED_KEY env to be set.
    """
    btype = (brokerage_row.get("brokerage_type") or "").lower()
    if btype == "alpaca":
        try:
            from alpaca.trading.client import TradingClient
        except ImportError as e:
            raise SystemExit(
                f"alpaca-py not installed; cannot inspect Alpaca broker state ({e})."
            )
        # Decrypt Alpaca credentials (see broker.py:629-631)
        _ak = _decrypt_or_passthrough(brokerage_row.get("alpaca_key"))
        _as = _decrypt_or_passthrough(brokerage_row.get("alpaca_secret"))
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
    if btype == "robinhood":
        # Lazy import via backend/ path. Operator must run with PYTHONPATH=backend.
        try:
            from robinhood_engine import RobinhoodClient, RobinhoodSessionState  # type: ignore
        except ImportError as e:
            raise SystemExit(
                f"robinhood_engine import failed ({e}); run from repo root."
            )
        # Decrypt RH credentials (see broker.py:644-645 + 530)
        _access = _decrypt_or_passthrough(brokerage_row.get("robinhood_access_token"))
        _refresh = _decrypt_or_passthrough(brokerage_row.get("robinhood_refresh_token"))
        _device = _decrypt_or_passthrough(brokerage_row.get("robinhood_device_token"))
        if not _access or not _refresh:
            raise SystemExit(
                "Robinhood access_token / refresh_token are empty after decrypt. "
                "Either the brokerage row is missing tokens (re-link via UI), "
                "or INTELLISTOCK_CRED_KEY is set incorrectly in this shell. "
                "Export the same INTELLISTOCK_CRED_KEY the broker daemon uses."
            )
        # Mirror backend/broker.py:_load_robinhood_extras_from_db
        state = RobinhoodSessionState(
            access_token=_access,
            refresh_token=_refresh,
            token_type="Bearer",
            device_token=_device or None,
            account_number=(brokerage_row.get("robinhood_account_number") or "").strip() or None,
            account_url=(brokerage_row.get("robinhood_account_url") or "").strip() or None,
            obtained_at_epoch=int(brokerage_row.get("robinhood_obtained_at_epoch") or 0),
            expires_in=int(brokerage_row.get("robinhood_expires_in") or 0),
        )
        client = RobinhoodClient(state=state, timeout_sec=20)
        positions = client.get_positions(
            account_number=brokerage_row.get("robinhood_account_number")
        ) or []
        account = client.get_account_summary(
            account_number=brokerage_row.get("robinhood_account_number")
        ) or {}
        cash = float(account.get("cash") or 0.0)
        equity = float(account.get("equity") or 0.0)
        # 0-C (bug-sweep 2026-05-28): RH /positions/ dicts have NO 'symbol'
        # field — only an 'instrument' URL. The old `p.get("symbol")` therefore
        # produced "" for every position, so the inspector reported 0 owned /
        # 0 external for EVERY Robinhood instance (a falsely-clean pre-flight).
        # Resolve the symbol via the instrument URL (cached) and normalize to the
        # dash-form the clean-room classifier matches against.
        _inst_sym_cache: dict[str, str] = {}

        def _resolve_symbol(pos: dict) -> str:
            raw = (pos.get("symbol") or "").strip()
            if raw:
                return raw.upper().replace(".", "-")
            url = (pos.get("instrument") or "").strip()
            if not url:
                return ""
            if url not in _inst_sym_cache:
                try:
                    sym = (client.get_instrument(url).get("symbol") or "").strip()
                except Exception:
                    sym = ""
                _inst_sym_cache[url] = sym.upper().replace(".", "-")
            return _inst_sym_cache[url]

        out_positions = []
        for p in positions:
            qty = float(p.get("quantity") or 0.0)
            if qty == 0.0:
                continue
            out_positions.append({
                "symbol": _resolve_symbol(p),
                "qty": qty,
                "market_value": qty * float(p.get("average_buy_price") or 0.0),
            })
        return (out_positions, cash, equity)
    raise SystemExit(f"unknown brokerage_type {btype!r} on this instance")


def main():
    p = argparse.ArgumentParser(description="Read-only pre-flight inspector for live instances")
    p.add_argument("--instance", required=True, help="Instances row id (e.g. main)")
    p.add_argument("--retention-days", type=int, default=180)
    args = p.parse_args()

    # Make backend importable (for secret_store + robinhood_engine + classifier).
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
            f"Account:  {bra.get('alpaca_account_number') or bra.get('robinhood_account_number')}"
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
