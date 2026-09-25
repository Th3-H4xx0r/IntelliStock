"""The swing-port order, bar and fill types (spec 6.2, interfaces section 4)."""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from simulated_execution import (  # noqa: E402
    SimulationBarEvent,
    SimulationFill,
    SimulationOrder,
)

T0 = datetime(2026, 3, 2, 13, 0, tzinfo=timezone.utc)
OPEN = datetime(2026, 3, 2, 14, 30, tzinfo=timezone.utc)
CLOSE = datetime(2026, 3, 2, 21, 0, tzinfo=timezone.utc)


def _order(**overrides):
    values = dict(order_id="o1", symbol="aapl", side="buy", quantity=10.0,
                  decision_at=T0, execute_not_before=T0, source="main_signal")
    values.update(overrides)
    return SimulationOrder(**values)


def test_new_order_fields_default_to_todays_behaviour():
    order = _order()
    assert order.bracket is None
    assert order.whole_shares is False
    assert order.fill_at_next_open is False


def test_bracket_is_normalised_to_two_float_prices():
    order = _order(bracket={"take_profit_price": "218", "stop_loss_price": 188,
                            "ignored": 1})
    assert order.bracket == {"take_profit_price": 218.0,
                             "stop_loss_price": 188.0}


@pytest.mark.parametrize("bracket, message", [
    ({"take_profit_price": 188.0, "stop_loss_price": 218.0}, "below"),
    ({"take_profit_price": 200.0, "stop_loss_price": 200.0}, "below"),
    ({"take_profit_price": 218.0}, "stop_loss_price"),
    ({"take_profit_price": float("nan"), "stop_loss_price": 1.0}, "finite"),
    ({"take_profit_price": 218.0, "stop_loss_price": -1.0}, "positive"),
    ([218.0, 188.0], "mapping"),
])
def test_bad_brackets_are_refused(bracket, message):
    with pytest.raises(ValueError, match=message):
        _order(bracket=bracket)


def test_a_sell_cannot_carry_a_bracket():
    with pytest.raises(ValueError, match="buy orders"):
        _order(side="sell", bracket={"take_profit_price": 2.0,
                                     "stop_loss_price": 1.0})


def test_whole_shares_needs_a_whole_quantity():
    assert _order(quantity=7.0, whole_shares=True).whole_shares is True
    with pytest.raises(ValueError, match="whole-share"):
        _order(quantity=7.5, whole_shares=True)


def test_a_next_open_order_is_a_market_order():
    assert _order(fill_at_next_open=True).fill_at_next_open is True
    with pytest.raises(ValueError, match="market orders"):
        _order(fill_at_next_open=True, limit_price=100.0)


def test_bar_event_normalises_and_validates():
    event = SimulationBarEvent(symbol=" aapl ", open="100", high=105.0,
                               low=98.0, close=101.0, bar_ts=OPEN,
                               available_at=CLOSE)
    assert event.symbol == "AAPL"
    assert event.open == 100.0
    with pytest.raises(ValueError, match="high cannot be below low"):
        SimulationBarEvent(symbol="AAPL", open=100.0, high=97.0, low=98.0,
                           close=99.0, bar_ts=OPEN, available_at=CLOSE)
    with pytest.raises(ValueError, match="available_at cannot precede"):
        SimulationBarEvent(symbol="AAPL", open=100.0, high=105.0, low=98.0,
                           close=101.0, bar_ts=CLOSE, available_at=OPEN)
    with pytest.raises(ValueError, match="positive"):
        SimulationBarEvent(symbol="AAPL", open=0.0, high=105.0, low=98.0,
                           close=101.0, bar_ts=OPEN, available_at=CLOSE)


def _fill(**overrides):
    values = dict(order_id="o1", symbol="AAPL", side="sell",
                  incremental_quantity=5.0, cumulative_quantity=5.0,
                  price=100.0, fees=0.1, spread_cost=0.0, slippage_cost=0.0,
                  quote_timestamp=OPEN, executed_at=OPEN,
                  cost_model_version="v", source="main_signal")
    values.update(overrides)
    return SimulationFill(**values)


def test_a_plain_fill_serialises_exactly_as_before():
    """No `exit_reason` key at all: fill_provenance of every non-bracket run
    stays byte-identical."""
    assert "exit_reason" not in _fill().as_dict()
    assert list(_fill().as_dict()) == [
        "order_id", "symbol", "side", "incremental_quantity",
        "cumulative_quantity", "price", "fees", "spread_cost",
        "slippage_cost", "quote_timestamp", "executed_at",
        "cost_model_version", "source", "order_quantity", "is_final",
    ]


def test_a_bracket_fill_carries_its_exit_reason():
    fill = _fill(exit_reason=" stop_loss ", source="bracket_sl:o0")
    assert fill.exit_reason == "stop_loss"
    assert fill.as_dict()["exit_reason"] == "stop_loss"
    with pytest.raises(ValueError, match="exit_reason"):
        _fill(exit_reason="  ")
