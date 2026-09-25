"""Sources and row builders for the swing reference tables (spec §7).

ONE implementation, used by both writers:
  - swing_trader.refdata_sync, the strategy's own first-run sync (missing
    rows only), and
  - scripts/build_swing_reference_data.py, the operator's full-build CLI.

SwingMacroDaily       VIX closes from Cboe's VIX_History.csv; FRED VIXCLS when
                      Cboe fails.
SwingIndexMembership  S&P 500 members by change date from fja05680/sp500's
                      "S&P 500 Historical Components & Changes*.csv", found
                      through GitHub's contents API ("(Updated)" preferred).
                      RENAME_MAP carries old tickers to the symbols Alpaca's
                      bars use.
SwingSectorMap        ST's overrides first, then Wikipedia's "List of S&P 500
                      companies" GICS Sector column (one request, the page ST's
                      update_symbols.py scrapes), mapped onto the Yahoo names
                      ST's sector cap was built on; yfinance info.sector only
                      for a symbol Wikipedia does not list (a former member).
                      Static, NOT point-in-time.

A VIX row with no numeric close (blank, "N/A", nan, inf, <= 0) is skipped and
listed; one is never written, since the reader looks only at the latest row.
"""
from __future__ import annotations

import csv
import io
import json
import math
import re
import time
import urllib.request
from datetime import date, datetime
from html.parser import HTMLParser

from swing_trader import refdata, sectors
from swing_trader.universe import norm_symbol

CBOE_VIX_URL = "https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv"
FRED_VIX_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=VIXCLS"
SP500_CONTENTS_URL = "https://api.github.com/repos/fja05680/sp500/contents"
#: The raw "(Updated)" file, read when the contents API cannot be.
SP500_UPDATED_CSV_URL = ("https://raw.githubusercontent.com/fja05680/sp500/master/"
                         "S%26P%20500%20Historical%20Components%20%26%20Changes"
                         "%20(Updated).csv")
WIKIPEDIA_SP500_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
_MEMBERSHIP_FILE = re.compile(r"^S&P 500 Historical Components & Changes.*\.csv$")
_FILE_DATE = re.compile(r"\((\d{2})-(\d{2})-(\d{4})\)")

#: A continuing company's old S&P ticker -> the symbol Alpaca's bars carry it
#: under today. Only ticker changes; an acquired or delisted name is left as
#: it is (it has no bars after it leaves, and the engine skips it). Verify
#: against Alpaca before trusting a window that spans one of these dates.
#: A ticker in today's S&P list (universe.SP500_SYMBOLS, ST's May 2026 list)
#: is current and is never a key here: Fiserv is FISV again and Paramount
#: Skydance is PSKY, so their older tickers map forward to those (FW-str (b)).
RENAME_MAP = {
    "ABC": "COR",      # AmerisourceBergen -> Cencora, 2023-08-30
    "ANTM": "ELV",     # Anthem -> Elevance Health, 2022-06-28
    "ARNC": "HWM",     # Arconic Inc. -> Howmet Aerospace, 2020-04-01
    "BBT": "TFC",      # BB&T -> Truist, 2019-12-09
    "BLL": "BALL",     # Ball Corp, 2022-05-17
    "CBS": "PSKY",     # CBS -> VIAC 2019-12-05 -> PARA 2022-02-16 -> PSKY Aug 2025
    "CDAY": "DAY",     # Ceridian -> Dayforce, 2024-02-01
    "COG": "CTRA",     # Cabot Oil & Gas -> Coterra, 2021-10-01
    "CTL": "LUMN",     # CenturyLink -> Lumen, 2020-09-18
    "DISCA": "WBD",    # Discovery -> Warner Bros. Discovery, 2022-04-11
    "DWDP": "DD",      # DowDuPont -> DuPont de Nemours, 2019-06-03
    "FB": "META",      # Facebook -> Meta Platforms, 2022-06-09
    "FBHS": "FBIN",    # Fortune Brands Home & Security -> Innovations, 2022-12-15
    "FI": "FISV",      # Fiserv: FI from 2023-06-07, FISV again from late 2025
    "FLT": "CPAY",     # FleetCor -> Corpay, 2024-03-25
    "HCP": "DOC",      # HCP -> Healthpeak (PEAK) 2019-11-05 -> DOC 2024-03-04
    "HRS": "LHX",      # Harris -> L3Harris, 2019-07-01
    "JEC": "J",        # Jacobs Engineering, 2019-12-10
    "LB": "BBWI",      # L Brands -> Bath & Body Works, 2021-08-03
    "MMC": "MRSH",     # Marsh & McLennan -> Marsh McLennan (MRSH), 2026-01-14
    "MYL": "VTRS",     # Mylan -> Viatris, 2020-11-16
    "NLOK": "GEN",     # NortonLifeLock -> Gen Digital, 2022-11-08
    "PARA": "PSKY",    # Paramount Global -> Paramount Skydance, Aug 2025
    "PEAK": "DOC",     # Healthpeak, 2024-03-04
    "PKI": "RVTY",     # PerkinElmer -> Revvity, 2023-05-16
    "RE": "EG",        # Everest Re -> Everest Group, 2023-07-10
    "SYMC": "GEN",     # Symantec -> NortonLifeLock (NLOK) 2019-11-04 -> GEN
    "UTX": "RTX",      # United Technologies -> Raytheon Technologies, 2020-04-03
    "VIAC": "PSKY",    # ViacomCBS -> PARA 2022-02-16 -> Paramount Skydance Aug 2025
    "WLTW": "WTW",     # Willis Towers Watson, 2022-01-04
}

