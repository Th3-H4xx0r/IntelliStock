"""LLM analyst plumbing: llm_adjustments shift the fused prob (evaluate) and
run_backtest calls the injected analyst_fn."""
import sys, types
sys.modules.setdefault("socketio", types.ModuleType("socketio"))

from kalshi.backtest import config_from_body, evaluate, run_backtest


def _cfg():
    return config_from_body({
        "leagues": ["World Cup"], "start_date": "2026-06-29", "end_date": "2026-07-01",
        "bankroll_cents": 10000, "edge_threshold": 0.02, "no_sharp_edge_threshold": 0.0,
        "order_size_min_cents": 500, "order_size_max_cents": 1000, "kelly_fraction": 0.2,
        "min_price_cents": 1, "max_price_cents": 99,
    })


def test_positive_llm_adjustment_raises_home_fused_fair_and_still_bets():
    cfg = _cfg()
    fx = {"fixture_id": "F", "league": "World Cup", "market_tickers": {"home": "F-H"}}
    mp = {"home": 0.50, "draw": 0.30, "away": 0.20}
    asks = {"home": 40}
    b0 = evaluate(cfg, mp, {}, asks, fx, {})
    b1 = evaluate(cfg, mp, {}, asks, fx, {"home": 0.10})   # clamped to llm_cap 0.05
    assert b0 and b1
    assert b1[0].fused_fair > b0[0].fused_fair


class _Provider:
    """Minimal fake: one settled, bettable fixture."""
    def __init__(self):
        self.api_calls = 0
        self.cache_hits = 0
    def fixtures(self, leagues, s, e):
        return [{"fixture_id": "F", "league": "World Cup", "kickoff_ts": 20_000}]
    def final_score(self, fx):
        return "home"
    def kalshi_tickers(self, fx):
        return {"home": "F-H", "draw": "F-T", "away": "F-A"}
    def candles(self, ticker, start_ts=0, end_ts=4102444800):
        # a single hourly candle before the decision snapshot (kickoff-3h)
        asks = {"F-H": "0.40", "F-T": "0.30", "F-A": "0.30"}
        return [{"end_period_ts": 100, "yes_ask": {"close_dollars": asks.get(ticker, "0.50")}}]
    def sharp_odds(self, fx):
        return []


def test_run_backtest_invokes_analyst_fn():
    cfg = _cfg()
    calls = []

    def analyst_fn(fx, model_probs, sharp_probs):
        calls.append((fx.get("fixture_id"), dict(model_probs)))
        return {"home": 0.05}

    def model_fn(fx):
        return {"home": 0.50, "draw": 0.30, "away": 0.20}

    res = run_backtest(cfg, _Provider(), model_fn, analyst_fn=analyst_fn)
    assert calls and calls[0][0] == "F"
    assert res.n_bets >= 1
