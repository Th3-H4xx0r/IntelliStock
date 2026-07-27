import os
import sys
from datetime import datetime, timedelta

_backend = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from strategies.graph_nexus_analysis import (  # noqa: E402
    _apply_buy_price_floor,
    _apply_portfolio_drawdown_halt,
    _compute_available_buy_budget,
    _compute_macro_risk_scale,
    _compute_releasable_cash_reserve,
    _estimate_rotation_sell_proceeds,
    _finalize_scores,
    _resolve_symbol_price,
    _rotation_effective_score,
)


class MockPortfolioEmulator:
    def __init__(self, initial_cash: float = 7000.0):
        self._cash = float(initial_cash)
        self._initial_value = float(initial_cash)
        self._positions: dict[str, float] = {}
        self._trades: list[dict] = []
        self._last_prices: dict[str, float] = {}

    def seed_position(self, ticker: str, shares: float, entry_price: float, *, buy_time: datetime):
        self._positions[ticker] = float(shares)
        self._trades.append(
            {
                "ticker": ticker,
                "action": "buy",
                "price": float(entry_price),
                "shares": float(shares),
                "timestamp": buy_time,
            }
        )

    def get_positions(self) -> dict:
        return dict(self._positions)

    def get_trade_history(self) -> list[dict]:
        return list(self._trades)

    def get_cash(self) -> float:
        return self._cash

    def get_portfolio_value(self, prices: dict) -> float:
        value = self._cash
        for ticker, shares in self._positions.items():
            value += shares * float((prices or {}).get(ticker, 0.0) or 0.0)
        return value

    def get_positions_value(self, prices: dict) -> float:
        value = 0.0
        for ticker, shares in self._positions.items():
            value += shares * float((prices or {}).get(ticker, 0.0) or 0.0)
        return value


def test_buy_price_floor_blocks_sub_five_dollar_buys():
    scores = {
        "MTNB": {"score": 1, "reason": "Direct buy", "action_intent": "initial_buy"},
        "AAPL": {"score": 1, "reason": "Direct buy", "action_intent": "initial_buy"},
    }
    updated = _apply_buy_price_floor(
        scores,
        ["MTNB", "AAPL"],
        {"MTNB": 1.22, "AAPL": 180.0},
        None,
        None,
        {"buy_price_floor": 5.0},
    )
    assert updated["MTNB"]["score"] == 0
    assert "PRICE_FLOOR" in updated["MTNB"]["reason"]
    assert updated["AAPL"]["score"] == 1


def test_portfolio_drawdown_halt_triggers_then_resumes_after_three_up_days():
    portfolio = MockPortfolioEmulator(initial_cash=7000.0)
    portfolio._cash = 200.0
    portfolio._positions = {"AAPL": 60.0}
    cache = {}
    config = {
        "portfolio_drawdown_halt_enabled": True,
        "portfolio_drawdown_halt_pct": 10.0,
        "portfolio_drawdown_resume_up_days": 3,
    }

    def _scores():
        return {"NVDA": {"score": 1, "reason": "Buy", "action_intent": "initial_buy"}}

    first = _apply_portfolio_drawdown_halt(
        _scores(),
        ["NVDA"],
        portfolio,
        config,
        cache,
        {"AAPL": 100.0},
        None,
        "2026-02-01",
    )
    assert first["NVDA"]["score"] == 0
    assert cache["_portfolio_drawdown_state"]["halt_active"] is True

    for date_key, price in [("2026-02-02", 102.0), ("2026-02-03", 104.0)]:
        result = _apply_portfolio_drawdown_halt(
            _scores(),
            ["NVDA"],
            portfolio,
            config,
            cache,
            {"AAPL": price},
            None,
            date_key,
        )
        assert result["NVDA"]["score"] == 0
        assert cache["_portfolio_drawdown_state"]["halt_active"] is True

    resumed = _apply_portfolio_drawdown_halt(
        _scores(),
        ["NVDA"],
        portfolio,
        config,
        cache,
        {"AAPL": 106.0},
        None,
        "2026-02-04",
    )
    assert cache["_portfolio_drawdown_state"]["halt_active"] is False
    assert resumed["NVDA"]["score"] == 1


def test_available_buy_budget_uses_floor_and_day_one_ramp():
    portfolio = MockPortfolioEmulator(initial_cash=7000.0)
    strategy_cache = {}
    budget, meta = _compute_available_buy_budget(
        portfolio,
        {},
        None,
        [],
        {},
        {
            "cash_reserve_floor_pct": 0.10,
            "deployment_ramp_enabled": True,
            "deployment_bar1_cap_pct": 0.50,
            "deployment_bar2_cap_pct": 0.70,
            "deployment_bar3_cap_pct": 0.90,
        },
        strategy_cache,
        datetime(2026, 3, 1, 13, 0, 0),
        "2026-03-01",
    )
    assert round(budget, 2) == 3500.0
    assert meta["cash_floor"] == 700.0
    assert meta["ramp_cap_pct"] == 0.5


