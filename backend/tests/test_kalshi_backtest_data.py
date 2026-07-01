"""Tests for BacktestDataProvider — fetch-or-cache data layer for the Kalshi
soccer backtest. All clients + the DB table are FAKE/in-memory: no network."""
import os
import sys

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from kalshi.backtest_data import BacktestDataProvider


class FakeKalshiClient:
    def __init__(self, candles=None, settled_markets=None):
        self.candles = candles or []
        self.settled_markets = settled_markets or []
        self.candle_calls = 0
        self.settled_calls = 0
        self.market_lists = {}  # (series, status) -> {"markets": [...]}

    def get_candlesticks(self, ticker, start_ts, end_ts, period_interval=60):
        self.candle_calls += 1
        return self.candles

    def list_settled_markets(self, series, limit=1000):
        self.settled_calls += 1
        return self.settled_markets

    def list_markets(self, *, status="open", series_ticker=None, limit=200, cursor=None):
        return self.market_lists.get((series_ticker, status), {"markets": []})


class FakeOddsPapiClient:
    def __init__(self, hist_odds=None, fixture_rows=None):
        self.hist_odds = hist_odds if hist_odds is not None else []
        self.hist_calls = 0
        self.fixture_rows = fixture_rows if fixture_rows is not None else []
        self.list_fixtures_calls = 0

    def historical_odds(self, fixture_id, books=("pinnacle",)):
        self.hist_calls += 1
        return self.hist_odds

    def list_fixtures(self, sport_id, date_from, date_to):
        self.list_fixtures_calls += 1
        return self.fixture_rows


# --- fixtures(): listing is cached per (leagues, start_date, end_date) query ---

_SETTLED_ENGFRA = [
    {"ticker": "KXWCGAME-26JUN15ENGFRA-ENG", "result": "yes", "close_time": "2026-06-15T20:00:00Z"},
    {"ticker": "KXWCGAME-26JUN15ENGFRA-FRA", "result": "no", "close_time": "2026-06-15T20:00:00Z"},
    {"ticker": "KXWCGAME-26JUN15ENGFRA-TIE", "result": "no", "close_time": "2026-06-15T20:00:00Z"},
]


def test_fixtures_from_kalshi_settled_markets_once_and_stores():
    kc = FakeKalshiClient(settled_markets=list(_SETTLED_ENGFRA))
    provider = BacktestDataProvider(kalshi_client=kc, tables={})
    rows = provider.fixtures(["world cup"], "2026-06-01", "2026-06-30")
    assert len(rows) == 1
    assert rows[0]["fixture_id"] == "KXWCGAME-26JUN15ENGFRA"
    assert rows[0]["result"] == "home"                       # ENG (home) resolved yes
    assert set(rows[0]["market_tickers"]) == {"home", "away", "draw"}
    assert rows[0]["home_flag"]                              # flag URL derived, not hardcoded
    assert kc.settled_calls == 1
    assert provider.api_calls == 1
    assert provider.cache_hits == 0


def test_fixtures_second_identical_call_is_a_cache_hit_no_client_call():
    kc = FakeKalshiClient(settled_markets=list(_SETTLED_ENGFRA))
    provider = BacktestDataProvider(kalshi_client=kc, tables={})
    rows1 = provider.fixtures(["world cup"], "2026-06-01", "2026-06-30")
    rows2 = provider.fixtures(["world cup"], "2026-06-01", "2026-06-30")
    assert rows1 == rows2
    assert kc.settled_calls == 1       # not called again
    assert provider.api_calls == 1
    assert provider.cache_hits == 1


def test_fixtures_no_kalshi_client_returns_empty():
    provider = BacktestDataProvider(kalshi_client=None, tables={})
    assert provider.fixtures(["world cup"], "2026-06-01", "2026-06-30") == []


def test_fixtures_filters_out_of_range_events():
    kc = FakeKalshiClient(settled_markets=list(_SETTLED_ENGFRA))
    provider = BacktestDataProvider(kalshi_client=kc, tables={})
    rows = provider.fixtures(["world cup"], "2026-07-01", "2026-07-31")  # June event excluded
    assert rows == []


