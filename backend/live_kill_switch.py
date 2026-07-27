"""Live-trading kill switch.

Halts live trading across all running instances with one call:
1. Flip runCommand=False on all Instances rows
2. For each linked brokerage with an instance currently live, cancel open orders
3. Emit a Discord alert

Safe to call multiple times - idempotent; re-running cancels any newly opened
orders while runCommand is already False.

Usage:
  python -m backend.live_kill_switch
or programmatically:
  from backend.live_kill_switch import halt_live_trading
  halt_live_trading(reason="risk breach: SPY -5% intraday")
"""

from __future__ import annotations

import os
import sys


DB_NAME = "IntelliStock"


def _get_conn():
    from rethinkdb import RethinkDB
    r = RethinkDB()
    host = os.environ.get("RETHINKDB_HOST", "localhost")
    port = int(os.environ.get("RETHINKDB_PORT", "28015"))
    return r, r.connect(host=host, port=port, timeout=10)


def _persist_rh_refreshed_token(r, conn, brokerage_row: dict, client) -> bool:
    """Write a kill-switch-refreshed Robinhood token back to the BrokerageAccounts
    row (Scope D B2) so the DB token doesn't desync after the emergency refresh.

    Raises on encryption or persistence failures.  The caller records a
    non-secret summary error while continuing the emergency cancel retry.
    """
    bid = (brokerage_row or {}).get("id")
    if not bid:
        return False
    from secret_store import encrypt as _encrypt
    st = getattr(client, "state", None)
    access = getattr(st, "access_token", "") or ""
    refresh_tok = getattr(st, "refresh_token", "") or ""
    enc_access = _encrypt(access)
    enc_refresh = _encrypt(refresh_tok)
    import time as _t
    import datetime as _dt
    r.db(DB_NAME).table("BrokerageAccounts").get(bid).update({
        "robinhood_access_token": enc_access,
        "robinhood_refresh_token": enc_refresh,
        "robinhood_obtained_at_epoch": int(getattr(st, "obtained_at_epoch", 0) or int(_t.time())),
        "robinhood_expires_in": int(getattr(st, "expires_in", 0) or 86400),
        "last_refresh_at": _dt.datetime.utcnow().isoformat(),
    }).run(conn)
    return True


