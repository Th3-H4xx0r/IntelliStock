#!/usr/bin/env python3
"""Build the swing-trader reference tables (spec §7).

    python3 scripts/build_swing_reference_data.py \\
        --membership-csv "S&P 500 Historical Components & Changes(09-01-2026).csv"
    python3 scripts/build_swing_reference_data.py --only vix

SwingMacroDaily       VIX closes from Cboe's VIX_History.csv; FRED VIXCLS when
                      Cboe fails.
SwingIndexMembership  S&P 500 members by change date from fja05680/sp500's
                      "S&P 500 Historical Components & Changes(MM-DD-YYYY).csv"
                      (take the newest from github.com/fja05680/sp500; the name
                      carries its date, so it is an argument, never a default).
                      RENAME_MAP carries old tickers to the symbols Alpaca's
                      bars use.
SwingSectorMap        yfinance info.sector for every member since --start plus
                      SPY, QQQ and the defensive ETFs, normalised as ST did;
                      ST's overrides win. Static, NOT point-in-time.

Idempotent: deterministic ids, conflict="replace". No broker credentials.
A VIX row with no numeric close (blank, "N/A", nan, inf, <= 0) is skipped and
listed; one is never written, since the reader looks only at the latest row.
"""
from __future__ import annotations

import argparse
import csv
import io
import math
import os
import re
import sys
import time
import urllib.request
from datetime import date, datetime

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "backend"))

from swing_trader import refdata, sectors  # noqa: E402
from swing_trader.constants import DEFENSIVE_UNIVERSE  # noqa: E402
from swing_trader.universe import norm_symbol  # noqa: E402

CBOE_VIX_URL = "https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv"
FRED_VIX_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=VIXCLS"
DEFAULT_START = "2019-01-01"
BENCHMARKS = ("SPY", "QQQ")

#: A continuing company's old S&P ticker -> the symbol Alpaca's bars carry it
#: under today. Only ticker changes; an acquired or delisted name is left as
#: it is (it has no bars after it leaves, and the engine skips it). Verify
#: against Alpaca before trusting a window that spans one of these dates.
RENAME_MAP = {
    "ABC": "COR",      # AmerisourceBergen -> Cencora, 2023-08-30
    "ANTM": "ELV",     # Anthem -> Elevance Health, 2022-06-28
    "ARNC": "HWM",     # Arconic Inc. -> Howmet Aerospace, 2020-04-01
    "BBT": "TFC",      # BB&T -> Truist, 2019-12-09
    "BLL": "BALL",     # Ball Corp, 2022-05-17
    "CBS": "PARA",     # CBS -> ViacomCBS (VIAC) 2019-12-05 -> Paramount 2022-02-16
    "CDAY": "DAY",     # Ceridian -> Dayforce, 2024-02-01
    "COG": "CTRA",     # Cabot Oil & Gas -> Coterra, 2021-10-01
    "CTL": "LUMN",     # CenturyLink -> Lumen, 2020-09-18
    "DISCA": "WBD",    # Discovery -> Warner Bros. Discovery, 2022-04-11
    "DWDP": "DD",      # DowDuPont -> DuPont de Nemours, 2019-06-03
    "FB": "META",      # Facebook -> Meta Platforms, 2022-06-09
    "FBHS": "FBIN",    # Fortune Brands Home & Security -> Innovations, 2022-12-15
    "FISV": "FI",      # Fiserv, 2023-06-07
    "FLT": "CPAY",     # FleetCor -> Corpay, 2024-03-25
    "HCP": "DOC",      # HCP -> Healthpeak (PEAK) 2019-11-05 -> DOC 2024-03-04
    "HRS": "LHX",      # Harris -> L3Harris, 2019-07-01
    "JEC": "J",        # Jacobs Engineering, 2019-12-10
    "LB": "BBWI",      # L Brands -> Bath & Body Works, 2021-08-03
    "MYL": "VTRS",     # Mylan -> Viatris, 2020-11-16
    "NLOK": "GEN",     # NortonLifeLock -> Gen Digital, 2022-11-08
    "PEAK": "DOC",     # Healthpeak, 2024-03-04
    "PKI": "RVTY",     # PerkinElmer -> Revvity, 2023-05-16
    "RE": "EG",        # Everest Re -> Everest Group, 2023-07-10
    "SYMC": "GEN",     # Symantec -> NortonLifeLock (NLOK) 2019-11-04 -> GEN
    "UTX": "RTX",      # United Technologies -> Raytheon Technologies, 2020-04-03
    "VIAC": "PARA",    # ViacomCBS -> Paramount Global, 2022-02-16
    "WLTW": "WTW",     # Willis Towers Watson, 2022-01-04
}

