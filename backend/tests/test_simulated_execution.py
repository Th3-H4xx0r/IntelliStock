from datetime import datetime, timedelta, timezone
import math

import pytest

from backend.simulated_execution import (
    ExecutionCostModel,
    NextEventExecutionSimulator,
    SimulationOrder,
    SimulationQuote,
)


T0 = datetime(2026, 3, 2, 14, 0, tzinfo=timezone.utc)
T1 = datetime(2026, 3, 2, 15, 0, tzinfo=timezone.utc)
T2 = datetime(2026, 3, 2, 16, 0, tzinfo=timezone.utc)
COSTS = ExecutionCostModel(
    version="equity-next-event-v1",
    spread_bps=20.0,
    slippage_bps=10.0,
    fee_bps=5.0,
    latency=timedelta(0),
)


def _order(**overrides):
    values = {
        "order_id": "order-1",
        "symbol": "SPY",
        "side": "buy",
        "quantity": 10.0,
        "decision_at": T0,
        "execute_not_before": T1,
        "source": "main_signal",
    }
    values.update(overrides)
    return SimulationOrder(**values)


def _quote(at, **overrides):
    values = {
        "symbol": "SPY",
        "timestamp": at,
        "bid": 100.0,
        "ask": 102.0,
    }
    values.update(overrides)
    return SimulationQuote(**values)


def test_order_cannot_fill_on_decision_event():
    simulator = NextEventExecutionSimulator(COSTS)
    simulator.submit(_order())

    assert simulator.on_quote(_quote(T0, bid=99.0, ask=101.0)) == ()

    fill = simulator.on_quote(_quote(T1))[0]
    assert fill.price > 102.0
    assert fill.quote_timestamp == T1
    assert fill.executed_at == T1
    assert fill.cost_model_version == COSTS.version


def test_partial_fills_are_cumulative_and_need_distinct_quote_events():
    simulator = NextEventExecutionSimulator(COSTS)
    simulator.submit(_order())

    first = simulator.on_quote(_quote(T1, available_quantity=4.0))
    assert len(first) == 1
    assert first[0].incremental_quantity == pytest.approx(4.0)
    assert first[0].cumulative_quantity == pytest.approx(4.0)
    assert simulator.pending_order_count == 1

    assert simulator.on_quote(_quote(T1, available_quantity=100.0)) == ()

    final = simulator.on_quote(_quote(T2, available_quantity=100.0))
    assert len(final) == 1
    assert final[0].incremental_quantity == pytest.approx(6.0)
    assert final[0].cumulative_quantity == pytest.approx(10.0)
    assert simulator.pending_order_count == 0


def test_quote_liquidity_is_shared_fifo_across_orders():
    simulator = NextEventExecutionSimulator(COSTS)
    simulator.submit(_order(order_id="first", quantity=5.0))
    simulator.submit(_order(order_id="second", quantity=5.0))

    fills = simulator.on_quote(_quote(T1, available_quantity=7.0))

    assert [(fill.order_id, fill.incremental_quantity) for fill in fills] == [
        ("first", 5.0),
        ("second", 2.0),
    ]
    assert [order.order_id for order in simulator.pending_orders] == ["second"]


def test_latency_waits_for_the_first_quote_at_or_after_eligibility():
    costs = ExecutionCostModel(
        version="latency-v1",
        spread_bps=5.0,
        slippage_bps=2.0,
        fee_bps=0.3,
        latency=timedelta(minutes=5),
    )
    simulator = NextEventExecutionSimulator(costs)
    simulator.submit(
        _order(execute_not_before=T0 + timedelta(minutes=1))
    )

    assert simulator.on_quote(
        _quote(T0 + timedelta(minutes=4))
    ) == ()
    assert simulator.on_quote(
        _quote(T0 + timedelta(minutes=5))
    )


def test_fill_reports_each_cost_component_and_serializable_provenance():
    simulator = NextEventExecutionSimulator(COSTS)
    simulator.submit(_order())
    fill = simulator.on_quote(_quote(T1))[0]

    assert fill.spread_cost > 0
    assert fill.slippage_cost > 0
    assert fill.fees > 0
    assert fill.source == "main_signal"

    summary = simulator.execution_summary()
    assert summary["execution_cost_model_version"] == COSTS.version
    assert summary["total_fees"] == pytest.approx(fill.fees)
    assert summary["spread_cost"] == pytest.approx(fill.spread_cost)
    assert summary["slippage_cost"] == pytest.approx(fill.slippage_cost)
    assert summary["unfilled_order_count"] == 0
    assert summary["rejected_order_count"] == 0
    assert summary["fill_provenance"][0]["quote_timestamp"] == T1.isoformat()
    assert summary["fill_provenance"][0]["order_id"] == "order-1"


def test_duplicate_order_id_is_rejected_and_counted():
    simulator = NextEventExecutionSimulator(COSTS)
    simulator.submit(_order())

    with pytest.raises(ValueError, match="duplicate order_id"):
        simulator.submit(_order())

    assert simulator.rejected_order_count == 1


@pytest.mark.parametrize(
    ("factory", "message"),
    [
        (
            lambda: SimulationOrder(
                order_id="bad",
                symbol="SPY",
                side="buy",
                quantity=math.nan,
                decision_at=T0,
                execute_not_before=T1,
            ),
            "quantity",
        ),
        (
            lambda: SimulationQuote(
                symbol="SPY",
                timestamp=T1,
                bid=0.0,
                ask=101.0,
            ),
            "bid",
        ),
        (
            lambda: SimulationQuote(
                symbol="SPY",
                timestamp=T1,
                bid=102.0,
                ask=101.0,
            ),
            "bid must be less than ask",
        ),
        (
            lambda: ExecutionCostModel(
                version="bad",
                spread_bps=-1.0,
                slippage_bps=1.0,
                fee_bps=1.0,
                latency=timedelta(0),
            ),
            "spread_bps",
        ),
    ],
)
def test_non_finite_nonpositive_or_crossed_inputs_are_rejected(factory, message):
    with pytest.raises(ValueError, match=message):
        factory()