# --- candles(): generic cache-miss / cache-hit pattern ---

def test_candles_cache_miss_calls_client_once_and_stores():
    kc = FakeKalshiClient(candles=[{"end_period_ts": 1}])
    provider = BacktestDataProvider(kalshi_client=kc, tables={})
    rows = provider.candles("KXWCGAME-26JUL01ENGCOD-ENG")
    assert rows == [{"end_period_ts": 1}]
    assert kc.candle_calls == 1
    assert provider.api_calls == 1
    assert provider.cache_hits == 0


def test_candles_second_call_is_a_cache_hit_no_client_call():
    kc = FakeKalshiClient(candles=[{"end_period_ts": 1}])
    provider = BacktestDataProvider(kalshi_client=kc, tables={})
    provider.candles("TICK")
    rows = provider.candles("TICK")
    assert rows == [{"end_period_ts": 1}]
    assert kc.candle_calls == 1          # not called again
    assert provider.api_calls == 1
    assert provider.cache_hits == 1


# --- sharp_odds(): generic cache pattern + budget guard ---

_OFX = [{"fixture_id": "oid1", "home": "England", "away": "DR Congo",
         "has_odds": True, "kickoff_ts": 1780000000}]
_FX = {"fixture_id": "fx1", "home": "England", "away": "DR Congo", "kickoff_ts": 1780000000}


def test_sharp_odds_matches_oddspapi_fixture_and_caches():
    op = FakeOddsPapiClient(hist_odds=[{"ts": 1, "home": 2.0, "draw": 3.5, "away": 4.0}],
                            fixture_rows=[dict(_OFX[0])])
    provider = BacktestDataProvider(oddspapi_client=op, tables={}, budget_used_getter=lambda: 0)
    rows1 = provider.sharp_odds(dict(_FX))
    rows2 = provider.sharp_odds(dict(_FX))
    assert rows1 == rows2 == [{"ts": 1, "home": 2.0, "draw": 3.5, "away": 4.0}]
    assert op.hist_calls == 1      # fetched once (by OddsPapi id), then cached


def test_sharp_odds_no_odds_flag_is_model_only():
    op = FakeOddsPapiClient(hist_odds=[{"ts": 1, "home": 2.0, "draw": 3.5, "away": 4.0}],
                            fixture_rows=[{**_OFX[0], "has_odds": False}])
    provider = BacktestDataProvider(oddspapi_client=op, tables={}, budget_used_getter=lambda: 0)
    assert provider.sharp_odds(dict(_FX)) == []
    assert op.hist_calls == 0       # never fetch odds when hasOdds is false


def test_sharp_odds_budget_exhausted_no_client_calls():
    op = FakeOddsPapiClient(hist_odds=[{"ts": 1, "home": 2.0, "draw": 3.5, "away": 4.0}],
                            fixture_rows=[dict(_OFX[0])])
    provider = BacktestDataProvider(oddspapi_client=op, tables={}, budget_used_getter=lambda: 250)
    assert provider.sharp_odds(dict(_FX)) == []
    assert op.hist_calls == 0
    assert provider.api_calls == 0


def test_sharp_odds_budget_exhausted_returns_cached_value_if_present():
    op = FakeOddsPapiClient(hist_odds=[{"ts": 9, "home": 1.1, "draw": 9.0, "away": 20.0}])
    tables = {"KalshiHistOdds": {"fx1": {"id": "fx1", "snapshots": [{"ts": 5, "home": 2.0, "draw": 3.0, "away": 3.0}]}}}
    provider = BacktestDataProvider(oddspapi_client=op, tables=tables,
                                     budget_used_getter=lambda: 250)
    rows = provider.sharp_odds({"fixture_id": "fx1"})
    assert rows == [{"ts": 5, "home": 2.0, "draw": 3.0, "away": 3.0}]
    assert op.hist_calls == 0