#: The shape of an Alpaca US equity symbol, dot for a share class (BRK.B).
_TICKER = re.compile(r"[A-Z]{1,6}(\.[A-Z]{1,2})?")

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
"inactive", delisted) is a wrong target, so fix the map and rerun. Known
suspects to check first: PARA (CBS and VIAC map to it; Paramount Skydance has
traded as PSKY since August 2025) and FISV/FI (Fiserv traded as FI from 2023;
check which symbol Alpaca lists as active today).
A former member that is not found, but whose company still trades under a new
symbol, needs a RENAME_MAP entry (old -> new). Rerunning is idempotent.
"""


def fetch_text(url, *, attempts=4, timeout=60) -> str:
    last = None
    for attempt in range(attempts):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "IntelliStock swing refdata"})
            with urllib.request.urlopen(req, timeout=timeout) as response:
                return response.read().decode("utf-8-sig")
        except Exception as exc:
            last = exc
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"fetch failed: {url} ({type(last).__name__}: {last})")


def read_source(path_or_url, fetch) -> str:
    if str(path_or_url).startswith(("http://", "https://")):
        return fetch(path_or_url)
    with open(path_or_url, encoding="utf-8-sig") as fh:
        return fh.read()


def _close(raw):
    """A finite, positive close, else None (G2 carry: never a nan/inf/0 row)."""
    try:
        close = float(str(raw if raw is not None else "").strip())
    except ValueError:
        return None
    return close if math.isfinite(close) and close > 0 else None


def parse_cboe_vix(text, skipped=None) -> list:
    """[(date, close)] from Cboe's CSV. A row without a date or a numeric close
    is skipped and, when `skipped` is a list, recorded there as (date, raw)."""
    out = []
    for row in csv.DictReader(io.StringIO(text)):
        raw_date = str(row.get("DATE") or "").strip()
        raw_close = str(row.get("CLOSE") or "").strip()
        try:
            d = datetime.strptime(raw_date, "%m/%d/%Y").date().isoformat()
        except ValueError:
            d = None
        close = _close(raw_close)
        if d is None or close is None:
            if skipped is not None:
                skipped.append((d or raw_date, raw_close))
            continue
        out.append((d, close))
    return sorted(out)


def parse_fred_vix(text, skipped=None) -> list:
    """[(date, close)] from FRED's VIXCLS CSV. "." is FRED's holiday and is
    dropped silently; any other bad row is recorded in `skipped`."""
    out = []
    reader = csv.reader(io.StringIO(text))
    next(reader, None)                                   # observation_date,VIXCLS
    for row in reader:
        if len(row) < 2:
            continue
        raw_date, raw_close = row[0].strip(), row[1].strip()
        if raw_close == ".":
            continue                                     # FRED writes "." for a holiday
        try:
            d = date.fromisoformat(raw_date).isoformat()
        except ValueError:
            d = None
        close = _close(raw_close)
        if d is None or close is None:
            if skipped is not None:
                skipped.append((d or raw_date, raw_close))
            continue
        out.append((d, close))
    return sorted(out)


def vix_rows(points, source, start) -> list:
    return [{"id": refdata.macro_id("VIX", d), "series": "VIX", "date": d, "close": close,
             "source": source} for d, close in points if d >= start]


def _in_window(skips, start) -> list:
    """The skips dated on or after `start`, plus any whose date is unreadable."""
    out = []
    for d, raw in skips:
        try:
            dated = date.fromisoformat(str(d)[:10]).isoformat()
        except ValueError:
            out.append((d, raw))
            continue
        if dated >= start:
            out.append((d, raw))
    return out


def load_vix(fetch, start, skipped=None) -> list:
    """VIX rows from Cboe, else FRED. The bad rows of the source used, from
    `start` on, are recorded in `skipped`."""
    cboe_skips = []
    try:
        rows = vix_rows(parse_cboe_vix(fetch(CBOE_VIX_URL), cboe_skips), "cboe", start)
        if rows:
            if skipped is not None:
                skipped.extend(_in_window(cboe_skips, start))
            return rows
        reason = "no rows"
    except Exception as exc:
        reason = f"{type(exc).__name__}: {exc}"
    print(f"Cboe VIX unavailable ({reason}); falling back to FRED VIXCLS", flush=True)
    fred_skips = []
    try:
        rows = vix_rows(parse_fred_vix(fetch(FRED_VIX_URL), fred_skips), "fred", start)
    except Exception as exc:
        raise SystemExit(f"no VIX from Cboe or FRED ({type(exc).__name__}: {exc})")
    if not rows:
        raise SystemExit("no VIX rows from Cboe or FRED")
    if skipped is not None:
        skipped.extend(_in_window(fred_skips, start))
    return rows


def parse_membership(text, rename=None) -> list:
    rename = RENAME_MAP if rename is None else rename
    out = []
    for row in csv.DictReader(io.StringIO(text)):
        try:
            d = date.fromisoformat(str(row.get("date") or "").strip()[:10])
        except ValueError:
            continue
        members = set()
        for raw in str(row.get("tickers") or "").split(","):
            sym = norm_symbol(raw)
            if sym:
                members.add(rename.get(sym, sym))
        if members:
            out.append((d.isoformat(), sorted(members)))
    return sorted(out)


def membership_rows(changes, start) -> list:
    """Every change dated on or after `start`, plus the one in effect at it."""
    before = [c for c in changes if c[0] < start]
    keep = ([before[-1]] if before else []) + [c for c in changes if c[0] >= start]
    return [{"id": refdata.membership_id("SPX", d), "index": "SPX", "date": d,
             "members": list(members)} for d, members in keep]


def members_union(rows) -> list:
    return sorted({s for r in rows for s in r["members"]})


def ticker_report(rows):
    """(former, malformed) for the operator's Alpaca check (see --help).
    former: members since --start that are not in the newest list and were
    not renamed onto a current symbol; malformed: symbols that are not the
    shape of an Alpaca equity symbol."""
    union = members_union(rows)
    latest = set(max(rows, key=lambda r: r["date"])["members"]) if rows else set()
    former = sorted(s for s in union if s not in latest)
    malformed = sorted(s for s in union if not _TICKER.fullmatch(s))
    return former, malformed


def yf_sector(symbol):
    import yfinance as yf
    return (yf.Ticker(str(symbol).replace(".", "-")).info or {}).get("sector")


def sector_rows(symbols, *, sector_of, as_of):
    rows, unknown = [], []
    for sym in sorted({norm_symbol(s) for s in symbols if str(s or "").strip()}):
        if sym in sectors._SECTOR_OVERRIDES:
            rows.append({"id": sym, "symbol": sym, "sector": sectors._SECTOR_OVERRIDES[sym],
                         "as_of": as_of, "source": "override"})
            continue
        try:
            raw = sector_of(sym)
        except Exception:
            raw = None
        if not raw:
            unknown.append(sym)
            continue
        rows.append({"id": sym, "symbol": sym, "sector": sectors.normalize_sector(raw),
                     "as_of": as_of, "source": "yfinance"})
    return rows, unknown


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
            srows, unknown = sector_rows(symbols, sector_of=sector_of, as_of=as_of)
            store.insert(refdata.SECTOR_TABLE, srows, conflict="replace")
            print(f"sectors: {len(srows)} rows; {len(unknown)} without a yfinance sector "
                  f"(read as 'unknown'): {', '.join(unknown[:40])}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
