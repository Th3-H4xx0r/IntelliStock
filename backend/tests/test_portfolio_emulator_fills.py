from datetime import datetime, timedelta, timezone

import pytest

from backend.portfolio_emulator import (
    PortfolioEmulator,
    create_backtest_emulator,
)
from backend.simulated_execution import (
    ExecutionCostModel,
    NextEventExecutionSimulator,
    SimulationFill,
    SimulationOrder,
    SimulationQuote,
)


T0 = datetime(2026, 3, 2, 14, 0, tzinfo=timezone.utc)
T1 = datetime(2026, 3, 2, 15, 0, tzinfo=timezone.utc)
COSTS = ExecutionCostModel(
    version="equity-next-event-v1",
    spread_bps=10.0,
    slippage_bps=5.0,
    fee_bps=2.0,
    latency=timedelta(0),
)


def _fill(
    *,
    order_id="fill-1",
    side="buy",
    incremental_quantity=10.0,
    cumulative_quantity=10.0,
    price=101.0,
    fees=1.0,
    spread_cost=5.0,
    slippage_cost=2.0,
):
    return SimulationFill(
        order_id=order_id,
        symbol="SPY",
        side=side,
        incremental_quantity=incremental_quantity,
        cumulative_quantity=cumulative_quantity,
        price=price,
        fees=fees,
        spread_cost=spread_cost,
        slippage_cost=slippage_cost,
        quote_timestamp=T1,
        executed_at=T1,
        cost_model_version=COSTS.version,
        source="test",
    )


def _configured_emulator(cash=10_000.0):
    simulator = NextEventExecutionSimulator(COSTS)
    emulator = PortfolioEmulator(
        cash,
        execution_simulator=simulator,
        execution_delay=timedelta(hours=1),
    )
    return emulator, simulator


def test_accounting_changes_only_on_confirmed_fill():
    emulator, _simulator = _configured_emulator()
    before = emulator.get_cash()
    emulator.record_order(
        SimulationOrder(
            order_id="pending",
            symbol="SPY",
            side="buy",
            quantity=10.0,
            decision_at=T0,
            execute_not_before=T1,
        )
    )

    assert emulator.get_cash() == before
    assert emulator.get_positions() == {}
    assert emulator.get_trade_history() == []

    emulator.apply_fill(_fill(order_id="pending"))

    assert emulator.get_cash() == pytest.approx(before - 1_011.0)
    assert emulator.get_positions() == {"SPY": pytest.approx(10.0)}
    assert emulator.get_trade_history()[0]["order_id"] == "pending"


def test_duplicate_cumulative_fill_is_exactly_once():
    emulator = PortfolioEmulator(10_000.0)
    fill = _fill(order_id="idempotent")

    emulator.apply_fill(fill)
    emulator.apply_fill(fill)

    assert emulator.get_cash() == pytest.approx(8_989.0)
    assert emulator.get_positions()["SPY"] == pytest.approx(10.0)
    assert len(emulator.get_trade_history()) == 1


def test_partial_fill_applies_only_the_new_cumulative_delta():
    emulator = PortfolioEmulator(10_000.0)
    emulator.apply_fill(
        _fill(
            order_id="partial",
            incremental_quantity=4.0,
            cumulative_quantity=4.0,
            fees=0.4,
        )
    )
    emulator.apply_fill(
        _fill(
            order_id="partial",
            incremental_quantity=6.0,
            cumulative_quantity=10.0,
            fees=0.6,
        )
    )

    assert emulator.get_positions()["SPY"] == pytest.approx(10.0)
    assert emulator.get_cash() == pytest.approx(10_000.0 - 1_011.0)
    assert len(emulator.get_trade_history()) == 2


def test_out_of_order_or_inconsistent_cumulative_fill_is_rejected_without_mutation():
    emulator = PortfolioEmulator(10_000.0)
    emulator.apply_fill(
        _fill(
            order_id="partial",
            incremental_quantity=4.0,
            cumulative_quantity=4.0,
            fees=0.4,
        )
    )
    before = (
        emulator.get_cash(),
        emulator.get_positions(),
        emulator.get_trade_history(),
    )

    with pytest.raises(ValueError, match="incremental_quantity"):
        emulator.apply_fill(
            _fill(
                order_id="partial",
                incremental_quantity=3.0,
                cumulative_quantity=10.0,
                fees=0.3,
            )
        )

    assert (
        emulator.get_cash(),
        emulator.get_positions(),
        emulator.get_trade_history(),
    ) == before