#: The shape of an Alpaca US equity symbol, dot for a share class (BRK.B).
_TICKER = re.compile(r"[A-Z]{1,6}(\.[A-Z]{1,2})?")

#: Wikipedia's GICS sector names -> the Yahoo sector names ST's cap was built
#: on (yfinance info.sector). Both then go through sectors.normalize_sector,
#: so a name from either source lands on the same label.
GICS_TO_YAHOO = {
    "Information Technology": "Technology",
    "Consumer Discretionary": "Consumer Cyclical",
    "Consumer Staples": "Consumer Defensive",
    "Health Care": "Healthcare",
    "Financials": "Financial Services",
    "Materials": "Basic Materials",
    "Communication Services": "Communication Services",
    "Industrials": "Industrials",
    "Energy": "Energy",
    "Utilities": "Utilities",
    "Real Estate": "Real Estate",
}


class RefdataUnavailable(RuntimeError):
    """A source could not provide its rows."""


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


# -- VIX ---------------------------------------------------------------------

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


def load_vix(fetch, start, skipped=None, *, log=print) -> list:
    """VIX rows from `start` on, from Cboe, else FRED. The bad rows of the
    source used, from `start` on, are recorded in `skipped`. Raises
    RefdataUnavailable when neither source gives a row."""
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
    log(f"Cboe VIX unavailable ({reason}); falling back to FRED VIXCLS")
    fred_skips = []
    try:
        rows = vix_rows(parse_fred_vix(fetch(FRED_VIX_URL), fred_skips), "fred", start)
    except Exception as exc:
        raise RefdataUnavailable(f"no VIX from Cboe or FRED ({type(exc).__name__}: {exc})")
    if not rows:
        raise RefdataUnavailable("no VIX rows from Cboe or FRED")
    if skipped is not None:
        skipped.extend(_in_window(fred_skips, start))
    return rows


# -- membership --------------------------------------------------------------

def membership_csv_url(fetch, *, log=print) -> str:
    """The newest fja05680/sp500 historical-components CSV: the "(Updated)"
    file when there is one, else the newest "(MM-DD-YYYY)" file, found through
    GitHub's contents API. The raw "(Updated)" URL when the API fails."""
    try:
        listing = json.loads(fetch(SP500_CONTENTS_URL))
        files = [e for e in listing if isinstance(e, dict)
                 and _MEMBERSHIP_FILE.match(str(e.get("name") or ""))
                 and e.get("download_url")]
        if files:
            return max(files, key=_membership_file_rank)["download_url"]
        reason = "no historical-components CSV listed"
    except Exception as exc:
        reason = f"{type(exc).__name__}: {exc}"
    log(f"fja05680/sp500 contents unavailable ({reason}); falling back to "
        f"{SP500_UPDATED_CSV_URL}")
    return SP500_UPDATED_CSV_URL


