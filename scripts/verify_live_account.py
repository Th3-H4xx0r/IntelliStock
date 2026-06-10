"""One-shot pre-live Robinhood account verifier.

Read-only. Decrypts the BrokerageAccounts row for the 'main' instance,
calls Robinhood for account/portfolio/positions/orders, and prints a
verdict. Does NOT cancel, refresh-to-disk, or submit anything.

Usage:
    python3 scripts/verify_live_account.py
"""
from __future__ import annotations

import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_BACKEND_DIR = os.path.join(_REPO_ROOT, "backend")
try:
    from dotenv import load_dotenv as _load_dotenv
    _load_dotenv(os.path.join(_BACKEND_DIR, ".env"))
    _load_dotenv(os.path.join(_REPO_ROOT, ".env"))
except ImportError:
    pass

sys.path.insert(0, _BACKEND_DIR)

# Operator-specific identifiers come from the environment — nothing
# operator-specific is baked into this open-source script.
BROKERAGE_ID = os.environ.get("VERIFY_BROKERAGE_ID", "").strip()
EXPECTED_ACCOUNT = os.environ.get("VERIFY_EXPECTED_ACCOUNT", "").strip()
# App-wide constant (mirrors backend/broker_adapters/robinhood.py): the
# deterministic namespace used to derive a Robinhood ref_id from a client
# order id. Not operator-specific.
_RH_REF_NAMESPACE = uuid.UUID("a3c9f2e4-5d6b-4f8c-9d2e-1f3a5b7c9d1e")


def _connect():
    from rethinkdb import RethinkDB
    r = RethinkDB()
    host = os.environ.get("RETHINKDB_HOST", "localhost")
    port = int(os.environ.get("RETHINKDB_PORT", "28015"))
    conn = r.connect(host=host, port=port, db="IntelliStock")
    return r, conn


def _decrypt(v):
    from secret_store import decrypt
    if v is None or v == "":
        return v
    try:
        return decrypt(v)
    except Exception:
        return v


def _ref_id_from_cid(cid: str) -> str:
    return str(uuid.uuid5(_RH_REF_NAMESPACE, cid or ""))


