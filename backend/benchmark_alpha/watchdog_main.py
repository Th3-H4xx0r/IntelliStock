"""Alpha mark/equity watchdog subprocess entrypoint (Task 6 Step 9).

Runs OUT-OF-PROCESS from the broker. Disabled by default: `instance.py`
launches it only when ``ALPHA_MARK_WATCHDOG_ENABLED=1``, and this entrypoint
refuses to start without its own scoped credentials
(``ALPACA_WATCHDOG_KEY``/``ALPACA_WATCHDOG_SECRET``) and a reachable
Postgres — the deployment prerequisites named by the plan. If Alpaca cannot
issue a separately scoped credential for the account, the operator may set
these to the shared runtime credential, accepting the residual risk recorded
in the LIVE_40 sign-off (Task 6 Step 8a).
"""
import argparse
import os
import sys
import time
from datetime import datetime, timezone


def _build_runtime(instance_id):
    from alpaca.trading.client import TradingClient
    from alpaca.trading.requests import GetOrdersRequest
    from alpaca.trading.enums import QueryOrderStatus

    from db import store as db_store
    from benchmark_alpha.pg_store import (
        AlphaPostgresStore,
        AlphaStateConflictError,
    )
    from benchmark_alpha.watchdog import AlphaWatchdog

    key = os.environ["ALPACA_WATCHDOG_KEY"]
    secret = os.environ["ALPACA_WATCHDOG_SECRET"]
    paper = os.environ.get("ALPACA_WATCHDOG_PAPER", "0") == "1"
    client = TradingClient(api_key=key, secret_key=secret, paper=paper)

    store = AlphaPostgresStore()

    class Probe:
        def broker_equity(self):
            return float(client.get_account().equity or 0.0)

        def broker_positions(self):
            out = {}
            for p in client.get_all_positions():
                try:
                    out[str(p.symbol)] = float(p.qty)
                except (TypeError, ValueError):
                    continue
            return out

        def cancel_entry_orders(self):
            req = GetOrdersRequest(status=QueryOrderStatus.OPEN)
            for order in client.get_orders(filter=req):
                if str(getattr(order, "side", "")).lower().endswith("buy"):
                    try:
                        client.cancel_order_by_id(order.id)
                    except Exception:
                        continue

        def halt_instance(self):
            db_store.update("Instances", instance_id, {"runCommand": False})

    def write_health(evidence):
        key = f"control_health:{instance_id}"
        for _attempt in range(3):
            current = store.get_state(key)
            expected = current.version if current is not None else 0
            try:
                store.put_state(key, evidence.to_doc(), expected)
                return
            except AlphaStateConflictError:
                continue
        raise AlphaStateConflictError(
            "watchdog control-health CAS retries exhausted"
        )

    watchdog = AlphaWatchdog(
        probe=Probe(), rethink_store=store, thresholds={},
        instance_id=instance_id,
        reduce_executor=None,
        health_writer=write_health,
    )
    return watchdog


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--instance-id", required=True)
    parser.add_argument("--poll-seconds", type=float,
                        default=float(os.environ.get("WATCHDOG_POLL_SEC", "30")))
    args = parser.parse_args(argv)

    missing = [name for name in ("ALPACA_WATCHDOG_KEY", "ALPACA_WATCHDOG_SECRET")
               if not os.environ.get(name)]
    if not (os.environ.get("PG_DSN") or os.environ.get("PGHOST")):
        missing.append("PG_DSN")
    if missing:
        print(f"[watchdog] refusing to start: missing env {missing} "
              "(scoped watchdog credentials are a deployment prerequisite)",
              file=sys.stderr)
        return 2

    watchdog = _build_runtime(args.instance_id)
    print(f"[watchdog] started for {args.instance_id} "
          f"(poll every {args.poll_seconds:.0f}s)")
    while True:
        try:
            result = watchdog.poll_once(datetime.now(timezone.utc))
            if result.status != "OK":
                print(f"[watchdog] {result.status} mismatches="
                      f"{len(result.mismatches)} degraded_audit={result.degraded_audit}")
        except Exception as exc:
            try:
                watchdog.record_failure(datetime.now(timezone.utc), exc)
            except Exception:
                pass
            print(f"[watchdog] poll error: {type(exc).__name__}: {exc}",
                  file=sys.stderr)
        time.sleep(args.poll_seconds)


if __name__ == "__main__":
    sys.exit(main())