def test_sharp_odds_budget_bumper_called_on_real_fetch():
    op = FakeOddsPapiClient(hist_odds=[{"ts": 1, "home": 2.0, "draw": 3.5, "away": 4.0}],
                            fixture_rows=[dict(_OFX[0])])
    bumps = []
    provider = BacktestDataProvider(oddspapi_client=op, tables={},
                                     budget_used_getter=lambda: 0,
                                     budget_bumper=lambda: bumps.append(1))
    provider.sharp_odds(dict(_FX))
    assert len(bumps) >= 1   # bumped for the fixture-list match and the odds fetch


# --- final_score(): Kalshi-primary, settled-tracking cache ---

def test_final_score_primary_source_is_kalshi_settled_markets():
    kc = FakeKalshiClient(settled_markets=[
        {"ticker": "KXWCGAME-26JUL01ENGCOD-ENG", "result": "yes"},
        {"ticker": "KXWCGAME-26JUL01ENGCOD-COD", "result": "no"},
        {"ticker": "KXWCGAME-26JUL01ENGCOD-TIE", "result": "no"},
    ])
    kc.market_lists[("KXWCGAME", "open")] = {"markets": [
        {"ticker": "KXWCGAME-26JUL01ENGCOD-ENG"},
        {"ticker": "KXWCGAME-26JUL01ENGCOD-COD"},
        {"ticker": "KXWCGAME-26JUL01ENGCOD-TIE"},
    ]}
    provider = BacktestDataProvider(kalshi_client=kc, tables={})
    # Fixture without a pre-resolved result -> resolved from Kalshi settled markets.
    fx = {"fixture_id": "KXWCGAME-26JUL01ENGCOD", "home": "England", "away": "DR Congo",
          "market_tickers": {"home": "KXWCGAME-26JUL01ENGCOD-ENG",
                             "away": "KXWCGAME-26JUL01ENGCOD-COD",
                             "draw": "KXWCGAME-26JUL01ENGCOD-TIE"}}
    assert provider.final_score(fx) == "home"
    assert kc.settled_calls == 1


def test_final_score_unsettled_cached_row_is_refetched_until_settled():
    kc = FakeKalshiClient(settled_markets=[])   # not settled yet on Kalshi
    kc.market_lists[("KXWCGAME", "open")] = {"markets": [
        {"ticker": "KXWCGAME-26JUL01ENGCOD-ENG"},
        {"ticker": "KXWCGAME-26JUL01ENGCOD-COD"},
        {"ticker": "KXWCGAME-26JUL01ENGCOD-TIE"},
    ]}
    provider = BacktestDataProvider(kalshi_client=kc, tables={})
    fx = {"fixture_id": "fx1", "home": "England", "away": "DR Congo", "settled": False, "result": None}

    assert provider.final_score(fx) is None
    assert kc.settled_calls == 1          # first fetch, still unsettled

    assert provider.final_score(fx) is None
    assert kc.settled_calls == 2          # unsettled cache -> re-fetched

    # Now Kalshi settles the market.
    kc.settled_markets = [
        {"ticker": "KXWCGAME-26JUL01ENGCOD-ENG", "result": "no"},
        {"ticker": "KXWCGAME-26JUL01ENGCOD-COD", "result": "yes"},
        {"ticker": "KXWCGAME-26JUL01ENGCOD-TIE", "result": "no"},
    ]
    assert provider.final_score(fx) == "away"
    assert kc.settled_calls == 3          # settled now -> fetched one more time

    assert provider.final_score(fx) == "away"
    assert kc.settled_calls == 3          # cached settled -> NEVER re-fetches again


def test_final_score_falls_back_to_oddspapi_when_kalshi_unavailable():
    provider = BacktestDataProvider(kalshi_client=None, tables={})
    fx = {"fixture_id": "fx2", "home": "England", "away": "DR Congo",
          "settled": True, "result": "draw"}
    assert provider.final_score(fx) == "draw"


def test_final_score_none_when_neither_source_has_it():
    provider = BacktestDataProvider(kalshi_client=None, tables={})
    fx = {"fixture_id": "fx3", "home": "England", "away": "DR Congo",
          "settled": False, "result": None}
    assert provider.final_score(fx) is None
