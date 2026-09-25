#!/usr/bin/env python3
"""Build the swing-trader reference tables in full (spec §7).

    python3 scripts/build_swing_reference_data.py \\
        --membership-csv "S&P 500 Historical Components & Changes (Updated).csv"
    python3 scripts/build_swing_reference_data.py --only vix

Optional. The swing strategy fetches and stores the rows it is missing on its
own first run, in any mode (swing_trader.refdata_sync). This CLI rebuilds
every row from --start instead, replacing what is stored.

A thin CLI over swing_trader.refdata_build, the ONE implementation both
writers use:

SwingMacroDaily       VIX closes from Cboe's VIX_History.csv; FRED VIXCLS when
                      Cboe fails.
SwingIndexMembership  S&P 500 members by change date from fja05680/sp500's
                      "S&P 500 Historical Components & Changes*.csv" (take the
                      newest from github.com/fja05680/sp500; it is an argument,
                      never a default). RENAME_MAP carries old tickers to the
                      symbols Alpaca's bars use.
SwingSectorMap        ST's overrides, then Wikipedia's GICS sector, then
                      yfinance info.sector, for every member since --start plus
                      SPY, QQQ and the defensive ETFs, normalised as ST did.
                      Static, NOT point-in-time.

Idempotent: deterministic ids, conflict="replace". No broker credentials.
A VIX row with no numeric close (blank, "N/A", nan, inf, <= 0) is skipped and
listed; one is never written, since the reader looks only at the latest row.
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "backend"))

from swing_trader import refdata, refdata_build  # noqa: E402
from swing_trader.constants import DEFENSIVE_UNIVERSE  # noqa: E402
from swing_trader.refdata_build import (  # noqa: E402,F401  (the tests read these here)
    CBOE_VIX_URL,
    FRED_VIX_URL,
    RENAME_MAP,
    fetch_text,
    members_union,
    membership_rows,
    parse_cboe_vix,
    parse_fred_vix,
    parse_membership,
    sector_rows,
    ticker_report,
    vix_rows,
    yf_sector,
)

DEFAULT_START = "2019-01-01"
BENCHMARKS = ("SPY", "QQQ")

VERIFY_HELP = """\
Operator verification (G8a ruling 5). The build never calls Alpaca, so it cannot
prove a symbol exists there; it prints what to check instead:
  - every RENAME_MAP target (the renames in play are printed), and
  - every "former member without a rename": a ticker in the membership file
    that is not in its newest list. That is an acquired or delisted name (fine:
    the engine skips a symbol with no bars), a name that left the index but
    still trades (fine), or a ticker change missing from RENAME_MAP (not fine:
    its history is lost to the old symbol).
Check them once against Alpaca's asset list, with the lab's own keys:

    python3 - <<'PY'
    from alpaca.trading.client import TradingClient
    client = TradingClient("<key>", "<secret>", paper=True)
    for symbol in ["COR", "ELV", "META"]:          # paste the printed lists
        try:
            asset = client.get_asset(symbol)
            print(symbol, asset.status, "tradable" if asset.tradable else "not tradable")
        except Exception as exc:
            print(symbol, "NOT FOUND", exc)
    PY

