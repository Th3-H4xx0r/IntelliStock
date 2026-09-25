"""swing-port Task 1: OrderIntent's optional fields, identity and stored row.

EB (doc 200, alpaca-main, real money) must keep every key and row it has
today. The pins below were computed from the PRE-change code on 2026-09-24.
Never edit a pin to make a test pass: a moved pin IS the regression.
"""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from live_orders import (
    BRACKET_LEG,
    ConfirmedFill,
    LifecycleState,
    OrderIntent,
    OrderSide,
    OrderSource,
)
from live_orders.store import _intent_from_row, _intent_to_row
from live_order_task8_helpers import event, intent as helper_intent, snapshot


BUY_AT = datetime(2026, 9, 24, 13, 31, 5, 123456, tzinfo=timezone.utc)
SELL_AT = datetime(2026, 9, 24, 13, 31, 42, 654321, tzinfo=timezone.utc)
MONDAY_1031_ET = datetime(2026, 9, 28, 14, 31, tzinfo=timezone.utc)


def eb_buy(**changes):
    values = dict(
        account_id="brk-alpaca-main", instance_id="alpaca-main",
        source=OrderSource.STRATEGY, reason="eb_rebalance", symbol="TQQQ",
        side=OrderSide.BUY, quantity=Decimal("12.34567891"), reduce_only=False,
        decision_at=BUY_AT, quote_at=BUY_AT,
        risk_snapshot_id="risk-state:41:2026-09-24T13:31:00+00:00",
        order_type="market", limit_price=None, tif="day",
        extended_hours=False, reference_price=Decimal("88.12"),
    )
    values.update(changes)
    return OrderIntent(**values)


def eb_sell(**changes):
    values = dict(
        side=OrderSide.SELL, symbol="GLD", quantity=Decimal("4.00021400"),
        reduce_only=True, reason="eb_rotation_trim", decision_at=SELL_AT,
        quote_at=SELL_AT, reference_price=Decimal("331.07"),
    )
    values.update(changes)
    return eb_buy(**values)


EB_KEY_PINS = [
    ("buy", lambda: eb_buy(),
     "alpacama-26078e450b92fad1c9e11db27d9425f41505f-0"),
    ("sell", lambda: eb_sell(),
     "alpacama-13d7255096507c7b1e595f1f7d4e26176b707-0"),
    ("risk_exit_sell", lambda: eb_sell(
        source=OrderSource.RISK_EXIT, reason="risk exit", symbol="TQQQ",
        quantity=Decimal("30.5")),
     "alpacama-12e87dd619816d7a76cca48aee87885a2e3e1-0"),
    ("ext_hours_sell", lambda: eb_sell(
        order_type="limit", limit_price=Decimal("329.42"),
        extended_hours=True, quantity=Decimal("4")),
     "alpacama-f6fe0e571a021062fc2ad71af1e5194a440b9-0"),
    ("retry1_buy", lambda: eb_buy(retry_ordinal=1),
     "alpacama-07837bd72b2f938c1f561f661bf19b31b5805-1"),
    ("manual_close", lambda: eb_sell(
        source=OrderSource.MANUAL, reason="operator close position",
        symbol="XLE", quantity=Decimal("7"), reference_price=None),
     "alpacama-f5367b13b448f0607f52740c00d96444c1ef2-0"),
]

EB_BUY_PAYLOAD = (
    '{"account_id":"brk-alpaca-main","instance_id":"alpaca-main",'
    '"retry_ordinal":0,"session_date":"2026-09-24","side":"buy",'
    '"source":"strategy","symbol":"TQQQ"}'
)

EB_BUY_ROW = {
    "account_id": "brk-alpaca-main", "instance_id": "alpaca-main",
    "source": "strategy", "reason": "eb_rebalance", "symbol": "TQQQ",
    "side": "buy", "quantity": "12.34567891", "reduce_only": False,
    "decision_at": "2026-09-24T13:31:05.123456+00:00",
    "quote_at": "2026-09-24T13:31:05.123456+00:00",
    "risk_snapshot_id": "risk-state:41:2026-09-24T13:31:00+00:00",
    "retry_ordinal": 0, "order_type": "market", "limit_price": None,
    "tif": "day", "extended_hours": False, "reference_price": "88.12",
}

# L1 addition, computed from the pre-change code on 2026-09-24: EB's
# extended-hours limit SELL row (the row shape with a non-null limit_price).
EB_EXT_SELL_ROW = {
    "account_id": "brk-alpaca-main", "instance_id": "alpaca-main",
    "source": "strategy", "reason": "eb_rotation_trim", "symbol": "GLD",
    "side": "sell", "quantity": "4", "reduce_only": True,
    "decision_at": "2026-09-24T13:31:42.654321+00:00",
    "quote_at": "2026-09-24T13:31:42.654321+00:00",
    "risk_snapshot_id": "risk-state:41:2026-09-24T13:31:00+00:00",
    "retry_ordinal": 0, "order_type": "limit", "limit_price": "329.42",
    "tif": "day", "extended_hours": True, "reference_price": "331.07",
}


