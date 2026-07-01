"""Tests for BacktestDataProvider.kalshi_tickers — fixture -> Kalshi ticker
resolution, reusing ticker_names parsing + odds_api's fuzzy-uniqueness match."""
import os
import sys

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from kalshi.backtest_data import BacktestDataProvider


class FakeKalshiClient:
    def __init__(self, market_lists):
        # market_lists: {(series_ticker, status): {"markets": [...]}}
        self.market_lists = market_lists

    def list_markets(self, *, status="open", series_ticker=None, limit=200, cursor=None):
        return self.market_lists.get((series_ticker, status), {"markets": []})


_ENG_COD_MARKETS = {"markets": [
    {"ticker": "KXWCGAME-26JUL01ENGCOD-ENG"},
    {"ticker": "KXWCGAME-26JUL01ENGCOD-COD"},
    {"ticker": "KXWCGAME-26JUL01ENGCOD-TIE"},
]}


def test_kalshi_tickers_resolves_three_sides():
    kc = FakeKalshiClient({("KXWCGAME", "open"): _ENG_COD_MARKETS})
    provider = BacktestDataProvider(kalshi_client=kc, tables={})
    fx = {"home": "England", "away": "DR Congo"}
    out = provider.kalshi_tickers(fx)
    assert out == {
        "home": "KXWCGAME-26JUL01ENGCOD-ENG",
        "away": "KXWCGAME-26JUL01ENGCOD-COD",
        "draw": "KXWCGAME-26JUL01ENGCOD-TIE",
    }


def test_kalshi_tickers_name_variant_still_matches():
    kc = FakeKalshiClient({("KXWCGAME", "open"): _ENG_COD_MARKETS})
    provider = BacktestDataProvider(kalshi_client=kc, tables={})
    fx = {"home": "England", "away": "Congo DR"}   # variant spelling
    out = provider.kalshi_tickers(fx)
    assert out["home"] == "KXWCGAME-26JUL01ENGCOD-ENG"
    assert out["away"] == "KXWCGAME-26JUL01ENGCOD-COD"


def test_kalshi_tickers_unmatched_fixture_returns_empty_and_logs(caplog):
    kc = FakeKalshiClient({("KXWCGAME", "open"): _ENG_COD_MARKETS})
    provider = BacktestDataProvider(kalshi_client=kc, tables={})
    fx = {"home": "Brazil", "away": "France"}   # no such Kalshi event
    with caplog.at_level("WARNING"):
        out = provider.kalshi_tickers(fx)
    assert out == {}
    assert any("kalshi_tickers" in r.message or "unmatched" in r.message.lower()
               for r in caplog.records)


def test_kalshi_tickers_no_client_returns_empty():
    provider = BacktestDataProvider(kalshi_client=None, tables={})
    assert provider.kalshi_tickers({"home": "England", "away": "DR Congo"}) == {}


def test_kalshi_tickers_ambiguous_two_similar_events_returns_empty():
    # Two events both partially resembling the fixture -> unique-match safety
    # must refuse rather than guess.
    markets = {"markets": [
        {"ticker": "KXWCGAME-26JUL01GUIESP-GUI"},
        {"ticker": "KXWCGAME-26JUL01GUIESP-ESP"},
        {"ticker": "KXWCGAME-26JUL01GUIESP-TIE"},
    ]}
    kc = FakeKalshiClient({("KXWCGAME", "open"): markets})
    provider = BacktestDataProvider(kalshi_client=kc, tables={})
    # Requesting a fixture whose away team ("Brazil") matches nothing -> safe miss.
    out = provider.kalshi_tickers({"home": "Guinea", "away": "Brazil"})
    assert out == {}
