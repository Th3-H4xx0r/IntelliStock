"""Task 9A: versioned execution-cost model and implementation shortfall."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from benchmark_alpha.costs import (
    CostModelConfig,
    EquityCostModel,
    ImplementationShortfall,
)

QUOTE = {"bid": 99.98, "ask": 100.02, "bid_size": 500, "ask_size": 500}


def _model(**kw):
    cfg = dict(version="cm-1", impact_coeff_bps=10.0, regulatory_fee_bps=0.3,
               fixed_monthly_cost=25.0, missed_trade_bps=5.0)
    cfg.update(kw)
    return EquityCostModel(CostModelConfig(**cfg))


def test_cost_components_are_separately_reported():
    est = _model().estimate(
        side="buy", notional=1000.0, decision_quote=QUOTE,
        submit_quote={"bid": 100.00, "ask": 100.04}, adv_notional=1_000_000.0)
    assert est.half_spread_cost > 0
    assert est.latency_drift_cost > 0  # price moved against the buy
    assert est.impact_cost > 0
    assert est.regulatory_fees == 0.0  # buys carry no SEC/TAF fee
    assert est.total_cost == pytest.approx(
        est.half_spread_cost + est.latency_drift_cost + est.impact_cost
        + est.regulatory_fees)
    sell = _model().estimate(side="sell", notional=1000.0,
                             decision_quote=QUOTE, submit_quote=QUOTE,
                             adv_notional=1_000_000.0)
    assert sell.regulatory_fees > 0


def test_missing_nbbo_uses_conservative_floor_never_zero():
    est = _model().estimate(side="buy", notional=1000.0, decision_quote=None,
                            submit_quote=None, adv_notional=None)
    assert est.half_spread_cost > 0
    assert est.total_cost > 0
    assert est.degraded_inputs is True


def test_zero_alpha_high_turnover_strategy_loses_after_costs():
    model = _model()
    cash = 10_000.0
    pnl = 0.0
    for _ in range(50):  # 50 zero-alpha round trips
        buy = model.estimate(side="buy", notional=2000.0, decision_quote=QUOTE,
                             submit_quote=QUOTE, adv_notional=500_000.0)
        sell = model.estimate(side="sell", notional=2000.0,
                              decision_quote=QUOTE, submit_quote=QUOTE,
                              adv_notional=500_000.0)
        pnl -= buy.total_cost + sell.total_cost
    pnl -= model.config.fixed_monthly_cost
    assert pnl < 0
    assert abs(pnl) / cash > 0.005  # costs are material at small capital


def test_fixed_cost_reported_separately_and_amortized():
    model = _model(fixed_monthly_cost=80.73)
    assert model.config.fixed_monthly_cost == 80.73
    assert model.amortized_fixed_bps(nav=6000.0) == pytest.approx(
        80.73 / 6000.0 * 10_000)


def test_cost_model_version_is_immutable():
    model = _model()
    with pytest.raises(Exception):
        model.config.version = "hacked"


def test_shortfall_labels_reference_proxy_vs_measured():
    proxy = ImplementationShortfall().observe(
        side="buy", decision_reference_price=100.0,
        fills=[{"qty": 10, "price": 100.32}], nbbo_at_decision=None)
    assert proxy.basis == "reference_proxy"
    assert proxy.shortfall_bps == pytest.approx(32.1, abs=0.5)
    measured = ImplementationShortfall().observe(
        side="buy", decision_reference_price=100.0,
        fills=[{"qty": 10, "price": 100.05}],
        nbbo_at_decision={"bid": 99.99, "ask": 100.01})
    assert measured.basis == "measured_nbbo"
    # Measured shortfall is vs the tradable ask, not the stale reference.
    assert measured.shortfall_bps == pytest.approx(
        (100.05 - 100.01) / 100.01 * 10_000, abs=0.01)


def test_july18_diagnostic_fixture_is_not_promotion_evidence():
    record = ImplementationShortfall().observe(
        side="buy", decision_reference_price=100.0,
        fills=[{"qty": 28, "price": 100.321}], nbbo_at_decision=None,
        decision_to_fill_seconds=390.0)
    assert record.basis == "reference_proxy"
    assert record.decision_to_fill_seconds == 390.0
    assert record.promotion_eligible is False


def test_crossed_quote_never_produces_a_cost_rebate():
    """Audit: bid>ask made half-spread negative — a garbled NBBO became a
    rebate with degraded_inputs=False."""
    est = _model().estimate(side="buy", notional=10_000.0,
                            decision_quote={"bid": 100.2, "ask": 100.0},
                            submit_quote={"bid": 100.2, "ask": 100.0},
                            adv_notional=1_000_000.0)
    assert est.half_spread_cost > 0
    assert est.total_cost > 0
    assert est.degraded_inputs is True


def test_malformed_submit_quote_flags_degraded():
    est = _model().estimate(side="buy", notional=1000.0,
                            decision_quote=QUOTE,
                            submit_quote={"bid": 0, "ask": 0},
                            adv_notional=1_000_000.0)
    assert est.degraded_inputs is True


def test_shortfall_guards_zero_reference_and_malformed_fills():
    with pytest.raises(ValueError):
        ImplementationShortfall().observe(
            side="buy", decision_reference_price=0.0,
            fills=[{"qty": 1, "price": 10.0}], nbbo_at_decision=None)
    with pytest.raises(ValueError):
        ImplementationShortfall().observe(
            side="buy", decision_reference_price=100.0,
            fills=[{"qty": 1, "price": 10.0}, {"price": 11.0}],
            nbbo_at_decision=None)