def sto(**changes):
    values = dict(
        account_id="brk-paper", instance_id="swing-paper",
        source=OrderSource.STRATEGY, reason="wheel_sto_put",
        symbol="APH261002P00130000", side=OrderSide.SELL,
        quantity=Decimal("1"), reduce_only=False,
        decision_at=MONDAY_1031_ET, quote_at=MONDAY_1031_ET,
        risk_snapshot_id="risk-1", order_type="limit",
        limit_price=Decimal("1.23"), tif="day", asset_class="us_option",
        position_intent="sell_to_open", contract_multiplier=100,
        underlying="aph", option_type="PUT", strike=130.0,
        expiry="2026-10-02",
    )
    values.update(changes)
    return OrderIntent(**values)


def bracket(**changes):
    values = dict(
        account_id="brk-paper", instance_id="swing-paper",
        source=OrderSource.STRATEGY, reason="swing_entry", symbol="AAPL",
        side=OrderSide.BUY, quantity=Decimal("5"), reduce_only=False,
        decision_at=MONDAY_1031_ET, quote_at=MONDAY_1031_ET,
        risk_snapshot_id="risk-1", order_type="market", tif="gtc",
        reference_price=Decimal("200"), order_class="bracket",
        take_profit_price=218.0, stop_loss_price=188.0,
    )
    values.update(changes)
    return OrderIntent(**values)


# --- EB invariance ----------------------------------------------------------

@pytest.mark.parametrize("name,build,key", EB_KEY_PINS,
                         ids=[p[0] for p in EB_KEY_PINS])
def test_eb_idempotency_keys_are_pinned(name, build, key):
    assert build().idempotency_key == key


def test_eb_buy_identity_payload_is_pinned():
    assert eb_buy().identity_payload == EB_BUY_PAYLOAD


def test_explicit_defaults_do_not_move_an_eb_key():
    assert eb_buy(asset_class="us_equity", contract_multiplier=1,
                  order_class=None).idempotency_key == EB_KEY_PINS[0][2]


def test_eb_row_keeps_its_exact_shape_and_round_trips():
    assert _intent_to_row(eb_buy()) == EB_BUY_ROW
    loaded = _intent_from_row(dict(EB_BUY_ROW))
    assert loaded.idempotency_key == EB_KEY_PINS[0][2]
    assert loaded == eb_buy()


def test_eb_limit_sell_row_keeps_its_exact_shape_and_round_trips():
    order = EB_KEY_PINS[3][1]()
    assert _intent_to_row(order) == EB_EXT_SELL_ROW
    loaded = _intent_from_row(dict(EB_EXT_SELL_ROW))
    assert loaded.idempotency_key == EB_KEY_PINS[3][2]
    assert loaded == order


def test_a_legacy_row_without_optional_keys_keeps_its_key():
    """Rows written before retry/order-type keys existed load on the same key."""
    legacy = {
        name: EB_BUY_ROW[name]
        for name in (
            "account_id", "instance_id", "source", "reason", "symbol", "side",
            "quantity", "reduce_only", "decision_at", "quote_at",
            "risk_snapshot_id",
        )
    }
    assert _intent_from_row(legacy).idempotency_key == EB_KEY_PINS[0][2]


# --- new fields -------------------------------------------------------------

def test_bracket_is_its_own_order_but_leg_prices_are_not_identity():
    plain = bracket(order_class=None, take_profit_price=None,
                    stop_loss_price=None, tif="day")
    entry = bracket()
    drifted = bracket(take_profit_price=219.0, stop_loss_price=187.0,
                      quantity=Decimal("6"))
    assert entry.idempotency_key != plain.idempotency_key
    assert drifted.idempotency_key == entry.idempotency_key
    assert entry.take_profit_price == Decimal("218.0")
    assert entry.stop_loss_price == Decimal("188.0")


def test_a_sell_to_open_rerun_keeps_one_identity_within_the_session():
    """Spec section 9 fix 1: a rerun cannot sell a duplicate put."""
    first = sto()
    later = first.decision_at + timedelta(minutes=7)
    rerun = sto(decision_at=later, quote_at=later, quantity=Decimal("2"),
                limit_price=Decimal("1.10"), risk_snapshot_id="risk-2")
    assert rerun.idempotency_key == first.idempotency_key
    assert rerun.same_identity(first)
    other = sto(symbol="APH261002P00125000", strike=125.0)
    assert other.idempotency_key != first.idempotency_key
    tomorrow = first.decision_at + timedelta(days=1)
    assert sto(decision_at=tomorrow, quote_at=tomorrow).idempotency_key \
        != first.idempotency_key


def test_option_fields_are_normalized():
    order = sto()
    assert order.underlying == "APH"
    assert order.option_type == "put"
    assert order.strike == Decimal("130.0")
    assert order.contract_multiplier == 100
    assert '"asset_class":"us_option"' in order.identity_payload
    assert '"strike":"130"' in order.identity_payload
    assert "decision_minute" not in order.identity_payload