def halt_live_trading(
    reason: str = "manual halt",
    cancel_open_orders: bool = True,
    instance_id: str | None = None,
) -> dict:
    """Halt live trading. Returns a summary dict for reporting.

    1-B (bug-sweep 2026-05-28): blast-radius control. When ``instance_id`` is
    None (the manual CLI), this halts EVERY instance and cancels open orders on
    EVERY linked brokerage — the operator-initiated emergency stop. When
    ``instance_id`` is provided (the AUTOMATIC LLM-critical abort path), it must
    halt ONLY that instance and cancel orders ONLY on that instance's linked
    brokerage, so e.g. a paper instance's LLM failure can never flip
    runCommand=False on the real-money 'main' instance or cancel its Robinhood
    orders. If the instance has no linked brokerage_id we cancel NOTHING (fail
    safe) rather than falling back to touching every account.
    """
    r, conn = _get_conn()
    summary = {
        "instances_halted": 0,
        "orders_canceled": 0,
        "errors": [],
        "scope": instance_id or "<all>",
    }

    try:
        # Step 1: flip runCommand=False (triggers instance.py to exit).
        # Scoped to the one instance when instance_id is given.
        try:
            _tbl = r.db(DB_NAME).table("Instances")
            _q = _tbl.filter(lambda row: row["id"] == instance_id) if instance_id is not None else _tbl
            res = _q.update(
                {"runCommand": False, "halt_reason": reason, "halted_at": r.now()}
            ).run(conn)
            summary["instances_halted"] = int(res.get("replaced", 0) or 0) + int(res.get("unchanged", 0) or 0)
        except Exception as e:
            summary["errors"].append(f"instances update: {e}")

        # Step 2: cancel open orders per linked brokerage.
        if cancel_open_orders:
            try:
                brokerages = list(r.db(DB_NAME).table("BrokerageAccounts").run(conn))
            except Exception as e:
                summary["errors"].append(f"brokerages fetch: {e}")
                brokerages = []
            # Scope to the failing instance's linked brokerage when instance_id
            # is given. Fail SAFE: if the link can't be resolved, cancel nothing
            # rather than touching every account.
            if instance_id is not None:
                linked_bid = None
                try:
                    _inst = r.db(DB_NAME).table("Instances").get(instance_id).run(conn) or {}
                    linked_bid = _inst.get("brokerage_id")
                except Exception as e:
                    summary["errors"].append(f"instance {instance_id} lookup: {e}")
                if linked_bid:
                    brokerages = [b for b in brokerages if b.get("id") == linked_bid]
                else:
                    summary["errors"].append(
                        f"instance {instance_id}: no linked brokerage_id resolved; "
                        f"skipping order-cancel (fail-safe, won't touch other accounts)"
                    )
                    brokerages = []
            for b in brokerages:
                bt = (b.get("brokerage_type") or "alpaca").strip().lower()
                if bt == "alpaca":
                    try:
                        from stock_credential_boundary import (
                            resolve_alpaca_brokerage_credentials,
                        )
                        creds = resolve_alpaca_brokerage_credentials(b)
                        from alpaca.trading.client import TradingClient
                        client = TradingClient(
                            api_key=creds.key,
                            secret_key=creds.secret,
                            paper=creds.paper,
                        )
                        canceled = client.cancel_orders() or []
                        summary["orders_canceled"] += len(canceled)
                    except Exception as e:
                        summary["errors"].append(
                            "Alpaca order-cancel failed "
                            f"({type(e).__name__})"
                        )
                    continue

                if bt == "robinhood":
                    # 2026-05-28: Robinhood support. Mirrors the Alpaca branch:
                    # decrypt creds, build a minimal RobinhoodClient, list open
                    # orders for the linked sub-account, cancel each one.
                    # We do NOT respect RH_DRY_RUN here — the kill switch is
                    # an emergency stop; if there are real open orders we cancel
                    # them regardless of the dry-run flag.
                    try:
                        from secret_store import decrypt
                        access = decrypt(b.get("robinhood_access_token")) or None
                        refresh = decrypt(b.get("robinhood_refresh_token")) or None
                        device = decrypt(b.get("robinhood_device_token")) or None
                        account_number = (b.get("robinhood_account_number") or "").strip() or None
                        account_url = (b.get("robinhood_account_url") or "").strip() or None
                        obtained_at = b.get("robinhood_obtained_at_epoch")
                        expires_in = b.get("robinhood_expires_in")
                        if not access or not refresh:
                            summary["errors"].append(
                                f"brokerage {b.get('id')}: robinhood creds missing/blank after decrypt"
                            )
                            continue
                        from robinhood_engine import RobinhoodClient, RobinhoodSessionState
                        state = RobinhoodSessionState(
                            access_token=access,
                            refresh_token=refresh,
                            token_type="Bearer",
                            device_token=device,
                            account_number=account_number,
                            account_url=account_url,
                            obtained_at_epoch=int(obtained_at) if obtained_at else None,
                            expires_in=int(expires_in) if expires_in else None,
                        )
                        client = RobinhoodClient(state=state, timeout_sec=20)

                        # Scope D B2: the kill switch is most needed when the
                        # daemon is DOWN — and if it's been down past the RH
                        # access-token lifetime, the stored token is expired.
                        # _request_json does NOT auto-refresh on 401, so list OR
                        # cancel would silently 401 and leave real orders open.
                        # Wrap EVERY RH call in a ONE-SHOT refresh+retry (covers
                        # both list_orders and each cancel_order, since a 401 can
                        # surface first on either) and persist the rotated token so
                        # the DB doesn't desync. Single-shot so it cannot loop.
                        _rh_refreshed = {"done": False}

                        def _with_401_refresh(fn):
                            try:
                                return fn()
                            except Exception as _e_rh:
                                if (
                                    int(getattr(_e_rh, "status_code", 0) or 0) == 401
                                    and not _rh_refreshed["done"]
                                    and callable(getattr(client, "refresh", None))
                                ):
                                    _rh_refreshed["done"] = True
                                    client.refresh()
                                    try:
                                        _persist_rh_refreshed_token(r, conn, b, client)
                                    except Exception as _persist_error:
                                        summary["errors"].append(
                                            "credential refresh persistence failed "
                                            f"({type(_persist_error).__name__})"
                                        )
                                    return fn()
                                raise

                        open_orders = _with_401_refresh(
                            lambda: client.list_orders(
                                state="open", limit=500, account_number=account_number,
                            ) or []
                        )
                        for o in open_orders:
                            oid = o.get("id")
                            cancel_url = o.get("cancel")
                            if not (oid or cancel_url):
                                continue
                            try:
                                ok = _with_401_refresh(
                                    lambda: client.cancel_order(
                                        cancel_url=cancel_url, order_id=oid,
                                    )
                                )
                                if ok:
                                    summary["orders_canceled"] += 1
                            except Exception as inner:
                                summary["errors"].append(
                                    f"brokerage {b.get('id')} order {oid}: {inner}"
                                )
                    except Exception as e:
                        summary["errors"].append(f"brokerage {b.get('id')}: {e}")
                    continue

                if bt == "kalshi":
                    # 2026-06-22: Kalshi support. Cancel ALL resting orders on the
                    # linked Kalshi account via the lean client's kill primitive.
                    # Like the other branches this is an emergency stop — it does
                    # not respect any per-instance dry-run flag.
                    try:
                        from secret_store import decrypt
                        key_id = (b.get("kalshi_key_id") or "").strip()
                        pem = decrypt(b.get("kalshi_private_key")) or ""
                        environment = (b.get("kalshi_environment") or "demo").strip().lower()
                        if not key_id or not pem:
                            summary["errors"].append(
                                f"brokerage {b.get('id')}: kalshi creds missing/blank after decrypt"
                            )
                            continue
                        from kalshi.client import KalshiClient
                        client = KalshiClient(
                            key_id=key_id, private_key_pem=pem, environment=environment
                        )
                        summary["orders_canceled"] += int(client.cancel_all_open_orders())
                    except Exception as e:
                        summary["errors"].append(f"brokerage {b.get('id')}: {e}")
                    continue

                # Unknown brokerage_type — note and skip
                summary["errors"].append(
                    f"brokerage {b.get('id')}: unsupported brokerage_type={bt!r}"
                )

        # Step 3: Discord alert.
        try:
            from live_alerts import alert_halt
            alert_halt(instance_id=(instance_id or "<all>"), reason=f"{reason} "
                                                     f"({summary['instances_halted']} instances, "
                                                     f"{summary['orders_canceled']} orders canceled)")
        except Exception as e:
            summary["errors"].append(f"discord alert: {e}")
    finally:
        try:
            conn.close()
        except Exception:
            pass

    return summary


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    reason = argv[0] if argv else "manual halt"
    summary = halt_live_trading(reason=reason)
    print("HALT SUMMARY:", summary)
    return 0 if not summary.get("errors") else 1


if __name__ == "__main__":
    raise SystemExit(main())
