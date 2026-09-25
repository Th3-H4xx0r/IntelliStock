"""The reference-data builder writes exactly what the backtest readers read
(spec §7). No network: every fetch is injected."""
import importlib.util
import os
import sys
from datetime import date

import pytest

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ROOT = os.path.dirname(_BACKEND)
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from swing_trader import refdata  # noqa: E402


def _script():
    path = os.path.join(_ROOT, "scripts", "build_swing_reference_data.py")
    spec = importlib.util.spec_from_file_location("_swing_refdata_builder", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


CBOE = ("DATE,OPEN,HIGH,LOW,CLOSE\n"
        "12/31/2018,26.0,26.5,25.1,25.42\n"
        "01/02/2019,27.54,28.53,23.1,23.22\n"
        "01/03/2019,23.22,25.73,22.8,25.45\n"
        "bad row\n")
FRED = "observation_date,VIXCLS\n2019-01-02,23.22\n2019-01-21,.\n2019-01-22,20.8\n"
MEMBERSHIP = ('date,tickers\n'
              '2018-12-20,"AAPL,BRK.B,FB,HRS"\n'
              '2019-06-03,"AAPL,BRK.B,DD,FB,HRS"\n'
              '2022-06-09,"AAPL,BRK.B,DD,LHX,META"\n')


def fetcher(pages):
    def fetch(url):
        if url not in pages:
            raise AssertionError(f"unexpected fetch {url}")
        page = pages[url]
        if isinstance(page, Exception):
            raise page
        return page
    return fetch


def test_cboe_closes_are_dated_and_bad_rows_dropped():
    b = _script()
    assert b.parse_cboe_vix(CBOE) == [("2018-12-31", 25.42), ("2019-01-02", 23.22),
                                      ("2019-01-03", 25.45)]
    rows = b.vix_rows(b.parse_cboe_vix(CBOE), "cboe", "2019-01-01")
    assert rows[0] == {"id": "VIX|2019-01-02", "series": "VIX", "date": "2019-01-02",
                       "close": 23.22, "source": "cboe"}
    assert len(rows) == 2


def test_fred_is_the_fallback_and_skips_holidays():
    b = _script()
    fetch = fetcher({b.CBOE_VIX_URL: RuntimeError("503"), b.FRED_VIX_URL: FRED})
    rows = b.load_vix(fetch, "2019-01-01")
    assert [(r["date"], r["close"], r["source"]) for r in rows] == [
        ("2019-01-02", 23.22, "fred"), ("2019-01-22", 20.8, "fred")]
    with pytest.raises(SystemExit):
        b.load_vix(fetcher({b.CBOE_VIX_URL: "DATE,CLOSE\n", b.FRED_VIX_URL: "x\n"}),
                   "2019-01-01")


def test_membership_keeps_the_row_in_effect_at_start_and_renames():
    b = _script()
    changes = b.parse_membership(MEMBERSHIP)
    assert changes[0] == ("2018-12-20", ["AAPL", "BRK.B", "LHX", "META"])
    rows = b.membership_rows(changes, "2019-01-01")
    assert [r["id"] for r in rows] == ["SPX|2018-12-20", "SPX|2019-06-03", "SPX|2022-06-09"]
    assert rows[1] == {"id": "SPX|2019-06-03", "index": "SPX", "date": "2019-06-03",
                       "members": ["AAPL", "BRK.B", "DD", "LHX", "META"]}
    assert b.membership_rows(changes, "2020-01-01")[0]["date"] == "2019-06-03"
    assert b.members_union(rows) == ["AAPL", "BRK.B", "DD", "LHX", "META"]
    # A dash spelling is Alpaca's dot, and a duplicate after renaming is dropped.
    assert b.parse_membership('date,tickers\n2020-01-02,"BRK-B,FB,META"\n') == [
        ("2020-01-02", ["BRK.B", "META"])]


def test_overrides_win_and_a_missing_sector_writes_no_row():
    b = _script()
    rows, unknown = b.sector_rows(
        ["AAPL", "SPY", "XLP", "brk-b"], as_of="2026-09-24",
        sector_of={"AAPL": "Technology", "BRK.B": "Financial Services", "XLP": None}.get)
    assert rows == [
        {"id": "AAPL", "symbol": "AAPL", "sector": "technology", "as_of": "2026-09-24",
         "source": "yfinance"},
        {"id": "BRK.B", "symbol": "BRK.B", "sector": "financial_services",
         "as_of": "2026-09-24", "source": "yfinance"},
        {"id": "SPY", "symbol": "SPY", "sector": "broad_market", "as_of": "2026-09-24",
         "source": "override"}]
    assert unknown == ["XLP"]


def test_the_build_is_idempotent_and_the_readers_see_it(store, tmp_path):
    b = _script()
    csv_path = tmp_path / "sp500.csv"
    csv_path.write_text(MEMBERSHIP, encoding="utf-8")
    fetch = fetcher({b.CBOE_VIX_URL: CBOE})
    sector_calls = []

    def sector_of(symbol):
        sector_calls.append(symbol)
        return {"AAPL": "Technology", "DD": "Basic Materials"}.get(symbol)

    argv = ["--start", "2019-01-01", "--membership-csv", str(csv_path)]
    for _ in range(2):
        assert b.main(argv, store=store, fetch=fetch, sector_of=sector_of,
                      today=date(2026, 9, 24)) == 0
    assert len(store.get_all(refdata.MACRO_TABLE, "VIX|2019-01-02", "VIX|2019-01-03")) == 2
    assert refdata.vix_before(store, "2019-01-03") == (23.22, None)
    assert refdata.members_before(store, "2019-06-03") == ["AAPL", "BRK.B", "LHX", "META"]
    assert "DD" in refdata.members_before(store, "2019-06-04")
    smap = refdata.sector_map(store, ["AAPL", "DD", "SPY", "META"])
    assert smap == {"AAPL": "technology", "DD": "basic_materials", "SPY": "broad_market"}
    assert "SPY" not in sector_calls and "XLP" in sector_calls      # defensive ETFs asked


def test_only_vix_needs_no_membership_file(store):
    b = _script()
    assert b.main(["--only", "vix"], store=store, fetch=fetcher({b.CBOE_VIX_URL: CBOE}),
                  sector_of=lambda s: None) == 0
    with pytest.raises(SystemExit) as exit_info:
        b.main(["--only", "membership"], store=store, fetch=fetcher({}),
               sector_of=lambda s: None)
    assert exit_info.value.code == 2


# -- G8a ruling 5 (G2 carry): a non-numeric VIX close is skipped and recorded;
# -- former members without a rename are logged for the operator to verify ------

BAD_CBOE = ("DATE,OPEN,HIGH,LOW,CLOSE\n"
            "01/02/2019,27.54,28.53,23.1,23.22\n"
            "01/03/2019,23.22,25.73,22.8,nan\n"
            "01/04/2019,23.22,25.73,22.8,N/A\n"
            "01/07/2019,23.22,25.73,22.8,inf\n"
            "01/08/2019,23.22,25.73,22.8,0\n"
            "13/45/2019,1,1,1,20.0\n")


def test_a_non_numeric_vix_close_is_skipped_and_recorded():
    b = _script()
    skipped = []
    assert b.parse_cboe_vix(BAD_CBOE, skipped=skipped) == [("2019-01-02", 23.22)]
    assert [s[1] for s in skipped] == ["nan", "N/A", "inf", "0", "20.0"]
    fred_skips = []
    fred = "observation_date,VIXCLS\n2019-01-02,23.22\n2019-01-21,.\n2019-01-22,x\n"
    assert b.parse_fred_vix(fred, skipped=fred_skips) == [("2019-01-02", 23.22)]
    assert fred_skips == [("2019-01-22", "x")]         # "." is FRED's holiday, not a skip


def test_the_build_never_writes_a_non_numeric_close_and_says_so(store, capsys):
    b = _script()
    assert b.main(["--only", "vix"], store=store, fetch=fetcher({b.CBOE_VIX_URL: BAD_CBOE}),
                  sector_of=lambda s: None) == 0
    rows = store.get_all(refdata.MACRO_TABLE, "VIX|2019-01-02", "VIX|2019-01-03",
                         "VIX|2019-01-04", "VIX|2019-01-07", "VIX|2019-01-08")
    assert [r["id"] for r in rows] == ["VIX|2019-01-02"]
    out = capsys.readouterr().out
    assert "skipped 5 VIX rows" in out and "2019-01-03=nan" in out
    # The reader never lands on a bad latest row: the good close is what it sees.
    assert refdata.vix_before(store, "2019-01-05") == (23.22, None)


def test_former_members_without_a_rename_are_logged_for_verification(store, tmp_path, capsys):
    b = _script()
    csv_path = tmp_path / "sp500.csv"
    csv_path.write_text('date,tickers\n'
                        '2019-01-02,"AAPL,FB,OLDCO,BAD$"\n'
                        '2020-01-02,"AAPL,META"\n', encoding="utf-8")
    rows = b.membership_rows(b.parse_membership(csv_path.read_text()), "2019-01-01")
    former, malformed = b.ticker_report(rows)
    assert former == ["BAD$", "OLDCO"] and malformed == ["BAD$"]
    assert b.main(["--only", "membership", "--start", "2019-01-01", "--membership-csv",
                   str(csv_path)], store=store, fetch=fetcher({}),
                  sector_of=lambda s: None) == 0
    out = capsys.readouterr().out
    assert "2 former member(s) without a rename" in out and "OLDCO" in out
    assert "1 malformed ticker(s): BAD$" in out
    assert "Alpaca" in out


def test_help_documents_the_operator_verification_step(capsys):
    b = _script()
    with pytest.raises(SystemExit) as done:
        b.main(["--help"])
    assert done.value.code == 0
    text = capsys.readouterr().out
    assert "RENAME_MAP" in text and "get_asset" in text and "former member" in text