def test_sell_fill_credits_only_net_confirmed_proceeds():
    emulator = PortfolioEmulator(10_000.0)
    emulator.apply_fill(_fill(order_id="seed", fees=0.0))
    before = emulator.get_cash()

    emulator.apply_fill(
        _fill(
            order_id="sell-1",
            side="sell",
            incremental_quantity=4.0,
            cumulative_quantity=4.0,
            price=110.0,
            fees=2.0,
        )
    )

    assert emulator.get_cash() == pytest.approx(before + 438.0)
    assert emulator.get_positions()["SPY"] == pytest.approx(6.0)


@pytest.mark.parametrize(
    ("broker_path", "signal", "sell_fraction"),
    [
        ("scheduled_start", 1, 1.0),
        ("scheduled_same_bar", -1, 0.5),
        ("residual_sleeve", 1, 1.0),
        ("main_signal", -1, 1.0),
    ],
)
def test_each_equity_broker_emission_shape_waits_for_next_quote(
    broker_path, signal, sell_fraction
):
    emulator, simulator = _configured_emulator()
    if signal == -1:
        emulator.apply_fill(_fill(order_id=f"{broker_path}-seed", fees=0.0))
    before_cash = emulator.get_cash()
    before_positions = emulator.get_positions()

    accepted = emulator.execute_signal(
        "SPY",
        signal,
        100.0,
        timestamp=T0,
        cash_per_trade=1_000.0,
        sell_fraction=sell_fraction,
        order_source=broker_path,
    )

    assert accepted is True
    assert emulator.get_cash() == before_cash
    assert emulator.get_positions() == before_positions
    assert simulator.pending_orders[-1].source == broker_path
    assert emulator.process_quote(
        SimulationQuote.from_mid(
            symbol="SPY",
            timestamp=T0,
            mid=100.0,
            spread_bps=COSTS.spread_bps,
        )
    ) == ()
    fills = emulator.process_quote(
        SimulationQuote.from_mid(
            symbol="SPY",
            timestamp=T1,
            mid=100.0,
            spread_bps=COSTS.spread_bps,
        )
    )
    assert fills
    assert fills[-1].source == broker_path


def test_default_legacy_helpers_remain_immediate_for_compatibility():
    emulator = PortfolioEmulator(1_000.0)

    assert emulator.execute_signal(
        "SPY", 1, 100.0, timestamp=T0, cash_per_trade=100.0
    )

    assert emulator.get_cash() == pytest.approx(900.0)
    assert emulator.get_positions()["SPY"] == pytest.approx(1.0)


def test_broker_factory_activates_next_event_only_for_equity_backtests():
    equity = create_backtest_emulator(
        initial_cash=1_000.0,
        taker_fee=0.123,
        is_crypto=False,
        execution_delay=timedelta(hours=1),
        cost_model=COSTS,
    )
    crypto = create_backtest_emulator(
        initial_cash=1_000.0,
        taker_fee=0.002,
        is_crypto=True,
        execution_delay=timedelta(hours=1),
        cost_model=COSTS,
    )

    assert equity.execute_signal(
        "SPY", 1, 100.0, timestamp=T0, cash_per_trade=100.0
    )
    assert equity.get_cash() == 1_000.0
    assert equity.pending_execution_symbols() == ("SPY",)

    assert crypto.execute_signal(
        "BTC/USD", 1, 100.0, timestamp=T0, cash_per_trade=100.0
    )
    assert crypto.get_cash() == pytest.approx(900.0)
    assert crypto.get_positions()["BTC/USD"] == pytest.approx(0.998)
    assert crypto.pending_execution_symbols() == ()


def test_broker_price_event_fills_all_pending_symbols_from_mid_prices():
    emulator = create_backtest_emulator(
        initial_cash=10_000.0,
        taker_fee=None,
        is_crypto=False,
        execution_delay=timedelta(hours=1),
        cost_model=COSTS,
    )
    assert emulator.execute_signal(
        "SPY", 1, 100.0, timestamp=T0, cash_per_trade=1_000.0
    )
    assert emulator.execute_signal(
        "AAPL", 1, 200.0, timestamp=T0, cash_per_trade=1_000.0
    )

    assert emulator.process_price_event(
        {"SPY": 100.0, "AAPL": 200.0}, timestamp=T0
    ) == ()
    fills = emulator.process_price_event(
        {"SPY": 101.0, "AAPL": 202.0}, timestamp=T1
    )

    assert {fill.symbol for fill in fills} == {"SPY", "AAPL"}
    assert emulator.pending_execution_symbols() == ()