A RENAME_MAP target must print status "active": anything else (not found,
"inactive", delisted) is a wrong target, so fix the map and rerun. The map
follows the port's current S&P list: CBS, VIAC and PARA map to PSKY
(Paramount Skydance since August 2025) and FI to FISV (Fiserv's ticker
again). Check first that Alpaca's PSKY and FISV bars reach back through the
PARA and FI years.
A former member that is not found, but whose company still trades under a new
symbol, needs a RENAME_MAP entry (old -> new). Rerunning is idempotent.
"""


def read_source(path_or_url, fetch) -> str:
    if str(path_or_url).startswith(("http://", "https://")):
        return fetch(path_or_url)
    with open(path_or_url, encoding="utf-8-sig") as fh:
        return fh.read()


def load_vix(fetch, start, skipped=None) -> list:
    """refdata_build.load_vix, exiting when neither Cboe nor FRED answers."""
    try:
        return refdata_build.load_vix(fetch, start, skipped, log=_say)
    except refdata_build.RefdataUnavailable as exc:
        raise SystemExit(str(exc))


def _say(message):
    print(message, flush=True)


def main(argv=None, *, store=None, fetch=None, sector_of=None, today=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, epilog=VERIFY_HELP,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", action="append", choices=("vix", "membership", "sectors"),
                    help="build only this table (repeatable); default all three")
    ap.add_argument("--start", default=DEFAULT_START, type=date.fromisoformat,
                    help="first date to build, YYYY-MM-DD (default %(default)s)")
    ap.add_argument("--membership-csv",
                    help="path or URL of fja05680's historical-components CSV")
    args = ap.parse_args(argv)
    args.start = args.start.isoformat()       # M7: validated by argparse, used as text
    parts = set(args.only or ("vix", "membership", "sectors"))
    if parts & {"membership", "sectors"} and not args.membership_csv:
        ap.error("--membership-csv is required to build membership or sectors")
    if store is None:
        from db import schema as dbschema
        from db import store as db_store
        dbschema.ensure_schema(tables=[refdata.MACRO_TABLE, refdata.MEMBERSHIP_TABLE,
                                       refdata.SECTOR_TABLE])
        store = db_store
    fetch = fetch or fetch_text
    sector_of = sector_of or yf_sector
    as_of = (today or date.today()).isoformat()

    if "vix" in parts:
        skipped = []
        rows = load_vix(fetch, args.start, skipped)
        store.insert(refdata.MACRO_TABLE, rows, conflict="replace")
        print(f"VIX: {len(rows)} rows {rows[0]['date']}..{rows[-1]['date']} "
              f"({rows[0]['source']})", flush=True)
        if skipped:
            print(f"VIX: skipped {len(skipped)} VIX rows with no numeric close (never "
                  "written): " + ", ".join(f"{d}={raw}" for d, raw in skipped[:40]),
                  flush=True)

    if parts & {"membership", "sectors"}:
        changes = parse_membership(read_source(args.membership_csv, fetch))
        rows = membership_rows(changes, args.start)
        if not rows:
            raise SystemExit("the membership CSV has no dated rows")
        if "membership" in parts:
            store.insert(refdata.MEMBERSHIP_TABLE, rows, conflict="replace")
            union = members_union(rows)
            applied = sorted(f"{old}->{new}" for old, new in RENAME_MAP.items()
                             if new in union)
            print(f"membership: {len(rows)} change dates {rows[0]['date']}..{rows[-1]['date']}, "
                  f"{len(union)} distinct members; renames in play: {', '.join(applied)}",
                  flush=True)
            former, malformed = ticker_report(rows)
            print(f"membership: {len(former)} former member(s) without a rename (acquired "
                  "or delisted, or a RENAME_MAP gap; verify against Alpaca's asset list, "
                  "see --help): " + ", ".join(former), flush=True)
            if malformed:
                print(f"membership: {len(malformed)} malformed ticker(s): "
                      + ", ".join(malformed), flush=True)
        if "sectors" in parts:
            symbols = members_union(rows) + list(BENCHMARKS) + list(DEFENSIVE_UNIVERSE)
            wikipedia = refdata_build.wikipedia_sectors(fetch, log=_say)
            srows, unknown = sector_rows(symbols, sector_of=sector_of, as_of=as_of,
                                         wikipedia=wikipedia)
            store.insert(refdata.SECTOR_TABLE, srows, conflict="replace")
            wiki = sum(1 for r in srows if r["source"] == "wikipedia")
            print(f"sectors: {len(srows)} rows ({wiki} from Wikipedia); {len(unknown)} "
                  "without a Wikipedia or yfinance sector (no row: read as 'unknown'): "
                  f"{', '.join(unknown[:40])}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
