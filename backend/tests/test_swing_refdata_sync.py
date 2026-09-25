"""The swing lane's own reference data (swing_trader.refdata_sync).

No network: every fetch and every sector lookup is injected. `store` is the
FakeStore, or real Postgres under PG_TEST_DSN.
"""
import importlib.util
import json
import os
import sys
from datetime import date, timedelta

import pytest

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ROOT = os.path.dirname(_BACKEND)
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from swing_trader import refdata, refdata_build, refdata_sync, sectors  # noqa: E402

START, END, TODAY = date(2021, 7, 5), date(2021, 9, 30), date(2026, 9, 25)
EXTRAS = {"SPY", "QQQ", "XLP", "XLU", "XLV", "GLD", "SHY"}
UPDATED_URL = "https://example.test/sp500-updated.csv"
DATED_URL = "https://example.test/sp500-08-01-2026.csv"
LISTING = json.dumps([
    {"name": "README.md", "download_url": "https://example.test/README.md"},
    {"name": "S&P 500 Historical Components & Changes.csv",
     "download_url": "https://example.test/sp500.csv"},
    {"name": "S&P 500 Historical Components & Changes (Updated).csv",
     "download_url": UPDATED_URL},
    {"name": "S&P 500 Historical Components & Changes(08-01-2026).csv",
     "download_url": DATED_URL},
])
#: FB is renamed to META on read; the change dated END is visible only after
#: the window, and 2026-08-18 keeps the table fresh.
MEMBERSHIP = ('date,tickers\n'
              '2019-06-03,"AAPL,BRK.B,FB,GONE"\n'
              '2021-01-04,"AAPL,BRK.B,FB,OLDCO"\n'
              '2021-08-02,"AAPL,BRK.B,FB,NEWCO"\n'
              '2021-09-30,"AAPL,BRK.B,FB,LATE"\n'
              '2026-08-18,"AAPL,BRK.B,META,NOW"\n')
WINDOW_MEMBERS = {"AAPL", "BRK.B", "META", "OLDCO", "NEWCO"}
#: yfinance, the fallback. AAPL, BRK.B and META are on Wikipedia and must
#: never be asked; the rest are not.
SECTORS = {"AAPL": "Should Not Be Asked", "BRK.B": "Should Not Be Asked",
           "META": "Should Not Be Asked", "OLDCO": "Industrials",
           "NEWCO": "Utilities", "XLU": "Utilities"}
#: Wikipedia's constituents table (today's members only). The second table
#: is the page's "changes" table and must be ignored.
WIKI = """<html><body><p>S&amp;P 500</p>
<table class="wikitable sortable" id="constituents"><tbody>
<tr><th><a href="/wiki/Ticker_symbol">Symbol</a></th><th>Security</th>
<th><a href="/wiki/GICS">GICS</a> Sector</th><th>GICS Sub-Industry</th></tr>
<tr><td><a href="https://www.nasdaq.com/AAPL">AAPL</a></td><td>Apple Inc.</td>
<td>Information Technology</td><td>Technology Hardware</td></tr>
<tr><td>BRK.B</td><td>Berkshire Hathaway</td><td>Financials</td><td>Insurance</td></tr>
<tr><td>META</td><td>Meta Platforms</td><td>Communication Services</td><td>Media</td></tr>
</tbody></table>
<table class="wikitable" id="changes"><tr><th>Symbol</th><th>GICS Sector</th></tr>
<tr><td>OLDCO</td><td>Energy</td></tr></table></body></html>"""


def weekdays(lo, hi):
    out, d = [], lo
    while d <= hi:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def cboe(lo=date(2021, 1, 1), hi=date(2026, 9, 24)):
    lines = ["DATE,OPEN,HIGH,LOW,CLOSE"]
    lines += [f"{d:%m/%d/%Y},20,21,19,{20 + d.day / 100:.2f}" for d in weekdays(lo, hi)]
    return "\n".join(lines) + "\n"


