"""Universes (ST sp500_symbols.py), sectors (ST paper_trader.py:92-115 over a
stored map), and the point-in-time readers the backtest uses (spec §7)."""
import os
import sys
import types

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from swing_trader import refdata, sectors, universe  # noqa: E402


def test_the_vendored_list_is_sts_may_2026_list():
    assert len(universe.SP500_SYMBOLS) == 503
    assert len(set(universe.SP500_SYMBOLS)) == 503
    assert universe.SP500_SYMBOLS[:3] == ["MMM", "AOS", "ABT"]
    assert universe.SP500_SYMBOLS[-3:] == ["ZBRA", "ZBH", "ZTS"]
    assert {"BRK-B", "BF-B"} <= set(universe.SP500_SYMBOLS)


def test_live_universe_appends_the_regime_etfs_and_wheel_drops_class_shares():
    live = universe.get_sp500_symbols()
    assert live[-2:] == ["SPY", "QQQ"] and len(live) == 505
    wheel = universe.get_wheel_universe()
    assert "BRK-B" not in wheel and "BF-B" not in wheel and len(wheel) == 501


def test_order_like_live_uses_todays_order_then_former_members():
    ordered = universe.order_like_live(["ZTS", "FB", "MMM", "brk-b", "SPY", "AOS"])
    assert ordered == ["MMM", "AOS", "BRK.B", "ZTS", "SPY", "FB"]


def test_norm_symbol():
    assert universe.norm_symbol(" brk-b ") == "BRK.B"


def test_overrides_win_then_the_map_then_unknown():
    smap = {"AAPL": "technology", "BRK.B": "financial_services"}
    assert sectors.get_symbol_sector("SPY", smap) == "broad_market"
    assert sectors.get_symbol_sector("AAPL", smap) == "technology"
    assert sectors.get_symbol_sector("BRK-B", smap) == "financial_services"
    assert sectors.get_symbol_sector("ZZZZ", smap) == "unknown"


def test_yfinance_is_only_a_live_fallback(monkeypatch):
    calls = []

    class _T:
        def __init__(self, symbol):
            calls.append(symbol)
            self.info = {"sector": "Consumer Defensive"}

    monkeypatch.setattr(sectors, "yf", types.SimpleNamespace(Ticker=_T))
    cache = {}
    assert sectors.get_symbol_sector("KO", {}, allow_network=False) == "unknown"
    assert calls == []
    assert sectors.get_symbol_sector("KO", {}, allow_network=True, cache=cache) == "consumer_defensive"
    assert sectors.get_symbol_sector("KO", {}, allow_network=True, cache=cache) == "consumer_defensive"
    assert calls == ["KO"]


def test_normalize_sector_is_sts_normalisation():
    assert sectors.normalize_sector("Financial Services") == "financial_services"
    assert sectors.normalize_sector(None) == "unknown"


def _vix(store, rows):
    store.insert(refdata.MACRO_TABLE, [
        {"id": refdata.macro_id("VIX", d), "series": "VIX", "date": d,
         "close": c, "source": "cboe"} for d, c in rows], conflict="replace")


def test_vix_before_skips_a_holiday_and_refuses_a_stale_gap(store):
    _vix(store, [("2026-05-21", 14.0), ("2026-05-22", 15.0), ("2026-05-26", 99.0)])
    # Tuesday after Memorial Day: Friday's close, 4 days old, is the reading.
    assert refdata.vix_before(store, "2026-05-26") == (15.0, None)
    # The row dated the session itself is never visible.
    assert refdata.vix_before(store, "2026-05-27")[0] == 99.0
    # A two-week hole is a data gap, not a weekend: no reading, with a reason.
    value, reason = refdata.vix_before(store, "2026-06-10")
    assert value is None and "2026-05-26" in reason
    assert refdata.vix_before(store, "2026-01-02")[0] is None


def test_vix_before_refuses_a_non_numeric_close(store):
    _vix(store, [("2026-06-01", "nan")])
    value, reason = refdata.vix_before(store, "2026-06-02")
    assert value is None and reason


def test_members_before_is_the_latest_change_strictly_before(store):
    store.insert(refdata.MEMBERSHIP_TABLE, [
        {"id": refdata.membership_id("SPX", "2021-01-04"), "index": "SPX",
         "date": "2021-01-04", "members": ["AAPL", "FB", "BRK.B"]},
        {"id": refdata.membership_id("SPX", "2022-06-09"), "index": "SPX",
         "date": "2022-06-09", "members": ["AAPL", "META", "BRK.B"]},
    ], conflict="replace")
    assert refdata.members_before(store, "2022-06-09") == ["AAPL", "FB", "BRK.B"]
    assert refdata.members_before(store, "2022-06-10") == ["AAPL", "META", "BRK.B"]
    assert refdata.members_before(store, "2020-12-31") is None


def test_sector_map_reads_by_alpaca_symbol(store):
    store.insert(refdata.SECTOR_TABLE, [
        {"id": "AAPL", "symbol": "AAPL", "sector": "technology",
         "as_of": "2026-09-24", "source": "yfinance"},
        {"id": "BRK.B", "symbol": "BRK.B", "sector": "financial_services",
         "as_of": "2026-09-24", "source": "yfinance"}], conflict="replace")
    assert refdata.sector_map(store, ["AAPL", "BRK-B", "NOPE"]) == {
        "AAPL": "technology", "BRK.B": "financial_services"}
    assert refdata.sector_map(store, []) == {}


def test_the_table_names_are_the_contracts():
    assert refdata.SWING_TABLES == ("SwingSignals", "SwingWheelScans",
                                    "SwingIvSnapshots", "SwingMacroDaily",
                                    "SwingIndexMembership", "SwingSectorMap",
                                    "SwingDailyBars")
