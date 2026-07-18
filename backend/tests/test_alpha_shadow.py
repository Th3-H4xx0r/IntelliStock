"""Task 9A: quote-driven virtual shadow portfolio — zero broker capability."""
import os
import sys
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from benchmark_alpha.costs import CostModelConfig, EquityCostModel
from benchmark_alpha.shadow import ShadowPortfolio

NOW = datetime(2026, 7, 11, 15, 0, tzinfo=timezone.utc)
MODEL = EquityCostModel(CostModelConfig(
    version="cm-1", impact_coeff_bps=5.0, regulatory_fee_bps=0.3,
    fixed_monthly_cost=0.0, missed_trade_bps=5.0))


def _quotes(**overrides):
    base = {"AAPL": {"bid": 99.98, "ask": 100.02},
            "SPY": {"bid": 499.95, "ask": 500.05}}
    base.update(overrides)
    return base


def test_shadow_fills_buys_at_ask_and_reconciles_equity():
    shadow = ShadowPortfolio(cash=10_000.0)
    cycle = shadow.apply({"AAPL": 0.08, "SPY": 0.90}, _quotes(), MODEL, NOW)
    assert cycle.fills
    aapl = next(f for f in cycle.fills if f["symbol"] == "AAPL")
    assert aapl["price"] == pytest.approx(100.02)  # buys pay the ask
    assert cycle.equity == pytest.approx(
        shadow.cash + sum(qty * (_quotes()[s]["bid"] + _quotes()[s]["ask"]) / 2
                          for s, qty in shadow.positions.items()), rel=1e-9)
    assert shadow.cash > 0  # ~2% operational cash target remains


def test_shadow_rejects_symbols_without_quotes_instead_of_inventing_fills():
    shadow = ShadowPortfolio(cash=10_000.0)
    cycle = shadow.apply({"AAPL": 0.08, "MYST": 0.08, "SPY": 0.82},
                         _quotes(), MODEL, NOW)
    rejected = [rej for rej in cycle.rejected if rej["symbol"] == "MYST"]
    assert rejected and rejected[0]["reason"] == "no_quote"
    assert "MYST" not in shadow.positions


def test_shadow_sells_at_bid_and_costs_reduce_equity():
    shadow = ShadowPortfolio(cash=10_000.0)
    shadow.apply({"AAPL": 0.10, "SPY": 0.88}, _quotes(), MODEL, NOW)
    equity_before = shadow.equity(_quotes())
    cycle = shadow.apply({"SPY": 0.98}, _quotes(), MODEL, NOW)
    sell = next(f for f in cycle.fills if f["symbol"] == "AAPL"
                and f["side"] == "sell")
    assert sell["price"] == pytest.approx(99.98)  # sells hit the bid
    assert shadow.equity(_quotes()) < equity_before  # spread+costs are real


def test_shadow_has_no_broker_submission_surface():
    shadow = ShadowPortfolio(cash=1_000.0)
    for forbidden in ("submit_order", "buy", "sell", "execute_signal",
                      "broker", "adapter", "wal"):
        assert not hasattr(shadow, forbidden)


def test_shadow_cycle_ledger_reconciles_cash_deltas():
    shadow = ShadowPortfolio(cash=10_000.0)
    cycle = shadow.apply({"AAPL": 0.08, "SPY": 0.90}, _quotes(), MODEL, NOW)
    spent = sum(f["qty"] * f["price"] for f in cycle.fills if f["side"] == "buy")
    costs = sum(f["cost"] for f in cycle.fills)
    assert shadow.cash == pytest.approx(10_000.0 - spent - costs)
