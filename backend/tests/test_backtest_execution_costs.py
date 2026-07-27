from datetime import datetime, timedelta, timezone

import pytest

from backend.backtest_summary import compute_backtest_summary
from backend.portfolio_emulator import PortfolioEmulator
from backend.simulated_execution import (
    ExecutionCostModel,
    NextEventExecutionSimulator,
    SimulationQuote,
)


T0 = datetime(2026, 3, 2, 14, 0, tzinfo=timezone.utc)
T1 = datetime(2026, 3, 2, 15, 0, tzinfo=timezone.utc)
T2 = datetime(2026, 3, 2, 16, 0, tzinfo=timezone.utc)
COSTS = ExecutionCostModel(
    version="equity-next-event-v1",
    spread_bps=10.0,
    slippage_bps=5.0,
    fee_bps=2.0,
    latency=timedelta(0),
)


def _quote(at):
    return SimulationQuote.from_mid(
        symbol="SPY",
        timestamp=at,
        mid=100.0,
        spread_bps=COSTS.spread_bps,
    )


def test_zero_alpha_round_trip_loses_after_versioned_costs():
    simulator = NextEventExecutionSimulator(COSTS)
    emulator = PortfolioEmulator(
        10_000.0,
        execution_simulator=simulator,
        execution_delay=timedelta(hours=1),
    )

    assert emulator.execute_signal(
        "SPY", 1, 100.0, timestamp=T0, cash_per_trade=5_000.0
    )
    buy_fills = emulator.process_quote(_quote(T1))
    assert buy_fills
    assert emulator.execute_signal(
        "SPY", -1, 100.0, timestamp=T1, sell_fraction=1.0
    )
    sell_fills = emulator.process_quote(_quote(T2))
    assert sell_fills

    emulator.save_portfolio_snapshot({"SPY": 100.0}, timestamp=T2)
    summary = compute_backtest_summary(
        emulator, emulator.get_portfolio_history(), 10_000.0
    )

    assert summary["pnl"] < 0
    assert summary["execution_cost_model_version"] == COSTS.version
    assert summary["total_fees"] > 0
    assert summary["spread_cost"] > 0
    assert summary["slippage_cost"] > 0
    assert len(summary["fill_provenance"]) == 2
    assert summary["unfilled_order_count"] == 0
    assert summary["rejected_order_count"] == 0


def test_unfilled_and_rejected_orders_are_persisted_in_summary():
    simulator = NextEventExecutionSimulator(COSTS)
    emulator = PortfolioEmulator(
        10_000.0,
        execution_simulator=simulator,
        execution_delay=timedelta(hours=1),
    )
    assert emulator.execute_signal(
        "SPY", 1, 100.0, timestamp=T0, cash_per_trade=1_000.0
    )
    pending = simulator.pending_orders[0]
    with pytest.raises(ValueError, match="duplicate order_id"):
        emulator.record_order(pending)

    emulator.save_portfolio_snapshot({"SPY": 100.0}, timestamp=T0)
    summary = compute_backtest_summary(
        emulator, emulator.get_portfolio_history(), 10_000.0
    )

    assert summary["execution_cost_model_version"] == COSTS.version
    assert summary["unfilled_order_count"] == 1
    assert summary["rejected_order_count"] == 1
    assert summary["fill_provenance"] == []


def test_legacy_summary_has_no_promotable_execution_provenance():
    emulator = PortfolioEmulator(1_000.0)
    emulator.save_portfolio_snapshot({"SPY": 100.0}, timestamp=T0)

    summary = compute_backtest_summary(
        emulator, emulator.get_portfolio_history(), 1_000.0
    )

    assert summary["execution_provenance_complete"] is False
    assert summary["execution_cost_model_version"] is None
