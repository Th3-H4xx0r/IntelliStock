from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from live_orders import (
    BrokerOrderEvent,
    ConfirmedFill,
    LifecycleState,
    OrderSide,
)
from live_risk_state import (
    InMemoryRiskBackend,
    RiskStateBootstrapRefused,
    RiskStateStore,
    RiskStateUnavailable,
    apply_confirmed_fill,
    evaluate_drawdown,
    initialize_live_risk_state,
    initialize_risk_state,
)


NOW = datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc)


def _fill(side, quantity, price):
    event = BrokerOrderEvent(
        event_id=f"fill-{side.value}-{quantity}",
        account_id="account-1",
        instance_id="alpaca-main",
        client_order_id=f"alpaca-cid-{side.value}-{quantity}",
        broker_order_id="broker-1",
        symbol="SQQQ",
        side=side,
        state=LifecycleState.FILLED,
        cumulative_quantity=quantity,
        cumulative_average_price=price,
        cumulative_fees=Decimal("0"),
        occurred_at=NOW,
        incremental_quantity=quantity,
        incremental_price=price,
    )
    sign = Decimal("1") if side is OrderSide.BUY else Decimal("-1")
    return ConfirmedFill(
        event=event,
        incremental_quantity=quantity,
        incremental_price=price,
        incremental_fees=Decimal("0"),
        position_delta=sign * quantity,
        cash_delta=-sign * quantity * price,
    )


def test_unknown_risk_state_fails_closed():
    store = RiskStateStore(InMemoryRiskBackend())
    with pytest.raises(RiskStateUnavailable, match="missing"):
        store.load_required("alpaca-main", "account-1")


def test_restart_preserves_high_water_and_sleeve_protection():
    store = RiskStateStore(InMemoryRiskBackend())
    state = initialize_risk_state(
        "alpaca-main", "account-1", Decimal("10000"), NOW
    )
    state = apply_confirmed_fill(
        state, _fill(OrderSide.BUY, Decimal("10"), Decimal("30"))
    )
    state = apply_confirmed_fill(
        state, _fill(OrderSide.BUY, Decimal("5"), Decimal("36"))
    )
    saved = store.save(state)
    loaded = store.load_required("alpaca-main", "account-1")

    assert loaded == saved
    sleeve = loaded.sleeve("SQQQ")
    assert sleeve.quantity == Decimal("15")
    assert sleeve.entry_basis == Decimal("32")
    assert sleeve.peak_price == Decimal("36")


def test_continuous_drawdown_kill_is_account_wide_and_preserves_peak():
    state = initialize_risk_state(
        "alpaca-main", "account-1", Decimal("100"), NOW
    )
    evaluated = evaluate_drawdown(
        state, Decimal("88"), NOW + timedelta(minutes=1)
    )
    assert evaluated.high_water_equity == Decimal("100")
    assert evaluated.drawdown == Decimal("0.12")
    assert evaluated.level == "kill"
    assert evaluated.new_exposure_allowed is False

    recovered = evaluate_drawdown(
        evaluated, Decimal("95"), NOW + timedelta(minutes=2)
    )
    assert recovered.high_water_equity == Decimal("100")


def test_store_rejects_identity_and_high_water_regression():
    backend = InMemoryRiskBackend()
    store = RiskStateStore(backend)
    saved = store.save(
        initialize_risk_state(
            "alpaca-main", "account-1", Decimal("100"), NOW
        )
    )
    lowered = evaluate_drawdown(
        saved, Decimal("90"), NOW + timedelta(minutes=1)
    )
    lowered = replace(lowered, high_water_equity=Decimal("99"))
    with pytest.raises(ValueError, match="high-water"):
        store.save(lowered)


def test_exposure_caps_follow_current_equity_instead_of_freezing():
    """Caps were written once at bootstrap and never updated, so they stayed
    denominated in the equity the row was created with — forever."""

    state = initialize_risk_state(
        "alpaca-main", "account-1", Decimal("10000"), NOW
    )
    assert state.max_order_notional == Decimal("1000")

    halved = evaluate_drawdown(
        state, Decimal("5000"), NOW + timedelta(minutes=1)
    )
    assert halved.max_order_notional == Decimal("500")
    assert halved.max_symbol_notional == Decimal("1000")
    assert halved.max_leveraged_notional == Decimal("500")

    grown = evaluate_drawdown(
        halved, Decimal("20000"), NOW + timedelta(minutes=2)
    )
    assert grown.max_order_notional == Decimal("2000")
    assert grown.max_symbol_notional == Decimal("4000")


def test_uncapped_risk_state_is_not_given_a_cap_by_a_refresh():
    state = replace(
        initialize_risk_state("alpaca-main", "account-1", Decimal("100"), NOW),
        max_order_notional=None,
    )
    refreshed = evaluate_drawdown(state, Decimal("120"), NOW + timedelta(minutes=1))

    assert refreshed.max_order_notional is None
    assert refreshed.max_symbol_notional == Decimal("24")


def test_live_account_bootstraps_risk_state_from_a_flat_book():
    """Without a row the gate blocks every buy, so a funded account would boot
    silently sell-only. A flat book makes the bootstrap as safe as paper."""

    state = initialize_live_risk_state(
        "alpaca-main",
        "account-1",
        Decimal("25000"),
        NOW,
        paper=False,
        open_position_count=0,
        open_order_count=0,
    )

    assert state.high_water_equity == Decimal("25000")
    assert state.new_exposure_allowed is True
    assert state.max_order_notional == Decimal("2500")


@pytest.mark.parametrize(
    "unsafe",
    (
        {"open_position_count": 1},
        {"open_order_count": 1},
        {"trading_blocked": True},
        {"open_position_count": None},
    ),
)
def test_live_bootstrap_refuses_anything_but_a_flat_book(unsafe):
    """A bootstrap resets the high-water mark to current equity; with exposure
    on the books that would forgive a real drawdown."""

    kwargs = {
        "paper": False,
        "open_position_count": 0,
        "open_order_count": 0,
    }
    kwargs.update(unsafe)
    with pytest.raises(RiskStateBootstrapRefused):
        initialize_live_risk_state(
            "alpaca-main", "account-1", Decimal("25000"), NOW, **kwargs
        )


def test_paper_bootstrap_is_unconditional():
    state = initialize_live_risk_state(
        "alpaca-main",
        "account-1",
        Decimal("1000"),
        NOW,
        paper=True,
        open_position_count=3,
        open_order_count=2,
    )
    assert state.last_equity == Decimal("1000")


def test_confirmed_sell_updates_sleeve_and_starts_cooldown():
    state = initialize_risk_state(
        "alpaca-main", "account-1", Decimal("10000"), NOW
    )
    state = apply_confirmed_fill(
        state, _fill(OrderSide.BUY, Decimal("10"), Decimal("30"))
    )
    state = apply_confirmed_fill(
        state, _fill(OrderSide.SELL, Decimal("10"), Decimal("27"))
    )
    sleeve = state.sleeve("SQQQ")
    assert sleeve.quantity == 0
    assert sleeve.entry_basis is None
    assert sleeve.cooldown_until == NOW + timedelta(hours=24)
