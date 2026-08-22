#!/usr/bin/env python3
"""READ-ONLY diagnostic for the live Alpaca instance `alpaca-main`.

Answers ONE question: how much of the daily "drop at the US open" is
  (a) unbankable overnight/pre-market MARK evaporating at 9:30, vs
  (b) real SLIPPAGE from the bot's own market orders placed into the open.

It pulls, read-only:
  1. Raw Alpaca portfolio-history JSON (1D + 5D, 15Min, continuous, extended)
     -> inspect whether the overnight segment is flat-carry or thin-drift and
        measure the size of the 9:30 ET step.
  2. Recent orders/fills (last 3 days) with order_type + filled_avg_price
     -> spot market orders fired near the open.
  3. Slippage of each fill vs the strategy's decision-time reference price
     (BotTradeDecisions.price) -> quantify self-inflicted cost.

Places / cancels NOTHING. Only get_orders, get_account, GET portfolio/history.

Run INSIDE a backend container (has alpaca-py, secret_store, RethinkDB reach,
and INTELLISTOCK_CRED_KEY already injected). No secrets are typed -- creds are
read from RethinkDB and Fernet-decrypted with the container's key.
"""
import os
import sys
import json
import datetime as dt

sys.path.insert(0, "/app")  # so `secret_store` / backend modules import inside the container

import requests  # noqa: E402
from db import store as _store  # noqa: E402
from secret_store import decrypt  # backend/secret_store.py  # noqa: E402

INSTANCE_ID = os.environ.get("DIAG_INSTANCE", "alpaca-main")
DB = "IntelliStock"
LOOKBACK_DAYS = int(os.environ.get("DIAG_LOOKBACK_DAYS", "3"))


def main():
    # R26: the store pools its own connection per operation.
    r, conn = _store, None

    # ---- resolve creds exactly like broker.py:585-643 (read-only) ----
    inst = _store.get("Instances", INSTANCE_ID)
    if not inst:
        print(f"!! no Instances row {INSTANCE_ID!r}", file=sys.stderr)
        sys.exit(2)
    bid = inst.get("brokerage_id")
    brow = r.get("BrokerageAccounts", bid)
    key = decrypt(brow.get("alpaca_key")) or ""
    secret = decrypt(brow.get("alpaca_secret")) or ""
    paper = bool(brow.get("alpaca_paper", inst.get("alpaca_paper", True)))
    base = brow.get("alpaca_base_url") or (
        "https://paper-api.alpaca.markets" if paper else "https://api.alpaca.markets"
    )
    hdr = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}
    print(f"# instance={INSTANCE_ID} brokerage_id={bid} paper={paper} "
          f"base={base} key=...{key[-4:] if key else '??'}")
    if paper:
        print("# NOTE: this row is PAPER, not the live account. Double-check the instance.")

    # ---- (1) raw portfolio-history JSON ----
    for period, tf in (("1D", "15Min"), ("5D", "15Min")):
        try:
            resp = requests.get(
                f"{base}/v2/account/portfolio/history", headers=hdr, timeout=15,
                params={"period": period, "timeframe": tf,
                        "intraday_reporting": "continuous", "extended_hours": "true"},
            )
            print(f"\n===== portfolio/history period={period} tf={tf} (HTTP {resp.status_code}) =====")
            if not resp.ok:
                print(resp.text[:500])
                continue
            j = resp.json()
            ts = j.get("timestamp") or []
            eq = j.get("equity") or []
            # compact per-bar dump in ET so you can eyeball the 9:30 step
            print(f"{'time_ET':20} {'equity':>12} {'d_equity':>10}")
            prev = None
            for t, v in zip(ts, eq):
                et = dt.datetime.fromtimestamp(int(t), dt.timezone.utc) - dt.timedelta(hours=4)
                d = "" if prev is None or v is None else f"{v - prev:+.2f}"
                print(f"{et.strftime('%Y-%m-%d %H:%M ET'):20} {('' if v is None else f'{v:.2f}'):>12} {d:>10}")
                if v is not None:
                    prev = v
        except Exception as e:
            print(f"!! portfolio/history {period} failed: {e}", file=sys.stderr)

    try:
        acct = requests.get(f"{base}/v2/account", headers=hdr, timeout=10).json()
        print(f"\n# live equity={acct.get('equity')} last_equity={acct.get('last_equity')} "
              f"cash={acct.get('cash')}")
    except Exception as e:
        print(f"!! /v2/account failed: {e}", file=sys.stderr)

    # ---- (2) recent orders/fills via the same alpaca-py client (read-only) ----
    rows = []
    try:
        from alpaca.trading.client import TradingClient
        from alpaca.trading.requests import GetOrdersRequest
        from alpaca.trading.enums import QueryOrderStatus
        client = TradingClient(api_key=key, secret_key=secret, paper=paper)
        since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=LOOKBACK_DAYS)
        orders = client.get_orders(
            filter=GetOrdersRequest(status=QueryOrderStatus.ALL, after=since, limit=500))
        print(f"\n===== orders since {since.date()} (n={len(orders)}) =====")
        for o in sorted(orders, key=lambda x: (x.submitted_at or since)):
            fa = float(o.filled_avg_price) if o.filled_avg_price else None
            sub_et = (o.submitted_at - dt.timedelta(hours=4)).strftime('%m-%d %H:%M ET') \
                if o.submitted_at else "?"
            print(f"{sub_et}  {o.side.value:4} {o.symbol:6} type={o.order_type.value:7} "
                  f"qty={o.qty} filled={o.filled_qty} avg={fa} status={o.status.value} "
                  f"ext={getattr(o, 'extended_hours', '?')} limit={o.limit_price} "
                  f"cid={o.client_order_id}")
            rows.append((o.symbol, o.side.value, str(o.submitted_at), fa))
    except Exception as e:
        print(f"!! orders pull failed: {e}", file=sys.stderr)

    # ---- (3) slippage vs strategy decision-time price ----
    print("\n===== slippage: fill vs BotTradeDecisions.price (unfavorable = bot paid up) =====")
    for sym, side, sub_ts, fa in rows:
        if fa is None:
            continue
        try:
            d = list(r.run(r.limit(r.order_by(r.filter(
                "BotTradeDecisions",
                r.P.field("symbol").eq(sym.upper())
                & r.P.field("side").eq(side)
                & r.P.field("instance_id").starts_with(INSTANCE_ID)),
                index="ts", desc=True), 1)))
        except Exception:
            d = []
        ref = d[0]["price"] if d and d[0].get("price") else None
        if ref:
            slip = (fa - ref) if side == "buy" else (ref - fa)
            print(f"{sym:6} {side:4} fill={fa} ref={ref} "
                  f"slippage={slip:+.4f} ({slip / ref * 1e4:+.1f} bps unfavorable)")
        else:
            print(f"{sym:6} {side:4} fill={fa} ref=<none in BotTradeDecisions>")

    conn.close()


if __name__ == "__main__":
    main()