def test_macro_risk_scale_reduces_budget_when_bearish_events_dominate():
    scale, meta = _compute_macro_risk_scale(
        [
            {"impact_direction": "bearish", "confidence": 0.9},
            {"impact_direction": "bearish", "confidence": 0.8},
        ],
        [
            {"impact_direction": "bearish", "confidence": 0.9},
            {"impact_direction": "bullish", "confidence": 0.6},
        ],
        {
            "macro_risk_scaling_enabled": True,
            "macro_risk_scale_step": 0.10,
            "macro_risk_scale_min": 0.60,
        },
    )
    assert scale < 1.0
    assert meta["bearish_score"] > meta["bullish_score"]


def test_releasable_cash_reserve_unlocks_for_high_conviction_after_min_positions():
    portfolio = MockPortfolioEmulator(initial_cash=7000.0)
    portfolio._cash = 700.0
    portfolio._positions = {
        "AAPL": 5.0,
        "MSFT": 5.0,
        "NVDA": 5.0,
        "AMZN": 5.0,
        "META": 5.0,
    }
    release = _compute_releasable_cash_reserve(
        {
            "cash_after_sells": 700.0,
            "cash_after_floor": 0.0,
            "cash_floor": 700.0,
            "ramp_room": 9999.0,
        },
        ["SNDK"],
        {"SNDK": {"raw_net_score": 0.85}},
        portfolio,
        {
            "cash_reserve_release_after_min_positions": True,
            "cash_reserve_hard_min_positions": 5,
            "cash_reserve_release_min_score": 0.50,
            "cash_reserve_release_cap_pct": 1.0,
        },
    )
    assert release == 700.0


def test_resolve_symbol_price_falls_back_to_history_and_last_price():
    portfolio = MockPortfolioEmulator(initial_cash=1000.0)
    portfolio._last_prices["MSFT"] = 410.0
    assert _resolve_symbol_price("MU", {}, {"MU": [{"c": 426.12}]}, portfolio_emulator=portfolio) == 426.12
    assert _resolve_symbol_price("MSFT", {}, None, portfolio_emulator=portfolio) == 410.0


def test_rotation_effective_score_uses_ml_confidence():
    buy_doc = {
        "raw_net_score": 0.70,
        "ml": {
            "ml_confidence": 0.9,
            "ml_up_probability": 0.85,
            "ml_down_probability": 0.15,
        },
    }
    held_doc = {
        "raw_net_score": 0.75,
        "ml": {
            "ml_confidence": 0.1,
            "ml_up_probability": 0.45,
            "ml_down_probability": 0.55,
        },
    }
    config = {"rotation_ml_weight": 0.20}
    assert _rotation_effective_score(buy_doc, config) > _rotation_effective_score(held_doc, config)


def test_rotation_sell_proceeds_estimate_funds_replacement_buys():
    portfolio = MockPortfolioEmulator(initial_cash=500.0)
    buy_time = datetime(2026, 1, 1, 13, 0, 0)
    portfolio.seed_position("LOSER", 10.0, 100.0, buy_time=buy_time)
    proceeds = _estimate_rotation_sell_proceeds(
        [{"sell": "LOSER", "buy": "MU"}],
        {},
        {"LOSER": 120.0},
        portfolio,
    )
    assert proceeds == 1200.0


def test_profit_take_only_fires_once_per_open_position():
    portfolio = MockPortfolioEmulator(initial_cash=7000.0)
    buy_time = datetime(2026, 1, 1, 13, 0, 0)
    portfolio.seed_position("WIN", 10.0, 100.0, buy_time=buy_time)
    cache = {}
    config = {
        "profit_take_enabled": True,
        "profit_take_gain_pct": 40.0,
        "profit_take_sell_fraction": 0.50,
        "trailing_stop_activation_pct": 10.0,
        "trailing_stop_pct": 8.0,
        "fast_loser_cut_pct": -10.0,
        "max_hold_days": 45,
    }

    first = _finalize_scores(
        ["WIN"],
        {},
        {},
        config,
        portfolio_emulator=portfolio,
        date_key="2026-01-10",
        prices={"WIN": 145.0},
        strategy_cache=cache,
    )
    assert first["WIN"]["score"] == -1
    assert first["WIN"]["sell_fraction"] == 0.5
    assert "Profit take" in first["WIN"]["reason"]

    second = _finalize_scores(
        ["WIN"],
        {},
        {},
        config,
        portfolio_emulator=portfolio,
        date_key="2026-01-11",
        prices={"WIN": 146.0},
        strategy_cache=cache,
    )
    assert second["WIN"]["score"] == 0