class Fetch:
    """Serves `pages` by URL and records every call. A page that is an
    exception is raised; an unknown URL fails the test loudly."""

    def __init__(self, pages=None):
        self.pages = dict(pages or {})
        self.calls = []

    def __call__(self, url):
        self.calls.append(url)
        if url not in self.pages:
            raise AssertionError(f"unexpected fetch {url}")
        page = self.pages[url]
        if isinstance(page, BaseException):
            raise page
        return page


def all_pages():
    return {refdata_build.CBOE_VIX_URL: cboe(),
            refdata_build.SP500_CONTENTS_URL: LISTING,
            UPDATED_URL: MEMBERSHIP,
            refdata_build.WIKIPEDIA_SP500_URL: "<html><table id='constituents'></table></html>"}


def pages_with_wikipedia():
    return {**all_pages(), refdata_build.WIKIPEDIA_SP500_URL: WIKI}


class Sectors:
    def __init__(self, table=None, clock=None):
        self.table = dict(SECTORS if table is None else table)
        self.calls = []
        self.clock = clock

    def __call__(self, symbol):
        self.calls.append(symbol)
        if self.clock is not None:
            self.clock.now += 1.0
        return self.table.get(symbol)


class Recording:
    """The store, with every inserted (table, id) recorded."""

    def __init__(self, inner):
        self.inner = inner
        self.inserted = []

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def insert(self, table, docs, **kwargs):
        docs = [docs] if isinstance(docs, dict) else list(docs)
        self.inserted.extend((table, d["id"]) for d in docs)
        return self.inner.insert(table, docs, **kwargs)


class Logs(list):
    def __call__(self, message, color="white"):
        self.append((color, str(message)))

    def text(self, color=None):
        return "\n".join(m for c, m in self if color is None or c == color)


def prep(store, *, start=START, end=END, today=TODAY, fetch=None, sector_of=None,
         log=None, **kwargs):
    return refdata_sync.sync_reference_data(
        start, end, store=store, today=today,
        fetch=fetch if fetch is not None else Fetch(all_pages()),
        sector_of=sector_of if sector_of is not None else Sectors(),
        log=log if log is not None else Logs(), **kwargs)


def members(store, rows):
    store.insert(refdata.MEMBERSHIP_TABLE, [
        {"id": refdata.membership_id("SPX", d), "index": "SPX", "date": d,
         "members": list(m)} for d, m in rows], conflict="replace")


def vix(store, days):
    store.insert(refdata.MACRO_TABLE, [
        {"id": refdata.macro_id("VIX", d), "series": "VIX", "date": d.isoformat(),
         "close": 20.0, "source": "cboe"} for d in days], conflict="replace")


def stored_ids(store, table, prefix):
    return sorted(r["id"] for r in store.run(store.between(table, f"{prefix}|", f"{prefix}}}")))


# -- (a)-(d) from an empty store ----------------------------------------------

