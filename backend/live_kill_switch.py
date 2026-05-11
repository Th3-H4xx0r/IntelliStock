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


def halt_live_trading(reason: str = "manual halt", cancel_open_orders: bool = True) -> dict:
    """Halt every running live instance. Returns a summary dict for reporting."""
    r, conn = _get_conn()
    summary = {"instances_halted": 0, "orders_canceled": 0, "errors": []}

    try:
        # Step 1: bulk-flip runCommand=False (triggers instance.py to exit).
        try:
            res = (
                r.db(DB_NAME)
                .table("Instances")
                .update({"runCommand": False, "halt_reason": reason, "halted_at": r.now()})
                .run(conn)
            )
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
            for b in brokerages:
                bt = (b.get("brokerage_type") or "alpaca").strip().lower()
                if bt != "alpaca":
                    continue  # Only Alpaca is wired for live; Robinhood scaffolded only.
                try:
                    from secret_store import decrypt
                    k = decrypt(b.get("alpaca_key"))
                    s = decrypt(b.get("alpaca_secret"))
                    paper = bool(b.get("alpaca_paper", True))
                    if not k or not s:
                        continue
                    from alpaca.trading.client import TradingClient
                    client = TradingClient(api_key=k, secret_key=s, paper=paper)
                    canceled = client.cancel_orders() or []
                    summary["orders_canceled"] += len(canceled)
                except Exception as e:
                    summary["errors"].append(f"brokerage {b.get('id')}: {e}")

        # Step 3: Discord alert.
        try:
            from live_alerts import alert_halt
            alert_halt(instance_id="<all>", reason=f"{reason} "
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
