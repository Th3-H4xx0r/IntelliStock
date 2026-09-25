"""swing-port L3 hardening of OrderIntent (L1 review, minors 1 and 2).

1. An option intent stores its expiry normalised, so one contract cannot be
   keyed twice through two spellings of the same date.
2. Leg prices are refused unless the intent is a bracket parent (or the
   record of one of its legs): a missed order_class flag must never turn a
   protected entry into an unprotected one.

EB intents carry neither field and keep their keys (pinned below and in
tests/test_swing_live_types.py)."""
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from live_orders import OrderIntent, OrderSide, OrderSource
from live_orders.store import _intent_from_row, _intent_to_row
from swing_live_fixtures import bracket_intent, option_intent

AT = datetime(2026, 9, 24, 13, 31, 5, tzinfo=timezone.utc)
#: Keys computed from the pre-change code (HEAD 11d7b8e). Never edit them.
EB_BUY_KEY = "alpacama-26078e450b92fad1c9e11db27d9425f41505f-0"
EB_SELL_KEY = "alpacama-7557bf640afc31bc294847ecb03cf661abe52-0"


def eb(**changes):
    values = dict(
        account_id="brk-alpaca-main", instance_id="alpaca-main",
        source=OrderSource.STRATEGY, reason="eb_rebalance", symbol="TQQQ",
        side=OrderSide.BUY, quantity=Decimal("10"), reduce_only=False,
        decision_at=AT, quote_at=AT, risk_snapshot_id="risk-1",
        reference_price=Decimal("88"))
    values.update(changes)
    return OrderIntent(**values)


def test_eb_keys_are_unchanged():
    buy = eb()
    sell = eb(side=OrderSide.SELL, reduce_only=True, quantity=Decimal("4"),
              source=OrderSource.RISK_EXIT)
    assert (buy.idempotency_key, sell.idempotency_key) == (
        EB_BUY_KEY, EB_SELL_KEY)
    assert buy.expiry is None and buy.take_profit_price is None
    assert _intent_from_row(_intent_to_row(buy)) == buy


# --- 1. expiry normalisation --------------------------------------------------

@pytest.mark.parametrize("spelling", ["20261009", "2026-10-09", " 2026-10-09 ",
                                      "2026-W41-5"])
def test_every_spelling_of_one_expiry_is_one_contract_key(spelling):
    canonical = option_intent(expiry="2026-10-09")
    order = option_intent(expiry=spelling)
    assert order.expiry == "2026-10-09"
    assert order.idempotency_key == canonical.idempotency_key
    assert order.same_identity(canonical)
    row = _intent_to_row(order)
    assert row["expiry"] == "2026-10-09"
    assert _intent_from_row(row) == canonical


@pytest.mark.parametrize("bad", ["10/09/2026", "2026-13-01", "", "soon"])
def test_an_unreadable_expiry_is_still_refused(bad):
    with pytest.raises(ValueError, match="expiry"):
        option_intent(expiry=bad)


def test_a_different_expiry_is_a_different_contract():
    assert option_intent(expiry="20261016").idempotency_key != \
        option_intent(expiry="20261009").idempotency_key


# --- 2. leg prices belong to a bracket ----------------------------------------

@pytest.mark.parametrize("prices", [
    {"take_profit_price": Decimal("109")},
    {"stop_loss_price": Decimal("94")},
    {"take_profit_price": Decimal("109"), "stop_loss_price": Decimal("94")},
])
@pytest.mark.parametrize("source", [OrderSource.STRATEGY, OrderSource.MANUAL,
                                    OrderSource.RISK_EXIT,
                                    OrderSource.RESIDUAL_SLEEVE])
def test_leg_prices_without_a_bracket_flag_are_refused(prices, source):
    """The entry a strategy meant to protect would otherwise go out as a
    plain market buy with no exit legs at all."""
    with pytest.raises(ValueError, match="bracket"):
        bracket_intent(order_class=None, source=source, tif="day", **prices)


def test_an_option_cannot_carry_leg_prices():
    with pytest.raises(ValueError, match="bracket"):
        option_intent(stop_loss_price=Decimal("0.5"))


def test_a_bracket_parent_still_carries_both_prices():
    order = bracket_intent()
    assert (order.order_class, order.take_profit_price,
            order.stop_loss_price) == ("bracket", Decimal("109"),
                                       Decimal("94"))


def test_the_record_of_a_bracket_leg_may_carry_its_parents_prices():
    """A leg row is record-only (LiveOrderService.submit refuses the
    bracket_leg source), so the prices it carries protect nothing and hide
    nothing: they describe the leg Alpaca already holds."""
    parent = bracket_intent()
    leg = OrderIntent(
        account_id=parent.account_id, instance_id=parent.instance_id,
        source=OrderSource.BRACKET_LEG, reason="bracket_stop_loss",
        symbol="AAPL", side=OrderSide.SELL, quantity=Decimal("5"),
        reduce_only=True, decision_at=parent.decision_at,
        quote_at=parent.quote_at, risk_snapshot_id="risk-1", tif="gtc",
        take_profit_price=Decimal("109"), stop_loss_price=Decimal("94"),
        parent_client_order_id=parent.idempotency_key,
        broker_client_order_id="7d1e0a2c-leg-sl")
    assert leg.stop_loss_price == Decimal("94") and leg.order_class is None