def test_an_empty_store_is_filled_and_the_window_symbols_returned(store):
    rec, fetch, yahoo, logs = Recording(store), Fetch(pages_with_wikipedia()), Sectors(), Logs()
    symbols = prep(rec, fetch=fetch, sector_of=yahoo, log=logs)

    assert symbols == sorted(WINDOW_MEMBERS | EXTRAS)
    # VIX: exactly the window's rows, [start - 30 days, end].
    want = [refdata.macro_id("VIX", d) for d in weekdays(START - timedelta(days=30), END)]
    assert stored_ids(store, refdata.MACRO_TABLE, "VIX") == want
    assert refdata.vix_before(store, START)[1] is None
    # Membership: the row in effect at start, and every change after it.
    assert stored_ids(store, refdata.MEMBERSHIP_TABLE, "SPX") == [
        "SPX|2021-01-04", "SPX|2021-08-02", "SPX|2021-09-30", "SPX|2026-08-18"]
    assert "NEWCO" in refdata.members_before(store, "2021-08-03")
    assert "META" in refdata.members_before(store, "2021-07-05")          # FB renamed
    # The "(Updated)" file was the one read; Wikipedia once, for every sector.
    assert fetch.calls == [refdata_build.CBOE_VIX_URL, refdata_build.SP500_CONTENTS_URL,
                           UPDATED_URL, refdata_build.WIKIPEDIA_SP500_URL]
    # Sectors: ST's overrides, then Wikipedia, and yfinance only for the rest.
    assert sorted(yahoo.calls) == ["NEWCO", "OLDCO", "SHY", "XLP", "XLU", "XLV"]
    assert refdata.sector_map(store, sorted(symbols)) == {
        "AAPL": "technology", "BRK.B": "financial_services",
        "META": "communication_services", "OLDCO": "industrials", "NEWCO": "utilities",
        "XLU": "utilities", "SPY": "broad_market", "QQQ": "broad_market",
        "GLD": "commodity"}
    assert store.get(refdata.SECTOR_TABLE, "SPY")["source"] == "override"
    assert store.get(refdata.SECTOR_TABLE, "AAPL") == {
        "id": "AAPL", "symbol": "AAPL", "sector": "technology",
        "as_of": "2026-09-25", "source": "wikipedia"}
    assert store.get(refdata.SECTOR_TABLE, "OLDCO")["source"] == "yfinance"
    # One summary line with every count.
    summary = [m for _c, m in logs if "symbols" in m and "VIX rows added" in m]
    n_vix = len(want)
    assert len(summary) == 1
    assert (f"VIX rows added {n_vix}" in summary[0]
            and "membership rows added 4" in summary[0]
            and "sectors added 9" in summary[0]
            and f"{len(symbols)} symbols" in summary[0])


def test_a_covered_store_fetches_nothing_and_writes_nothing(store):
    prep(store)                                            # fills everything
    rec, logs, fetch, yahoo = Recording(store), Logs(), Fetch(), Sectors()
    # 3 days on: the failed lookups (XLP, XLV, SHY) are still resting.
    again = prep(rec, today=TODAY + timedelta(days=3), fetch=fetch, sector_of=yahoo,
                 log=logs)
    assert again == sorted(WINDOW_MEMBERS | EXTRAS)
    assert fetch.calls == [] and yahoo.calls == []
    assert rec.inserted == []
    assert "yellow" not in {c for c, _m in logs}


def test_only_the_missing_vix_rows_are_written(store):
    have = weekdays(START - timedelta(days=30), date(2021, 8, 13))
    vix(store, have)
    rec = Recording(store)
    prep(rec)
    written = sorted(i for t, i in rec.inserted if t == refdata.MACRO_TABLE)
    assert written == [refdata.macro_id("VIX", d)
                       for d in weekdays(date(2021, 8, 14), END)]


def test_a_vix_gap_inside_the_window_is_refilled(store):
    days = weekdays(START - timedelta(days=30), END)
    gap = [d for d in days if date(2021, 8, 2) <= d <= date(2021, 8, 13)]
    vix(store, [d for d in days if d not in gap])
    rec = Recording(store)
    prep(rec)
    assert sorted(i for t, i in rec.inserted if t == refdata.MACRO_TABLE) == [
        refdata.macro_id("VIX", d) for d in gap]


# -- (b) when membership is refreshed -----------------------------------------------

def _fresh_vix(store):
    vix(store, weekdays(START - timedelta(days=30), END))


