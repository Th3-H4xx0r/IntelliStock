from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from live_orders import OrderIntent, OrderSide, OrderSource


NOW = datetime(2026, 7, 27, 14, 30, tzinfo=timezone.utc)


def _intent(**changes):
    values = {
        "account_id": "acct-1",
        "instance_id": "instance-1",
        "source": OrderSource.STRATEGY,
        "reason": "allocation",
        "symbol": "aapl",
        "side": OrderSide.BUY,
        "quantity": Decimal("2.50"),
        "reduce_only": False,
        "decision_at": NOW,
        "quote_at": NOW,
        "risk_snapshot_id": "risk-7",
        "retry_ordinal": 0,
    }
    values.update(changes)
    return OrderIntent(**values)


def test_order_intent_is_normalized_immutable_and_deterministic():
    intent = _intent()
    same = _intent()

    assert intent.symbol == "AAPL"
    assert intent.quantity == Decimal("2.50")
    assert intent.idempotency_key == same.idempotency_key
    assert intent.idempotency_key.startswith("instance-")
    assert len(intent.idempotency_key) <= 48
    with pytest.raises(FrozenInstanceError):
        intent.quantity = Decimal("9")


def test_retry_ordinal_changes_only_the_deterministic_key():
    original = _intent()
    retry = replace(original, retry_ordinal=1)

    assert retry.idempotency_key != original.idempotency_key
    assert retry.symbol == original.symbol
    assert retry.quantity == original.quantity


@pytest.mark.parametrize(
    "change",
    [
        {"quantity": Decimal("0")},
        {"quantity": Decimal("NaN")},
        {"decision_at": datetime(2026, 7, 27, 14, 30)},
        {"quote_at": datetime(2026, 7, 27, 14, 30)},
        {"risk_snapshot_id": ""},
        {"retry_ordinal": -1},
    ],
)
def test_invalid_intents_fail_closed_at_construction(change):
    with pytest.raises((TypeError, ValueError)):
        _intent(**change)