def test_a_bracket_leg_is_keyed_by_the_broker_minted_client_order_id():
    parent = bracket()
    leg = OrderIntent(
        account_id="brk-paper", instance_id="swing-paper",
        source=OrderSource.BRACKET_LEG, reason="bracket_take_profit",
        symbol="AAPL", side=OrderSide.SELL, quantity=Decimal("5"),
        reduce_only=True, decision_at=MONDAY_1031_ET,
        quote_at=MONDAY_1031_ET, risk_snapshot_id="risk-1",
        order_type="limit", limit_price=Decimal("218"), tif="gtc",
        take_profit_price=218.0, stop_loss_price=188.0,
        parent_client_order_id=parent.idempotency_key,
        broker_client_order_id="7d1e0a2c-5b8f-4c1e-9f7a-leg-tp",
    )
    assert leg.idempotency_key == "7d1e0a2c-5b8f-4c1e-9f7a-leg-tp"
    assert BRACKET_LEG == "bracket_leg" == OrderSource.BRACKET_LEG.value
    assert _intent_from_row(_intent_to_row(leg)) == leg
    with pytest.raises(ValueError, match="reserved"):
        eb_buy(broker_client_order_id="alpacama-forged-0")


@pytest.mark.parametrize("build", [sto, bracket])
def test_new_field_rows_round_trip_exactly(build):
    order = build()
    row = _intent_to_row(order)
    assert _intent_from_row(row) == order
    assert _intent_from_row(row).idempotency_key == order.idempotency_key


@pytest.mark.parametrize("changes", [
    {"side": OrderSide.SELL, "reduce_only": True},
    {"quantity": Decimal("5.5")},
    {"take_profit_price": 180.0},
    {"stop_loss_price": None},
    {"tif": "day"},
    {"order_type": "limit", "limit_price": Decimal("200")},
])
def test_a_malformed_bracket_fails_at_construction(changes):
    with pytest.raises((TypeError, ValueError)):
        bracket(**changes)


@pytest.mark.parametrize("changes", [
    {"side": OrderSide.BUY},
    {"contract_multiplier": 1},
    {"quantity": Decimal("1.5")},
    {"order_type": "limit", "extended_hours": True},
    {"strike": None},
    {"option_type": "straddle"},
    {"expiry": "10/02/2026"},
    {"underlying": ""},
    {"position_intent": "sell_short"},
    {"contract_multiplier": True},
])
def test_a_malformed_option_intent_fails_at_construction(changes):
    with pytest.raises((TypeError, ValueError)):
        sto(**changes)


def test_option_fields_on_an_equity_intent_fail_closed():
    with pytest.raises(ValueError):
        eb_buy(strike=130.0)
    with pytest.raises(ValueError):
        eb_buy(position_intent="buy_to_open")
    with pytest.raises(ValueError):
        eb_buy(contract_multiplier=100)


# --- DependencySnapshot / ConfirmedFill ---------------------------------------

def test_only_an_option_snapshot_may_carry_a_short_position():
    order = helper_intent()
    with pytest.raises(ValueError, match="position_quantity"):
        snapshot(order, position_quantity=Decimal("-1"))
    short = snapshot(
        order, asset_class="us_option", position_quantity=Decimal("-1"),
        account_equity=50000, open_short_put_collateral="13000",
        underlying_put_collateral=13000, regular_session_open=1)
    assert short.position_quantity == Decimal("-1")
    assert short.account_equity == Decimal("50000")
    assert short.open_short_put_collateral == Decimal("13000")
    assert short.regular_session_open is True
    plain = snapshot(order)
    assert plain.asset_class == "us_equity"
    assert plain.open_short_put_collateral is None
    assert plain.pending_sell_to_open_collateral == Decimal("0")
    assert plain.max_underlying_collateral_fraction == Decimal("0.25")
    with pytest.raises(ValueError):
        snapshot(order, asset_class="us_option",
                 max_underlying_collateral_fraction=Decimal("1.5"))


def test_confirmed_fill_defaults_to_one_share_per_unit():
    order = helper_intent()
    filled = event(order, state=LifecycleState.FILLED,
                   cumulative=Decimal("5"), average=Decimal("100"))
    fill = ConfirmedFill(
        event=filled, incremental_quantity=Decimal("5"),
        incremental_price=Decimal("100"), incremental_fees=Decimal("0"),
        position_delta=Decimal("5"), cash_delta=Decimal("-500"))
    assert fill.asset_class == "us_equity"
    assert fill.contract_multiplier == 1
    option_fill = replace(fill, asset_class="us_option",
                          contract_multiplier=100)
    assert option_fill.contract_multiplier == 100
    with pytest.raises(ValueError):
        replace(fill, contract_multiplier=0)
    with pytest.raises(ValueError):
        replace(fill, asset_class="crypto")