def main():
    if not os.environ.get("INTELLISTOCK_CRED_KEY"):
        print("ERROR: INTELLISTOCK_CRED_KEY missing")
        return 3
    if not BROKERAGE_ID:
        print("ERROR: VERIFY_BROKERAGE_ID missing (the BrokerageAccounts row id to verify)")
        return 3

    r, conn = _connect()
    try:
        bra = r.table("BrokerageAccounts").get(BROKERAGE_ID).run(conn)
        if bra is None:
            print(f"ERROR: brokerage row {BROKERAGE_ID} not found")
            return 2

        print("=" * 72)
        print("STAGE 1 -- DB row")
        print("=" * 72)
        print(f"  brokerage_type      : {bra.get('brokerage_type')}")
        print(f"  account_name        : {bra.get('account_name')}")
        print(f"  account_number (DB) : {bra.get('robinhood_account_number')}")
        print(f"  account_url (DB)    : {bra.get('robinhood_account_url')}")
        print(f"  status              : {bra.get('status')}")
        print(f"  last_error          : {bra.get('last_error')}")
        print(f"  last_refresh_at     : {bra.get('last_refresh_at')}")

        obtained_at_epoch = int(bra.get("robinhood_obtained_at_epoch") or 0)
        expires_in = int(bra.get("robinhood_expires_in") or 0)
        now_epoch = int(time.time())
        if obtained_at_epoch:
            obtained_iso = datetime.fromtimestamp(obtained_at_epoch, tz=timezone.utc).isoformat()
        else:
            obtained_iso = "(unset)"
        expires_at_epoch = obtained_at_epoch + expires_in
        if expires_at_epoch:
            expires_iso = datetime.fromtimestamp(expires_at_epoch, tz=timezone.utc).isoformat()
        else:
            expires_iso = "(unset)"
        secs_remaining = expires_at_epoch - now_epoch
        print(f"  token obtained_at   : {obtained_iso}")
        print(f"  token expires_in    : {expires_in}s  (expires_at {expires_iso})")
        print(f"  token secs_remaining: {secs_remaining}  (~{secs_remaining/3600:.2f}h)")
        print(f"  last_refresh_epoch  : {bra.get('_last_refresh_epoch')}")

        access = _decrypt(bra.get("robinhood_access_token"))
        refresh = _decrypt(bra.get("robinhood_refresh_token"))
        device = _decrypt(bra.get("robinhood_device_token"))
        print(f"  access_token len    : {len(access) if access else 0}  "
              f"({'fernet-tagged' if str(bra.get('robinhood_access_token') or '').startswith('fernet:') else 'plaintext-or-other'})")
        print(f"  refresh_token len   : {len(refresh) if refresh else 0}")
        print(f"  device_token        : {device[:8]+'...' if device else '(none)'}")

        if not access or not refresh:
            print("\nERROR: access/refresh empty after decrypt — cannot probe API")
            return 2

        print()
        print("=" * 72)
        print("STAGE 2 -- Live API probe (read-only)")
        print("=" * 72)

        from robinhood_engine import (
            RobinhoodClient,
            RobinhoodSessionState,
            RobinhoodAPIError,
        )

        state = RobinhoodSessionState(
            access_token=access,
            refresh_token=refresh,
            token_type=bra.get("robinhood_token_type", "Bearer"),
            device_token=device or None,
            account_number=(bra.get("robinhood_account_number") or "").strip() or None,
            account_url=(bra.get("robinhood_account_url") or "").strip() or None,
            obtained_at_epoch=obtained_at_epoch,
            expires_in=expires_in,
        )
        client = RobinhoodClient(state=state, timeout_sec=20)

        # 2a. Account summary
        try:
            summary = client.get_account_summary(
                account_number=bra.get("robinhood_account_number")
            ) or {}
        except RobinhoodAPIError as e:
            print(f"ERROR calling get_account_summary: HTTP {e.status_code} {e}")
            return 2

        print("\n[account_summary]")
        for k, v in summary.items():
            print(f"  {k:<26}: {v}")

        # 2b. Raw account dict (for fields summary doesn't expose)
        try:
            accounts = client.get_accounts() or []
        except RobinhoodAPIError as e:
            print(f"ERROR calling get_accounts: HTTP {e.status_code} {e}")
            accounts = []

        raw_acct = None
        for a in accounts:
            if (a.get("account_number") or "").strip() == (bra.get("robinhood_account_number") or "").strip():
                raw_acct = a
                break
        if raw_acct is None and accounts:
            raw_acct = accounts[0]

        if raw_acct:
            print("\n[raw account dict — selected fields]")
            for k in [
                "account_number", "type", "deactivated", "state",
                "pattern_day_trader", "day_trade_count", "day_trade_buying_power",
                "buying_power", "cash", "cash_available_for_withdrawal",
                "cash_held_for_orders", "unsettled_funds",
                "uncleared_deposits", "unsettled_debit",
                "sma", "sma_held_for_orders",
                "only_position_closing_trades", "instant_eligibility",
                "instant_used", "instant_allocated", "max_ach_early_access_amount",
                "margin_balances", "cash_balances", "is_pinnacle_account",
                "option_level", "rhs_account_number", "withdrawal_halted",
                "deposit_halted", "sweep_enabled", "drip_enabled",
                "portfolio_cash", "user_id", "display_name", "management_type",
            ]:
                if k in raw_acct:
                    val = raw_acct[k]
                    # truncate margin_balances/cash_balances dict for readability
                    if isinstance(val, dict):
                        val = json.dumps(val, default=str)[:600]
                    print(f"  {k:<32}: {val}")

        # 2c. Portfolio
        try:
            portfolio = client.get_portfolio(
                account_number=bra.get("robinhood_account_number")
            ) or {}
        except RobinhoodAPIError as e:
            print(f"WARN get_portfolio failed: HTTP {e.status_code} {e}")
            portfolio = {}

        if portfolio:
            print("\n[portfolio — selected fields]")
            for k in [
                "equity", "market_value", "extended_hours_equity",
                "last_core_equity", "last_core_market_value",
                "withdrawable_amount", "unwithdrawable_deposits",
                "unwithdrawable_grants", "excess_margin", "excess_maintenance",
            ]:
                if k in portfolio:
                    print(f"  {k:<32}: {portfolio[k]}")

        # 2d. Positions (full — including zero qty closed)
        try:
            positions_open = client.get_positions(
                account_number=bra.get("robinhood_account_number"), nonzero=True
            ) or []
        except RobinhoodAPIError as e:
            print(f"WARN get_positions(nonzero=True) failed: HTTP {e.status_code} {e}")
            positions_open = []

        print(f"\n[positions (nonzero=True)]: {len(positions_open)} open")
        for p in positions_open:
            print(f"  qty={p.get('quantity')} avg={p.get('average_buy_price')} "
                  f"instrument={p.get('instrument')}")

        # 2e. Orders — last 20
        try:
            recent_orders = client.list_orders(
                limit=20,
                account_number=bra.get("robinhood_account_number"),
            ) or []
        except RobinhoodAPIError as e:
            print(f"WARN list_orders failed: HTTP {e.status_code} {e}")
            recent_orders = []

        # Also open orders specifically
        try:
            open_orders = client.list_orders(
                limit=50, state="open",
                account_number=bra.get("robinhood_account_number"),
            ) or []
        except RobinhoodAPIError as e:
            print(f"WARN list_orders(state=open) failed: HTTP {e.status_code} {e}")
            open_orders = []

        print(f"\n[open orders]: {len(open_orders)}")
        for o in open_orders:
            print(f"  id={o.get('id')} state={o.get('state')} side={o.get('side')} "
                  f"qty={o.get('quantity')} type={o.get('type')} symbol_inst={o.get('instrument')}")

        # 2f. WAL scan for instance main
        print(f"\n[WAL scan — cid prefix 'main-']")
        wal_rows = []
        try:
            for row in r.table("LiveOrderWAL").run(conn):
                cid = (row.get("client_order_id") or "")
                if cid.startswith("main-"):
                    wal_rows.append(row)
        except Exception as e:
            print(f"WARN WAL scan failed: {e}")
        print(f"  total main- WAL rows: {len(wal_rows)}")
        # Show most recent 5
        wal_rows_sorted = sorted(
            wal_rows,
            key=lambda x: x.get("updated_at_utc") or x.get("created_at_utc") or "",
            reverse=True,
        )
        for row in wal_rows_sorted[:5]:
            print(f"    cid={row.get('client_order_id')[:32]:<32} "
                  f"sym={row.get('symbol')} side={row.get('side')} "
                  f"state={row.get('state')} filled_qty={row.get('filled_qty')} "
                  f"updated={row.get('updated_at_utc')}")

        # Build ref_id -> wal row lookup
        wal_by_refid = {}
        for row in wal_rows:
            cid = row.get("client_order_id") or ""
            if cid:
                wal_by_refid[_ref_id_from_cid(cid)] = row

        # 2g. Reconcile last 20 RH orders with WAL
        print(f"\n[last {len(recent_orders)} RH orders -- CID reconciliation]")
        print("=" * 72)
        phantom_count = 0
        strategy_count = 0
        for i, o in enumerate(recent_orders, 1):
            rh_ref_id = (o.get("ref_id") or "").strip().lower()
            wal_row = wal_by_refid.get(rh_ref_id)
            origin = "STRATEGY" if wal_row else "PHANTOM/MANUAL"
            if wal_row:
                strategy_count += 1
            else:
                phantom_count += 1
            inst_url = (o.get("instrument") or "").strip()
            # Quick symbol resolution from cache if available
            print(f"  #{i:2d} {o.get('created_at', '')[:19]} {o.get('state','?'):<14} "
                  f"side={o.get('side','?'):<4} qty={(o.get('quantity') or '?'):>10} "
                  f"avg_px={o.get('average_price', '?')!s:>9}  "
                  f"oid={(o.get('id') or '')[:8]}  "
                  f"ref={rh_ref_id[:8]}  {origin}")
            if wal_row:
                print(f"         └─ WAL cid={wal_row.get('client_order_id')[:36]} "
                      f"sym={wal_row.get('symbol')}")

        print(f"\n  STRATEGY-OWNED orders: {strategy_count}")
        print(f"  PHANTOM/MANUAL orders: {phantom_count}")

        # 2h. Refresh-path probe (do NOT persist!)
        print("\n[refresh path probe -- in-memory only, NOT persisted to DB]")
        try:
            # Save current tokens; restore after probe
            saved_access = client.state.access_token
            saved_refresh = client.state.refresh_token
            saved_expires_in = client.state.expires_in
            saved_obtained = client.state.obtained_at_epoch
            new_state = client.refresh()
            new_access_changed = new_state.access_token != saved_access
            new_refresh_changed = new_state.refresh_token != saved_refresh
            print(f"  refresh()     : OK")
            print(f"  access rotated: {new_access_changed}")
            print(f"  refresh rotated: {new_refresh_changed}")
            print(f"  new expires_in: {new_state.expires_in}s  "
                  f"(~{(new_state.expires_in or 0)/3600:.2f}h)")
            print(f"  new obtained_at: {datetime.fromtimestamp(new_state.obtained_at_epoch, tz=timezone.utc).isoformat()}")
            # We do not write back to DB because the credential service will
            # do it on its next cycle. But note the rotated tokens here so
            # the operator knows whether the cred service should be
            # bounced before launch.
        except Exception as e:
            print(f"  refresh()     : FAILED — {type(e).__name__}: {e}")

        # 2i. Misc safety queries
        try:
            ach_relationships = client._request_json(
                "GET",
                "https://api.robinhood.com/ach/relationships/",
                timeout_sec=15,
            )
            ach_res = ach_relationships.get("results") or []
            print(f"\n[ACH relationships]: {len(ach_res)}")
            for ach in ach_res:
                print(f"  id={ach.get('id')[:8] if ach.get('id') else '?'} "
                      f"verified={ach.get('verified')} unlink_request={ach.get('unlink_request')} "
                      f"bank={ach.get('bank_account_nickname')} verify_method={ach.get('verify_method')}")
        except Exception as e:
            print(f"\n[ACH relationships]: WARN {type(e).__name__}: {e}")

        try:
            # ACH transfers — show last 5 to spot any pending withdrawals
            ach_xfers = client._request_json(
                "GET",
                "https://api.robinhood.com/ach/transfers/",
                params={"account_numbers": bra.get("robinhood_account_number")},
                timeout_sec=15,
            )
            ach_xfer_res = ach_xfers.get("results") or []
            print(f"\n[ACH transfers]: total fetched={len(ach_xfer_res)} (showing last 5)")
            for x in ach_xfer_res[:5]:
                print(f"  id={x.get('id')[:8] if x.get('id') else '?'} "
                      f"dir={x.get('direction')} amount={x.get('amount')} "
                      f"state={x.get('state')} created={x.get('created_at','')[:19]} "
                      f"expected_landing={x.get('expected_landing_date')}")
        except Exception as e:
            print(f"\n[ACH transfers]: WARN {type(e).__name__}: {e}")

        try:
            divs = client._request_json(
                "GET",
                "https://api.robinhood.com/dividends/",
                params={"account_numbers": bra.get("robinhood_account_number")},
                timeout_sec=15,
            )
            div_res = divs.get("results") or []
            pending = [d for d in div_res if (d.get("state") or "").lower() in ("pending", "reinvested", "voided") or (d.get("paid_at") is None and d.get("state") != "paid")]
            print(f"\n[dividends]: total fetched={len(div_res)} pending/non-paid={len(pending)}")
            for d in pending[:5]:
                print(f"  id={(d.get('id') or '')[:8]} state={d.get('state')} "
                      f"amount={d.get('amount')} rate={d.get('rate')} "
                      f"payable={d.get('payable_date')} paid_at={d.get('paid_at')}")
        except Exception as e:
            print(f"\n[dividends]: WARN {type(e).__name__}: {e}")

        # 2j. Save a JSON dump so we can inspect later if needed
        dump = {
            "now_utc": datetime.now(timezone.utc).isoformat(),
            "brokerage_id": BROKERAGE_ID,
            "expected_account": EXPECTED_ACCOUNT,
            "db_row_snapshot": {
                k: bra.get(k) for k in [
                    "id", "brokerage_type", "account_name", "robinhood_account_number",
                    "robinhood_account_url", "status", "last_error", "last_refresh_at",
                    "robinhood_obtained_at_epoch", "robinhood_expires_in",
                    "_last_refresh_epoch", "updated_at",
                ]
            },
            "account_summary": summary,
            "raw_account_dict": raw_acct,
            "portfolio": portfolio,
            "positions_open": positions_open,
            "open_orders": open_orders,
            "recent_orders": recent_orders,
            "wal_rows_count": len(wal_rows),
        }
        dump_path = "/tmp/verify_live_account.json"
        with open(dump_path, "w") as fh:
            json.dump(dump, fh, default=str, indent=2)
        print(f"\nJSON dump written to {dump_path}")

        # 2k. Verdict
        print()
        print("=" * 72)
        print("VERDICT")
        print("=" * 72)
        issues = []
        if summary.get("account_blocked"):
            issues.append(f"account_blocked=True (state={summary.get('state')})")
        if summary.get("trading_blocked"):
            issues.append("trading_blocked=True (only_position_closing_trades)")
        if summary.get("pattern_day_trader") and float(summary.get("equity") or 0) < 25_000:
            issues.append(f"PDT=True + equity ${summary.get('equity'):,.2f} < $25K (PDT-restricted)")
        if EXPECTED_ACCOUNT and (summary.get("account_number") or "").strip() != EXPECTED_ACCOUNT:
            issues.append(f"account_number mismatch: API={summary.get('account_number')} expected={EXPECTED_ACCOUNT}")
        if len(open_orders) > 0:
            issues.append(f"{len(open_orders)} OPEN orders at boot — stragglers")
        if len(positions_open) > 0:
            issues.append(f"{len(positions_open)} OPEN positions — clean-room boot will quarantine or adopt")
        if phantom_count > 0:
            issues.append(f"{phantom_count} of last {len(recent_orders)} RH orders are PHANTOM (no WAL CID match)")
        if secs_remaining < 600:
            issues.append(f"access_token expires in {secs_remaining}s (< 10 min)")

        if not issues:
            print("ACCOUNT-READY-FOR-LIVE")
        else:
            print("READY-WITH-ISSUES" if all(
                not s.startswith(("account_blocked", "trading_blocked", "PDT="))
                for s in issues
            ) else "NOT-READY")
            for s in issues:
                print(f"  - {s}")

        return 0
    finally:
        try:
            conn.close()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
