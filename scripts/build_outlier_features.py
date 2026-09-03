#!/usr/bin/env python3
"""Build OutlierUniverseFeatures from Alpaca daily bars (IEX, adjusted).

    python3 scripts/build_outlier_features.py --start 2020-06-01 --end 2026-08-31

Universe: US equities on NASDAQ/NYSE/ARCA/AMEX, active AND inactive (the
survivorship guard), alphabetic tickers <= 5 chars, ADV >= $10M and close >= $3
over the last ~90 sessions, plus inactive names that were liquid in 2023-H1.
Measured: ~10k bars per 0.7s page; a full 5.5-year build is minutes.
Idempotent: rows are inserted with conflict="replace".
Credentials: the Alpaca key pair of --brokerage-id (default: the paper account
used by strategy-eb), decrypted through secret_store. Never printed.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.parse
import urllib.request

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "backend"))

from outlier_features import (  # noqa: E402
    FEATURES_TABLE, compute_features, feature_id, rank_cross_section,
)

DEFAULT_BROKERAGE = "bf78ad0c-3073-4aac-97a5-a29c7b043404"
DATA = "https://data.alpaca.markets/v2/stocks/bars"
ASSETS = "https://paper-api.alpaca.markets/v2/assets"
EXCHANGES = {"NASDAQ", "NYSE", "ARCA", "AMEX"}


def select_liquid(recent, adv_min, price_min, min_bars) -> list:
    out = []
    for sym, bb in recent.items():
        if len(bb) < min_bars:
            continue
        adv = sum(float(x["c"]) * float(x["v"]) for x in bb) / len(bb)
        if adv >= adv_min and float(bb[-1]["c"]) >= price_min:
            out.append(sym)
    return sorted(out)


def rows_for_universe(bars_by_symbol, adv_min) -> list:
    """Feature rows for every (date, symbol), ranked within each date."""
    by_date = {}
    for sym, bb in bars_by_symbol.items():
        if not bb:
            continue
        dates = [str(x["t"])[:10] for x in bb]
        rows = compute_features([float(x["c"]) for x in bb],
                                [float(x["v"]) for x in bb], dates)
        for r in rows:
            r["symbol"] = sym.upper()
            r["id"] = feature_id(r["date"], sym)
            by_date.setdefault(r["date"], []).append(r)
    out = []
    for d in sorted(by_date):
        out.extend(rank_cross_section(by_date[d], adv_min))
    return out


def _headers(brokerage_id):
    from db import store
    from secret_store import decrypt
    b = store.get("BrokerageAccounts", brokerage_id)
    if not b:
        raise SystemExit(f"brokerage {brokerage_id} not found")
    return {"APCA-API-KEY-ID": decrypt(b["alpaca_key"]),
            "APCA-API-SECRET-KEY": decrypt(b["alpaca_secret"])}


def _get(url, headers):
    for att in range(5):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.load(r)
        except Exception:
            time.sleep(3 * (att + 1))
    raise SystemExit("alpaca request failed: " + url[:90])


def fetch_bars(symbols, start, end, headers, chunk=150) -> dict:
    out = {}
    for i in range(0, len(symbols), chunk):
        part = symbols[i:i + chunk]
        tok = None
        while True:
            q = {"symbols": ",".join(part), "timeframe": "1Day", "start": start,
                 "end": end, "limit": 10000, "feed": "iex", "adjustment": "all"}
            if tok:
                q["page_token"] = tok
            d = _get(DATA + "?" + urllib.parse.urlencode(q), headers)
            for s, bb in (d.get("bars") or {}).items():
                out.setdefault(s, []).extend(bb)
            tok = d.get("next_page_token")
            if not tok:
                break
    return out


def candidate_symbols(headers) -> list:
    assets = (_get(ASSETS + "?status=active&asset_class=us_equity", headers)
              + _get(ASSETS + "?status=inactive&asset_class=us_equity", headers))
    return sorted({a["symbol"] for a in assets
                   if a.get("exchange") in EXCHANGES and a["symbol"].isalpha()
                   and len(a["symbol"]) <= 5})


def _shift(day, days):
    from datetime import date, timedelta
    return (date.fromisoformat(day) + timedelta(days=days)).isoformat()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--start", default="2020-06-01")
    ap.add_argument("--end", required=True)
    ap.add_argument("--brokerage-id", default=DEFAULT_BROKERAGE)
    ap.add_argument("--adv-min", type=float, default=1e7)
    ap.add_argument("--price-min", type=float, default=3.0)
    args = ap.parse_args(argv)
    from db import store
    from db import schema as dbschema
    dbschema.ensure_schema(tables=[FEATURES_TABLE])
    headers = _headers(args.brokerage_id)
    syms = candidate_symbols(headers)
    print(f"candidates {len(syms)}", flush=True)
    recent = fetch_bars(syms, _shift(args.end, -130), args.end, headers)
    liquid = select_liquid(recent, args.adv_min, args.price_min, 40)
    dead = [s for s in syms if s not in recent]
    old = fetch_bars(dead, "2023-01-01", "2023-06-30", headers)
    liquid = sorted(set(liquid) | set(select_liquid(old, args.adv_min, args.price_min, 60)))
    print(f"liquid universe {len(liquid)}", flush=True)
    bars = fetch_bars(liquid, args.start, args.end, headers)
    rows = rows_for_universe(bars, args.adv_min)
    print(f"rows {len(rows)}", flush=True)
    for i in range(0, len(rows), 5000):
        store.insert(FEATURES_TABLE, rows[i:i + 5000], conflict="replace")
        if (i // 5000) % 20 == 0:
            print(f"  wrote {min(i + 5000, len(rows))}/{len(rows)}", flush=True)
    print("done", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