def _membership_file_rank(entry):
    name = str(entry.get("name") or "")
    m = _FILE_DATE.search(name)
    dated = date(int(m.group(3)), int(m.group(1)), int(m.group(2))) if m else date.min
    return ("(updated)" in name.lower(), dated, name)


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
    """(former, malformed) for the operator's Alpaca check (see the builder's
    --help). former: members since --start that are not in the newest list and
    were not renamed onto a current symbol; malformed: symbols that are not the
    shape of an Alpaca equity symbol."""
    union = members_union(rows)
    latest = set(max(rows, key=lambda r: r["date"])["members"]) if rows else set()
    former = sorted(s for s in union if s not in latest)
    malformed = sorted(s for s in union if not _TICKER.fullmatch(s))
    return former, malformed


# -- sectors -----------------------------------------------------------------

def gics_sector(name):
    """A GICS sector name as the normalised label a Yahoo name gets, or None."""
    yahoo = GICS_TO_YAHOO.get(str(name or "").strip())
    return sectors.normalize_sector(yahoo) if yahoo else None


class _ConstituentsTable(HTMLParser):
    """The text of every cell of the table with id="constituents"."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows, self._depth, self._row, self._cell = [], 0, None, None

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            if self._depth or dict(attrs).get("id") == "constituents":
                self._depth += 1
        elif self._depth == 1 and tag == "tr":
            self._row = []
        elif self._depth == 1 and tag in ("td", "th") and self._row is not None:
            self._cell = []

    def handle_endtag(self, tag):
        if tag == "table" and self._depth:
            self._depth -= 1
        elif self._depth == 1 and tag in ("td", "th") and self._cell is not None:
            self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif self._depth == 1 and tag == "tr" and self._row is not None:
            self.rows.append(self._row)
            self._row = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


def parse_wikipedia_sectors(html) -> dict:
    """{symbol: label} from Wikipedia's S&P 500 constituents table, the GICS
    Sector column mapped through gics_sector. Symbols are Alpaca's spelling."""
    table = _ConstituentsTable()
    table.feed(str(html or ""))
    if not table.rows:
        return {}
    header = [h.lower() for h in table.rows[0]]
    try:
        sym_col = header.index("symbol")
        sec_col = header.index("gics sector")
    except ValueError:
        return {}
    out = {}
    for row in table.rows[1:]:
        if len(row) <= max(sym_col, sec_col):
            continue
        sym, label = norm_symbol(row[sym_col]), gics_sector(row[sec_col])
        if sym and label:
            out[sym] = label
    return out


def wikipedia_sectors(fetch, *, log=print) -> dict:
    """parse_wikipedia_sectors of the live page; {} when it cannot be read."""
    try:
        found = parse_wikipedia_sectors(fetch(WIKIPEDIA_SP500_URL))
        if found:
            return found
        reason = "no constituents table"
    except Exception as exc:
        reason = f"{type(exc).__name__}: {exc}"
    log(f"Wikipedia S&P 500 sectors unavailable ({reason}); yfinance only")
    return {}


def yf_sector(symbol):
    import yfinance as yf
    return (yf.Ticker(str(symbol).replace(".", "-")).info or {}).get("sector")


def sector_row(symbol, *, sector_of, as_of, wikipedia=None):
    """One SwingSectorMap row for `symbol`: ST's override, else Wikipedia's
    GICS sector, else `sector_of`'s (yfinance) answer normalised. None when
    none of them knows it."""
    sym = norm_symbol(symbol)
    if sym in sectors._SECTOR_OVERRIDES:
        return {"id": sym, "symbol": sym, "sector": sectors._SECTOR_OVERRIDES[sym],
                "as_of": as_of, "source": "override"}
    if wikipedia and wikipedia.get(sym):
        return {"id": sym, "symbol": sym, "sector": wikipedia[sym], "as_of": as_of,
                "source": "wikipedia"}
    try:
        raw = sector_of(sym)
    except Exception:
        raw = None
    if not raw:
        return None
    return {"id": sym, "symbol": sym, "sector": sectors.normalize_sector(raw),
            "as_of": as_of, "source": "yfinance"}


def sector_rows(symbols, *, sector_of, as_of, wikipedia=None):
    rows, unknown = [], []
    for sym in sorted({norm_symbol(s) for s in symbols if str(s or "").strip()}):
        row = sector_row(sym, sector_of=sector_of, as_of=as_of, wikipedia=wikipedia)
        if row is None:
            unknown.append(sym)
        else:
            rows.append(row)
    return rows, unknown
