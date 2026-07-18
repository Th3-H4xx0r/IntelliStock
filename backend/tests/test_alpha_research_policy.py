"""Task 9A: non-trading research portfolio policy (Stage B gate input)."""
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from benchmark_alpha.records import Forecast
from benchmark_alpha.research_policy import ResearchPortfolioPolicy, ResearchTaxState
from benchmark_alpha.types import EvidenceClass, RunOrigin

AS_OF = datetime(2026, 7, 11, 14, 30, tzinfo=timezone.utc)


def _forecast(symbol, expected=0.02, evidence=EvidenceClass.DIRECT,
              eligible=True, as_of=AS_OF):
    return Forecast(
        producer="graph_nexus", model_version="gn-1", evidence_class=evidence,
        symbol=symbol, as_of=as_of, horizon_trading_days=3,
        expected_excess_return=expected, probability_outperform=0.6,
        confidence=0.5, feature_version="f1", data_snapshot_id="snap",
        eligibility=eligible, gate_reasons=(), revision=0,
        instance_id="alpaca-main", origin=RunOrigin.BACKTEST, run_id="run-1")


def _build(forecasts, active_cap, **kw):
    policy = ResearchPortfolioPolicy()
    return policy.build(forecasts, active_cap=active_cap, as_of=AS_OF, **kw)


def test_no_signals_produces_98_spy_2_cash():
    target = _build([], active_cap=0.40)
    assert target.weights["SPY"] == pytest.approx(0.98)
    assert target.cash_weight == pytest.approx(0.02)


def test_five_strong_signals_at_live40_produce_40_58_2():
    forecasts = [_forecast(s) for s in ("AAA", "BBB", "CCC", "DDD", "EEE")]
    target = _build(forecasts, active_cap=0.40)
    active = sum(w for s, w in target.weights.items() if s != "SPY")
    assert active == pytest.approx(0.40)
    assert target.weights["SPY"] == pytest.approx(0.58)
    assert all(w <= 0.08 + 1e-9 for s, w in target.weights.items() if s != "SPY")


def test_ten_strong_signals_reach_80_only_with_live80_cap():
    forecasts = [_forecast(f"S{i:02d}") for i in range(12)]
    live40 = _build(forecasts, active_cap=0.40)
    assert sum(w for s, w in live40.weights.items() if s != "SPY") == pytest.approx(0.40)
    live80 = _build(forecasts, active_cap=0.80)
    active = sum(w for s, w in live80.weights.items() if s != "SPY")
    assert active == pytest.approx(0.80)
    assert len([s for s in live80.weights if s != "SPY"]) == 10  # max names


def test_sector_cap_limits_concentration():
    forecasts = [_forecast(s) for s in ("T1", "T2", "T3", "T4")]
    sectors = {s: "tech" for s in ("T1", "T2", "T3", "T4")}
    target = _build(forecasts, active_cap=0.40, sectors=sectors)
    tech_weight = sum(w for s, w in target.weights.items()
                      if sectors.get(s) == "tech")
    assert tech_weight <= 0.20 + 1e-9


def test_propagation_and_stale_forecasts_are_not_tradable():
    stale = _forecast("OLD", as_of=AS_OF - timedelta(days=1))
    prop = _forecast("PRP", evidence=EvidenceClass.PROPAGATION)
    rejected = _forecast("REJ", eligible=False)
    target = _build([stale, prop, rejected, _forecast("GOOD")], active_cap=0.40)
    active_syms = [s for s in target.weights if s != "SPY"]
    assert active_syms == ["GOOD"]
    reasons = {r["symbol"]: r["reason"] for r in target.rejections}
    assert "not_current_cycle" in reasons["OLD"]
    assert "evidence_class" in reasons["PRP"]
    assert "ineligible" in reasons["REJ"]


def test_wash_sale_proxy_blocks_repurchase_and_records_opportunity_cost():
    tax = ResearchTaxState(loss_sales={"AAPL": AS_OF - timedelta(days=10)})
    target = _build([_forecast("AAPL"), _forecast("MSFT")], active_cap=0.40,
                    tax_state=tax)
    assert "AAPL" not in [s for s in target.weights if s != "SPY"]
    blocked = [r for r in target.rejections if r["symbol"] == "AAPL"]
    assert blocked and "wash_sale" in blocked[0]["reason"]
    assert target.tax_opportunity_cost_bps >= 0
    old = ResearchTaxState(loss_sales={"AAPL": AS_OF - timedelta(days=32)})
    ok = _build([_forecast("AAPL")], active_cap=0.40, tax_state=old)
    assert "AAPL" in ok.weights


def test_turnover_cap_scales_same_session_trading():
    current = {"OLD1": 0.08, "OLD2": 0.08, "SPY": 0.82}
    forecasts = [_forecast(f"N{i}") for i in range(5)]
    target = _build(forecasts, active_cap=0.40, current_weights=current,
                    turnover_cap=0.10)
    assert target.turnover <= 0.10 + 1e-9
    assert target.turnover_capped is True


def test_policy_cannot_create_a_broker_intent():
    policy = ResearchPortfolioPolicy()
    for forbidden in ("submit_order", "execute", "broker", "wal",
                      "order_intent", "runtime"):
        assert not hasattr(policy, forbidden)
