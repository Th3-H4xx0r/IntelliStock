"""swing-port Task 2: the adapter contract grows without touching Binance.US
or the abstract surface every adapter must implement."""
import dataclasses
from types import SimpleNamespace

import pytest

from broker_adapters.base import (
    AccountDTO,
    BrokerAdapter,
    OptionActivityDTO,
    OptionContractDTO,
    OptionPositionDTO,
    OptionSnapshotDTO,
    OrderRef,
    PositionDTO,
    is_bracket_child_order,
    is_risk_reducing_order,
)
from broker_adapters.binanceus import BinanceUSAdapter
from broker_adapters.errors import (
    BrokerError,
    FractionalNotAllowed,
    NON_RETRYABLE,
    OptionsNotPermitted,
)


#: The abstract surface as it stood on 2026-09-24. New methods must be
#: non-abstract, or Binance.US (and every test double) stops instantiating.
ABSTRACT_2026_09_24 = {
    "submit_order", "cancel_order", "get_order", "get_order_by_client_id",
    "list_open_orders", "refresh_positions", "refresh_cash",
    "refresh_account", "is_market_open", "health_check", "buy", "sell",
    "execute_signal", "get_positions", "get_positions_value",
    "get_portfolio_value", "get_trade_history", "get_portfolio_history",
    "get_cash", "get_available_cash", "get_initial_value",
    "save_portfolio_snapshot", "print_portfolio",
}

NEW_METHODS = {
    "get_option_chain": (("APH",), {}),
    "get_option_contracts": (("APH",), {}),
    "get_option_snapshots": ((["APH261002P00130000"],), {}),
    "list_option_positions": ((), {}),
    "get_account_options": ((), {}),
    "get_option_activities": ((), {}),
    "get_order_with_legs": (("broker-1",), {}),
    "cancel_orders_confirmed": ((["broker-1"],), {}),
    "list_closed_orders": ((["AAPL"], "2026-09-01"), {}),
    "get_daily_bars": ((["AAPL"], 30), {}),
    "get_latest_trades": ((["AAPL"],), {}),
}


def _concrete():
    body = {name: (lambda self, *a, **k: None)
            for name in BrokerAdapter.__abstractmethods__}
    return type("Concrete", (BrokerAdapter,), body)()


def test_the_abstract_surface_is_unchanged():
    assert set(BrokerAdapter.__abstractmethods__) == ABSTRACT_2026_09_24


@pytest.mark.parametrize("name", sorted(NEW_METHODS))
def test_new_methods_are_non_abstract_and_refuse_loudly(name):
    args, kwargs = NEW_METHODS[name]
    with pytest.raises(NotImplementedError, match="Concrete"):
        getattr(_concrete(), name)(*args, **kwargs)


@pytest.mark.parametrize("name", sorted(NEW_METHODS))
def test_binanceus_inherits_the_refusals_untouched(name):
    assert getattr(BinanceUSAdapter, name) is getattr(BrokerAdapter, name)


def test_order_ref_defaults_leave_old_callers_unchanged():
    ref = OrderRef("b-1", "c-1", "AAPL", "buy", 5.0, "new")
    assert (ref.order_class, ref.legs, ref.position_intent, ref.asset_class,
            ref.order_type, ref.limit_price, ref.stop_price) == (
        None, (), None, None, None, None, None)


def test_account_and_position_dtos_gain_optional_fields():
    account = AccountDTO(equity=1.0, pattern_day_trader=False,
                         daytrade_count=0, account_blocked=False,
                         trading_blocked=False)
    assert account.options_trading_level is None
    assert account.options_buying_power is None
    position = PositionDTO("AAPL", 1.0, 100.0, 100.0)
    assert position.asset_class is None and position.multiplier == 1
    # Ruling F4: option positions carry option_type; an equity row leaves it None.
    assert position.option_type is None


def test_option_dtos_are_frozen():
    contract = OptionContractDTO("APH261002P00130000", "APH", "put", 130.0,
                                 "2026-10-02", 120, 1.1)
    position = OptionPositionDTO("APH261002P00130000", "APH", "put", 130.0,
                                 "2026-10-02", -1, 1.23, 1.1, -110.0, 13.0)
    snap = OptionSnapshotDTO("APH261002P00130000", 1.0, 1.2, 1.1, 0.3,
                             -0.25, 0.05, -0.04, 0.1, None)
    activity = OptionActivityDTO("a-1", "OPASN", "APH261002P00130000",
                                 -1.0, "2026-10-02", None)
    assert position.multiplier == 100
    assert position.option_type == "put"
    for dto in (contract, position, snap, activity):
        with pytest.raises(dataclasses.FrozenInstanceError):
            dto.symbol = "X"


