"""Tests for backtest.run_backtest() — Task 8 of the Kalshi backtest replay
engine. Orchestrates a FULLY FAKE data provider + fake model_fn over multiple
fixtures: kickoff-order looping, unsettled/unmatched skip reasons, one decision
snapshot per fixture (yes_ask_close_at + devigged sharp odds at/just-before the
snapshot ts), settle, aggregate, progress reporting, and api_calls/cache_hits
surfaced into the result."""
from kalshi.backtest import BacktestConfig, run_backtest
from kalshi.risk import RiskCaps


def _cfg(**overrides):
    caps = RiskCaps(
        edge_threshold=0.03,
        kelly_fraction=0.25,
        bankroll_cents=100_000,
        min_price_cents=15,
        max_price_cents=90,
        draw_min_edge=0.10,
        no_sharp_edge_threshold=0.10,
        per_bet_cap_frac=0.10,
    )
    defaults = dict(
        leagues=["EPL"], start_date="2026-01-01", end_date="2026-01-31",
        bankroll_cents=100_000, caps=caps, sharp_weight=0.7, devig_method="power",
        fee_rate=0.07, decision_offsets_sec=(-100,),
    )
    defaults.update(overrides)
    return BacktestConfig(**defaults)


class FakeDataProvider:
    """No network — canned fixtures/score/candles/odds/tickers, mirroring the
    real BacktestDataProvider's public surface run_backtest depends on."""

    def __init__(self):
        self.api_calls = 3
        self.cache_hits = 5
        self._fixtures = [
            {"fixture_id": "f1", "league": "EPL", "home": "Team A", "away": "Team B",
             "kickoff_ts": 1000},
            {"fixture_id": "f2", "league": "EPL", "home": "Team C", "away": "Team D",
             "kickoff_ts": 500},
            {"fixture_id": "f3", "league": "EPL", "home": "Team E", "away": "Team F",
             "kickoff_ts": 2000},
        ]
        self._scores = {"f1": "home", "f2": None, "f3": "home"}
        self._tickers = {
            "f1": {"home": "TICK-F1-HOME"},
            "f2": {"home": "TICK-F2-HOME"},
            "f3": {},  # unmatched
        }
        self._candles = {
            "TICK-F1-HOME": [{"end_period_ts": 900, "yes_ask": {"close_dollars": 0.40}}],
        }
        self._odds = {
            "f1": [{"ts": 800, "home": 2.0, "draw": 3.4, "away": 4.0}],
            "f2": [],
            "f3": [],
        }

    def fixtures(self, leagues, start_date, end_date):
        return list(self._fixtures)

    def final_score(self, fx):
        return self._scores[fx["fixture_id"]]

    def kalshi_tickers(self, fx):
        return dict(self._tickers[fx["fixture_id"]])

    def candles(self, ticker):
        return list(self._candles.get(ticker, []))

    def sharp_odds(self, fx):
        return list(self._odds.get(fx["fixture_id"], []))


def _fake_model_fn(fx):
    return {"home": 0.55, "draw": 0.25, "away": 0.20}


def test_bettable_fixture_produces_a_winning_bet():
    cfg = _cfg()
    data = FakeDataProvider()
    result = run_backtest(cfg, data, _fake_model_fn)
    assert result.n_bets >= 1
    assert result.pnl_cents > 0
    # the unsettled (f2) and unmatched (f3) fixtures contributed no trades
    assert all(t.fixture_id == "f1" for t in result.trades)


def test_unsettled_fixture_is_skipped():
    cfg = _cfg()
    data = FakeDataProvider()
    result = run_backtest(cfg, data, _fake_model_fn)
    fixture_ids = {t.fixture_id for t in result.trades}
    assert "f2" not in fixture_ids


def test_unmatched_fixture_is_skipped():
    cfg = _cfg()
    data = FakeDataProvider()
    result = run_backtest(cfg, data, _fake_model_fn)
    fixture_ids = {t.fixture_id for t in result.trades}
    assert "f3" not in fixture_ids


def test_progress_cb_reaches_one():
    cfg = _cfg()
    data = FakeDataProvider()
    fracs = []
    run_backtest(cfg, data, _fake_model_fn, progress_cb=fracs.append)
    assert fracs, "progress_cb was never called"
    assert fracs[-1] == 1.0
    assert all(0.0 < f <= 1.0 for f in fracs)


def test_api_calls_and_cache_hits_surfaced_into_result_summary():
    cfg = _cfg()
    data = FakeDataProvider()
    result = run_backtest(cfg, data, _fake_model_fn)
    assert result.summary["api_calls"] == data.api_calls
    assert result.summary["cache_hits"] == data.cache_hits


def test_no_fixtures_returns_empty_result_without_progress_div_by_zero():
    class EmptyData(FakeDataProvider):
        def fixtures(self, leagues, start_date, end_date):
            return []
    cfg = _cfg()
    fracs = []
    result = run_backtest(cfg, EmptyData(), _fake_model_fn, progress_cb=fracs.append)
    assert result.n_bets == 0
    assert result.trades == []
