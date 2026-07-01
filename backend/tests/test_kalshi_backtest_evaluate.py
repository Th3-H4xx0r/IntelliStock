"""Tests for backtest.evaluate() — Task 6 of the Kalshi backtest replay engine.
Wires the SAME pricing/candidate/sizing path the live bot uses
(intelligence.pricing.build_market_probs -> strategy.candidates.generate_candidates
-> capital.planner.allocate), one fixture at a time, no LLM/live parts."""
from kalshi.backtest import BacktestConfig, SizedBet, evaluate
from kalshi.risk import RiskCaps


def _cfg(**cap_overrides):
    defaults = dict(
        edge_threshold=0.03,
        kelly_fraction=0.25,
        bankroll_cents=100_000,
        min_price_cents=15,
        max_price_cents=90,
        draw_min_edge=0.10,
        no_sharp_edge_threshold=0.10,
        per_bet_cap_frac=0.10,
    )
    defaults.update(cap_overrides)
    caps = RiskCaps(**defaults)
    return BacktestConfig(
        leagues=["EPL"], start_date="", end_date="", bankroll_cents=100_000,
        caps=caps, sharp_weight=0.7, devig_method="power", fee_rate=0.07,
    )


def _fixture():
    return {"fixture_id": "f1", "league": "EPL", "expected_goals": None}


def test_model_and_sharp_agree_home_edge_returns_sized_bet():
    cfg = _cfg()
    model_probs = {"home": 0.55, "draw": 0.25, "away": 0.20}
    sharp_probs = {"home": 0.50, "draw": 0.25, "away": 0.25}
    kalshi_asks = {"home": 45}
    bets = evaluate(cfg, model_probs, sharp_probs, kalshi_asks, _fixture())
    assert len(bets) == 1
    bet = bets[0]
    assert isinstance(bet, SizedBet)
    assert bet.side == "home"
    assert bet.size > 0
    assert bet.entry_cents == 45


def test_thin_edge_is_gated_out():
    cfg = _cfg()
    model_probs = {"home": 0.55, "draw": 0.25, "away": 0.20}
    sharp_probs = {"home": 0.44, "draw": 0.28, "away": 0.28}
    kalshi_asks = {"home": 45}
    bets = evaluate(cfg, model_probs, sharp_probs, kalshi_asks, _fixture())
    assert bets == []


def test_no_sharp_line_honors_no_sharp_edge_threshold():
    cfg = _cfg(no_sharp_edge_threshold=0.20)
    # Model-only side (no sharp price) with a modest edge that clears the
    # plain edge_threshold (0.03) but NOT the no_sharp_edge_threshold (0.20).
    model_probs = {"home": 0.50, "draw": 0.25, "away": 0.25}
    sharp_probs = {}
    kalshi_asks = {"home": 45}
    bets = evaluate(cfg, model_probs, sharp_probs, kalshi_asks, _fixture())
    assert bets == []

    # A big enough model-only edge clears the higher no-sharp bar.
    model_probs_big = {"home": 0.80, "draw": 0.10, "away": 0.10}
    bets_big = evaluate(cfg, model_probs_big, sharp_probs, kalshi_asks, _fixture())
    assert len(bets_big) == 1
    assert bets_big[0].side == "home"