@pytest.mark.parametrize("order,child,reducing", [
    (SimpleNamespace(order_class="bracket", side="sell", status="new"), True, True),
    (SimpleNamespace(order_class=SimpleNamespace(value="bracket"),
                     side=SimpleNamespace(value="sell"),
                     status=SimpleNamespace(value="held")), True, True),
    (SimpleNamespace(order_class="bracket", side="buy", status="filled"), False, False),
    (SimpleNamespace(order_class="oco", side="sell", status="new"), True, True),
    (SimpleNamespace(order_class="simple", side="sell", status="new"), False, False),
    (SimpleNamespace(order_class="simple", side="buy", status="held"), False, False),
    (SimpleNamespace(side="sell", status="new"), False, False),
    # Ruling F1: a closing position_intent counts only on a us_option order.
    (SimpleNamespace(order_class="simple", side="buy", status="new",
                     asset_class="us_option",
                     position_intent="buy_to_close"), False, True),
    (SimpleNamespace(order_class="simple", side="sell", status="new",
                     asset_class="us_option",
                     position_intent=SimpleNamespace(value="sell_to_open")), False, False),
])
def test_bracket_child_and_risk_reducing_classification(order, child, reducing):
    assert is_bracket_child_order(order) is child
    assert is_risk_reducing_order(order) is reducing


# --- Ruling F1: EB's stock orders stay cancellable ----------------------------
#
# Alpaca may tag a STOCK order with a position_intent (sell_to_close on an EB
# trim). A halt or the kill rung on alpaca-main must keep cancelling EB's
# working sells exactly as today, so a stock order's position_intent is never
# read as risk-reducing. These shapes mirror what EB's working orders look like
# as OrderRef rows (today: no new fields; after Task 3: Alpaca's fields copied).

EB_SELL_CID = "alpacama-13d7255096507c7b1e595f1f7d4e26176b707-0"

EB_STOCK_ORDERS = {
    "eb_sell_today": OrderRef("b-eb", EB_SELL_CID, "GLD", "sell", 4.000214, "new"),
    "eb_sell_sell_to_close": OrderRef(
        "b-eb", EB_SELL_CID, "GLD", "sell", 4.000214, "new",
        order_class="simple", position_intent="sell_to_close",
        asset_class="us_equity", order_type="market"),
    "eb_sell_sell_to_close_enums": SimpleNamespace(
        broker_order_id="b-eb", client_order_id=EB_SELL_CID, symbol="GLD",
        order_class=SimpleNamespace(value="simple"),
        side=SimpleNamespace(value="sell"),
        status=SimpleNamespace(value="accepted"),
        asset_class=SimpleNamespace(value="us_equity"),
        position_intent=SimpleNamespace(value="sell_to_close")),
    "eb_sell_no_position_intent_attr": SimpleNamespace(
        broker_order_id="b-eb", client_order_id=EB_SELL_CID, symbol="GLD",
        order_class="simple", side="sell", status="new",
        asset_class="us_equity"),
    "eb_sell_intent_without_asset_class": OrderRef(
        "b-eb", EB_SELL_CID, "GLD", "sell", 4.000214, "new",
        position_intent="sell_to_close"),
    "eb_held_simple_sell": OrderRef(
        "b-eb", EB_SELL_CID, "GLD", "sell", 4.000214, "held",
        order_class="simple", position_intent="sell_to_close",
        asset_class="us_equity"),
    "eb_buy_buy_to_close": OrderRef(
        "b-eb2", "alpacama-26078e450b92fad1c9e11db27d9425f41505f-0", "TQQQ",
        "buy", 12.34567891, "new", order_class="simple",
        position_intent="buy_to_close", asset_class="us_equity"),
}


@pytest.mark.parametrize("name", sorted(EB_STOCK_ORDERS))
def test_an_eb_stock_order_is_never_risk_reducing(name):
    order = EB_STOCK_ORDERS[name]
    assert is_bracket_child_order(order) is False
    assert is_risk_reducing_order(order) is False


def test_the_halt_skip_rule_still_cancels_every_eb_stock_order():
    """The halt and kill-rung loops (Task 11) skip only risk-reducing orders;
    every EB stock order, with or without a position_intent, stays on the
    cancel list, while a real option buy-to-close and a bracket leg do not."""
    option_btc = OrderRef(
        "b-opt", "swingpap-btc-0", "APH261002P00130000", "buy", 1.0, "new",
        order_class="simple", position_intent="buy_to_close",
        asset_class="us_option", order_type="limit", limit_price=0.4)
    leg = OrderRef("b-leg", "leg-tp", "AAPL", "sell", 5.0, "new",
                   order_class="bracket", order_type="limit",
                   limit_price=218.0)
    working = [*EB_STOCK_ORDERS.values(), option_btc, leg]
    cancelled = [o for o in working if not is_risk_reducing_order(o)]
    assert cancelled == list(EB_STOCK_ORDERS.values())


def test_options_not_permitted_is_definitive_and_never_retried():
    assert issubclass(OptionsNotPermitted, BrokerError)
    assert not issubclass(OptionsNotPermitted, FractionalNotAllowed)
    assert OptionsNotPermitted.broker_definitive_rejection is True
    assert OptionsNotPermitted in NON_RETRYABLE