@pytest.mark.parametrize("rows, end, refetch", [
    # Newest row 2021-01-04, window ends 2021-09-30: more than 45 days old.
    ([("2019-06-03", ["AAPL"]), ("2021-01-04", ["AAPL"])], END, True),
    # Newest row within 45 days of the window's end: fresh.
    ([("2019-06-03", ["AAPL"]), ("2021-08-20", ["AAPL"])], END, False),
    # Fresh, but no row before the window's start.
    ([("2021-08-20", ["AAPL"])], END, True),
])
def test_membership_is_refetched_only_when_stale_or_missing_at_start(
        store, rows, end, refetch):
    members(store, rows)
    _fresh_vix(store)
    fetch = Fetch(all_pages())
    prep(store, end=end, fetch=fetch, sector_of=Sectors({}))
    assert (refdata_build.SP500_CONTENTS_URL in fetch.calls) is refetch
    assert refdata_build.CBOE_VIX_URL not in fetch.calls


def test_a_backtest_ending_in_the_future_ages_membership_from_today(store):
    members(store, [("2021-01-04", ["AAPL"]), ("2026-08-18", ["AAPL"])])
    vix(store, weekdays(date(2026, 6, 1), date(2026, 9, 24)))
    fetch = Fetch(all_pages())
    prep(store, start=date(2026, 7, 6), end=date(2026, 12, 31), fetch=fetch,
         sector_of=Sectors({}))
    assert fetch.calls == [refdata_build.WIKIPEDIA_SP500_URL]       # sectors only


# -- (e) best-effort -----------------------------------------------------------

def test_a_failed_fetch_logs_and_returns_the_stored_symbols(store):
    members(store, [("2021-01-04", ["AAPL", "OLDCO"]), ("2021-08-02", ["AAPL", "NEWCO"])])
    down = RuntimeError("HTTP 503")
    fetch = Fetch({refdata_build.CBOE_VIX_URL: down, refdata_build.FRED_VIX_URL: down,
                   refdata_build.SP500_CONTENTS_URL: down,
                   refdata_build.SP500_UPDATED_CSV_URL: down,
                   refdata_build.WIKIPEDIA_SP500_URL: down})
    logs = Logs()

    def sector_of(symbol):
        raise RuntimeError("yahoo 404")

    symbols = prep(store, fetch=fetch, sector_of=sector_of, log=logs)
    assert symbols == sorted({"AAPL", "OLDCO", "NEWCO"} | EXTRAS)
    yellow = logs.text("yellow")
    assert "VIX" in yellow and "membership" in yellow and "503" in yellow
    # Both VIX sources and the raw-URL fallback were tried.
    assert refdata_build.FRED_VIX_URL in fetch.calls
    assert refdata_build.SP500_UPDATED_CSV_URL in fetch.calls


class Broken:
    """A store whose every call raises: the database is unreachable."""

    def __getattr__(self, name):
        def fail(*_a, **_k):
            raise ConnectionError("server7 unreachable")
        return fail


def test_an_unreachable_store_never_raises_into_the_engine():
    logs = Logs()
    symbols = prep(Broken(), fetch=Fetch(all_pages()), log=logs)
    assert symbols == sorted(EXTRAS)
    assert "server7 unreachable" in logs.text("yellow")


def test_an_interrupt_is_not_swallowed(store):
    """Best-effort means every Exception; a stop (KeyboardInterrupt,
    SystemExit) still ends the run."""
    logs = Logs()

    def boom(*_a, **_k):
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        prep(store, fetch=boom, log=logs)


# -- (c) the union rule is lab_watchlist's ----------------------------------------------

def _lab_setup():
    path = os.path.join(_ROOT, "scripts", "swing_lab_setup.py")
    spec = importlib.util.spec_from_file_location("_swing_lab_setup_for_prep", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_the_symbol_union_matches_lab_watchlist(store):
    lab = _lab_setup()
    members(store, [("2021-01-04", ["AAPL", "BRK.B", "OLD", "brk-b"]),
                    ("2021-09-20", ["AAPL", "BRK.B", "NEW"]),
                    ("2026-09-18", ["AAPL", "BRK.B", "LATE"])])
    down = RuntimeError("offline")
    fetch = Fetch({refdata_build.CBOE_VIX_URL: down, refdata_build.FRED_VIX_URL: down,
                   refdata_build.WIKIPEDIA_SP500_URL: down})
    got = prep(store, start=date(2021, 7, 1), end=date(2026, 9, 18), fetch=fetch,
               sector_of=Sectors({}))
    assert got == lab.lab_watchlist(store, "2021-07-01", "2026-09-18")
    assert "LATE" not in got


def test_the_defensive_universe_is_the_lanes(store):
    prep(store)
    got = prep(store, fetch=Fetch(), sector_of=Sectors({}),
               defensive_universe=["tlt", " gld ", "BRK-B", ""])
    assert got == sorted(WINDOW_MEMBERS | {"SPY", "QQQ", "TLT", "GLD", "BRK.B"})


# -- (d) the sector budget and the retry rule ------------------------------------------

class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def _many_members(store, n):
    names = [f"S{i:03d}" for i in range(n)]
    members(store, [("2021-01-04", names), ("2021-08-20", names)])
    _fresh_vix(store)
    return names


def test_the_sector_budget_is_respected(store):
    _many_members(store, 120)
    clock, logs = Clock(), Logs()
    yahoo = Sectors({f"S{i:03d}": "Technology" for i in range(120)}, clock=clock)
    prep(store, sector_of=yahoo, log=logs, clock=clock, sector_budget_s=10)
    assert len(yahoo.calls) == 10
    assert "budget" in logs.text()
    got = refdata.sector_map(store, [f"S{i:03d}" for i in range(120)])
    assert len(got) == 10
    # The next run picks up where this one stopped.
    yahoo.calls.clear()
    prep(store, sector_of=yahoo, log=Logs(), clock=clock, sector_budget_s=10)
    assert yahoo.calls and not set(yahoo.calls) & set(got)


def test_sector_progress_is_logged_every_50_lookups(store):
    _many_members(store, 120)
    logs, yahoo = Logs(), Sectors({})
    prep(store, sector_of=yahoo, log=logs)
    progress = [m for _c, m in logs if "sector lookups" in m and "/" in m]
    assert len(progress) == len(yahoo.calls) // 50 >= 2


def test_an_unknown_yfinance_sector_is_retried_at_most_once_per_7_days(store):
    """Coordinator addition to 2(d): a stored empty or 'unknown' yfinance
    sector is retryable, at most once per 7 days (retried_at, else as_of), so
    a delisted name is not re-queried on every run. ST's overrides win."""
    members(store, [("2021-01-04", ["BK", "FISV", "K", "OLDCO", "HOLX"]),
                    ("2021-08-20", ["BK", "FISV", "K", "OLDCO", "HOLX"])])
    _fresh_vix(store)
    y = {"source": "yfinance"}
    store.insert(refdata.SECTOR_TABLE, [
        {"id": "BK", "symbol": "BK", "sector": "unknown", "as_of": "2026-09-20", **y},
        {"id": "FISV", "symbol": "FISV", "sector": "", "as_of": "2026-09-10", **y},
        {"id": "K", "symbol": "K", "sector": "unknown", "as_of": "2026-09-01",
         "retried_at": "2026-09-22", **y},
        {"id": "OLDCO", "symbol": "OLDCO", "sector": "unknown", "as_of": "2026-09-01", **y},
        {"id": "HOLX", "symbol": "HOLX", "sector": "healthcare", "as_of": "2026-01-01", **y},
        {"id": "SPY", "symbol": "SPY", "sector": "unknown", "as_of": "2026-09-24", **y},
    ], conflict="replace")
    yahoo = Sectors({"FISV": "Technology", "BK": "Financial Services",
                       "SPY": "Should Not Be Asked"})
    prep(store, sector_of=yahoo)

    tested = {"BK", "FISV", "K", "OLDCO", "HOLX", "SPY"}
    assert set(yahoo.calls) & tested == {"FISV", "OLDCO"}
    assert store.get(refdata.SECTOR_TABLE, "FISV") == {
        "id": "FISV", "symbol": "FISV", "sector": "technology", "as_of": "2026-09-25",
        "source": "yfinance"}
    oldco = store.get(refdata.SECTOR_TABLE, "OLDCO")
    assert (oldco["sector"], oldco["source"], oldco["retried_at"]) == (
        "unknown", "yfinance", "2026-09-25")
    assert store.get(refdata.SECTOR_TABLE, "SPY")["source"] == "override"
    # A failed lookup reads as absent to the lane, so its live yfinance
    # fallback still runs; only real sectors are returned.
    assert refdata.sector_map(store, sorted(tested)) == {
        "FISV": "technology", "HOLX": "healthcare", "SPY": "broad_market"}

    # 6 days later OLDCO is still resting; BK (as_of 09-20) comes due at 09-27.
    yahoo.calls.clear()
    prep(store, sector_of=yahoo, today=date(2026, 10, 1))
    assert set(yahoo.calls) & tested == {"BK", "K"}
    assert store.get(refdata.SECTOR_TABLE, "BK")["sector"] == "financial_services"
    yahoo.calls.clear()
    prep(store, sector_of=yahoo, today=date(2026, 10, 2))
    assert set(yahoo.calls) & tested == {"OLDCO"}


def test_a_symbol_never_looked_up_gets_a_retry_marker(store):
    """A first lookup that fails leaves a marker row, so the next run does
    not ask again for 7 days; the lane still reads the symbol as absent."""
    members(store, [("2021-01-04", ["GONE"]), ("2021-08-20", ["GONE"])])
    _fresh_vix(store)
    yahoo = Sectors({})
    prep(store, sector_of=yahoo)
    assert "GONE" in yahoo.calls
    row = store.get(refdata.SECTOR_TABLE, "GONE")
    assert (row["sector"], row["source"], row["retried_at"]) == (
        "unknown", "yfinance", "2026-09-25")
    assert refdata.sector_map(store, ["GONE"]) == {}
    yahoo.calls.clear()
    prep(store, sector_of=yahoo, today=TODAY + timedelta(days=6))
    assert "GONE" not in yahoo.calls


# -- the membership CSV is found, not named -------------------------------------------

def test_the_updated_file_is_preferred():
    fetch = Fetch({refdata_build.SP500_CONTENTS_URL: LISTING})
    assert refdata_build.membership_csv_url(fetch, log=Logs()) == UPDATED_URL


def test_without_an_updated_file_the_newest_dated_one_wins():
    listing = json.dumps([
        {"name": "S&P 500 Historical Components & Changes(01-17-2026).csv",
         "download_url": "https://example.test/jan.csv"},
        {"name": "S&P 500 Historical Components & Changes(08-01-2025).csv",
         "download_url": "https://example.test/aug.csv"},
        {"name": "sp500.csv", "download_url": "https://example.test/sp500.csv"},
    ])
    fetch = Fetch({refdata_build.SP500_CONTENTS_URL: listing})
    assert refdata_build.membership_csv_url(fetch, log=Logs()) == "https://example.test/jan.csv"


@pytest.mark.parametrize("page", [RuntimeError("rate limited"), "not json", "[]"])
def test_the_contents_api_failing_falls_back_to_the_raw_url(page):
    logs = Logs()
    fetch = Fetch({refdata_build.SP500_CONTENTS_URL: page})
    assert refdata_build.membership_csv_url(fetch, log=logs) == \
        refdata_build.SP500_UPDATED_CSV_URL
    assert "falling back" in logs.text()


# -- one implementation ----------------------------------------------------------------

def test_the_builder_script_uses_the_moved_implementation():
    path = os.path.join(_ROOT, "scripts", "build_swing_reference_data.py")
    spec = importlib.util.spec_from_file_location("_swing_refdata_builder_for_prep", path)
    b = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = b
    spec.loader.exec_module(b)
    for name in ("RENAME_MAP", "CBOE_VIX_URL", "FRED_VIX_URL", "fetch_text",
                 "parse_cboe_vix", "parse_fred_vix", "vix_rows", "parse_membership",
                 "membership_rows", "members_union", "ticker_report", "sector_rows",
                 "yf_sector"):
        assert getattr(b, name) is getattr(refdata_build, name), name


# -- Wikipedia sectors land on ST's (Yahoo) labels ------------------------------------

@pytest.mark.parametrize("gics, yahoo_name", [
    ("Information Technology", "Technology"),
    ("Consumer Discretionary", "Consumer Cyclical"),
    ("Consumer Staples", "Consumer Defensive"),
    ("Health Care", "Healthcare"),
    ("Financials", "Financial Services"),
    ("Materials", "Basic Materials"),
    ("Communication Services", "Communication Services"),
    ("Industrials", "Industrials"),
    ("Energy", "Energy"),
    ("Utilities", "Utilities"),
    ("Real Estate", "Real Estate"),
])
def test_a_gics_sector_gets_the_label_its_yahoo_name_gets(gics, yahoo_name):
    assert refdata_build.gics_sector(gics) == sectors.normalize_sector(yahoo_name)


def test_the_labels_are_st_s():
    assert [refdata_build.gics_sector(g) for g in (
        "Information Technology", "Health Care", "Consumer Discretionary")] == [
        "technology", "healthcare", "consumer_cyclical"]
    assert refdata_build.gics_sector("Not A Sector") is None


def test_only_the_constituents_table_is_read():
    got = refdata_build.parse_wikipedia_sectors(WIKI)
    assert got == {"AAPL": "technology", "BRK.B": "financial_services",
                   "META": "communication_services"}
    assert refdata_build.parse_wikipedia_sectors("<html>no table</html>") == {}


def test_wikipedia_wins_over_yfinance_and_says_so(store):
    members(store, [("2021-01-04", ["AAPL", "OLDCO"]), ("2021-08-20", ["AAPL", "OLDCO"])])
    _fresh_vix(store)
    yahoo = Sectors()
    prep(store, fetch=Fetch(pages_with_wikipedia()), sector_of=yahoo)
    assert "AAPL" not in yahoo.calls and "OLDCO" in yahoo.calls
    assert store.get(refdata.SECTOR_TABLE, "AAPL")["source"] == "wikipedia"
    assert store.get(refdata.SECTOR_TABLE, "OLDCO")["source"] == "yfinance"


# -- live: at most one background sync per process per NY day -------------------------

class Threads:
    def __init__(self):
        self.started = []

    def __call__(self, *, target, name, daemon):
        test = self

        class _Thread:
            def start(self):
                test.started.append((name, daemon))
                target()
        return _Thread()


def test_the_live_sync_runs_in_a_daemon_thread_once_per_ny_day(monkeypatch):
    monkeypatch.setitem(refdata_sync._background_day, "day", None)
    threads, calls = Threads(), []

    def sync(start, end, *, defensive_universe=None):
        calls.append((start, end, defensive_universe))

    day = date(2026, 9, 25)
    for _ in range(3):
        refdata_sync.start_background_sync(day, defensive_universe=["XLP"], sync=sync,
                                           thread_factory=threads)
    assert calls == [(day, day, ["XLP"])]
    assert threads.started == [("swing-refdata-2026-09-25", True)]
    assert refdata_sync.start_background_sync(date(2026, 9, 28), sync=sync,
                                              thread_factory=threads) is True
    assert len(calls) == 2


def test_a_live_sync_that_raises_is_logged_not_raised(monkeypatch):
    monkeypatch.setitem(refdata_sync._background_day, "day", None)
    logs = Logs()

    def sync(*_a, **_k):
        raise RuntimeError("boom")

    assert refdata_sync.start_background_sync(date(2026, 9, 25), sync=sync,
                                              thread_factory=Threads(), log=logs)
    assert "boom" in logs.text("yellow")
