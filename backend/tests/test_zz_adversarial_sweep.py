"""ADVERSARIAL SWEEP (not for commit).

Each test here is written to FAIL if the claimed fix holds, and to PASS by
demonstrating the defect. Read the docstrings, not the assertions.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from broker_adapters._wal import InMemoryStore, LiveOrderWAL
from broker_adapters.alpaca import AlpacaAdapter
from broker_adapters.errors import BrokerError, BrokerPreflightBlocked
from live_orders import (
    InMemoryLifecycleBackend,
    LifecycleState,
    LiveOrderService,
    OrderLifecycleStore,
    OrderSide,
)
from live_order_task8_helpers import NOW, intent, snapshot


def _account(**changes):
    values = {
        "cash": "10000",
        "buying_power": "10000",
        "daytrading_buying_power": "10000",
        "equity": "10000",
        "last_equity": "10000",
        "pattern_day_trader": False,
        "daytrade_count": 0,
        "account_blocked": False,
        "trading_blocked": False,
    }
    values.update(changes)
    return SimpleNamespace(**values)


class _Client:
    def __init__(self, *, submit_exc=None, lookup_exc=None, account=None):
        self._account = account or _account()
        self._submit_exc = submit_exc
        self._lookup_exc = lookup_exc or Exception("404 not found")
        self.submitted = []

    def get_account(self):
        return self._account

    def get_all_positions(self):
        return []

    def get_order_by_client_id(self, cid):
        raise self._lookup_exc

    def get_orders(self, filter=None):
        return []

    def submit_order(self, order_data=None):
        self.submitted.append(order_data)
        if self._submit_exc is not None:
            raise self._submit_exc
        return SimpleNamespace(
            id="broker-1",
            client_order_id=getattr(order_data, "client_order_id", "cid"),
            symbol=getattr(order_data, "symbol", "AAPL"),
            side="buy",
            qty=getattr(order_data, "qty", 1),
            status="accepted",
            filled_qty=0,
            filled_avg_price=None,
            submitted_at=datetime.now(timezone.utc),
        )


def _adapter(client):
    return AlpacaAdapter(
        api_key="k",
        api_secret="s",
        paper=True,
        instance_id="instance-1",
        wal=LiveOrderWAL(InMemoryStore()),
        initial_value=10000,
        seed_trades_from_broker=False,
        _test_client=client,
    )


# ---------------------------------------------------------------- FINDING 1
def test_a_client_side_read_timeout_is_labelled_a_definite_rejection():
    """alpaca.py:1056 stamps broker_definitive_rejection=True on ANY submit
    exception whose follow-up lookup 404s -- including a client-side socket
    timeout, where the POST may still be executing at Alpaca. The comment
    calls that "positive evidence that Alpaca holds no such order"; it is
    not, because a timed-out POST has no server-side outcome yet.
    """

    import requests

    adapter = _adapter(
        _Client(submit_exc=requests.exceptions.ReadTimeout("Read timed out."))
    )
    with pytest.raises(BrokerError) as excinfo:
        adapter.submit_order(
            "AAPL", "buy", qty=5.0, notional=None, order_type="market",
            limit_price=None, tif="day", extended_hours=False,
            client_order_id="cid-timeout",
        )
    # The order WAS posted to the wire.
    assert len(adapter._client.submitted) == 1
    # ...and the ambiguity is reported as a definite refusal.
    assert getattr(excinfo.value, "broker_definitive_rejection", False) is True


def test_b_a_misclassified_ambiguity_produces_a_second_real_buy():
    """Feed the service the error shape produced above. It records terminal
    REJECTED, releases the reservation, and the next emission of the SAME
    decision escalates retry_ordinal into a brand-new client order id -- a
    second POST for an order the broker may already hold.
    """

    store = OrderLifecycleStore(InMemoryLifecycleBackend())
    posted: list[str] = []

    class _Ambiguous(BrokerError):
        broker_definitive_rejection = True  # what alpaca.py stamps on

    def transport(**kwargs):
        posted.append(kwargs["client_order_id"])
        if len(posted) == 1:
            raise _Ambiguous("read timeout; lookup 404'd")
        return SimpleNamespace(
            id="broker-2", status="accepted", filled_qty=0,
            filled_avg_price=None,
        )

    order = intent()
    service = LiveOrderService(
        account_id=order.account_id,
        instance_id=order.instance_id,
        snapshot_provider=lambda current: snapshot(current),
        transport=transport,
        lookup_by_client_id=lambda _cid: None,
        lifecycle_store=store,
    )

    first = service.submit(order)
    assert first.accepted is False
    assert store.require(order.idempotency_key).state is LifecycleState.REJECTED
    assert service.reservation_for(order.idempotency_key) is None

    # Same logical decision, re-emitted on the next tick.
    second = service.submit(intent())
    assert second.accepted is True
    assert len(posted) == 2
    assert posted[0] != posted[1]  # different cid => Alpaca cannot dedupe


# ---------------------------------------------------------------- FINDING 2
def test_c_same_day_reentry_after_a_filled_buy_is_permanently_denied():
    """The date-keyed buy identity collapses two genuinely different intents.
    Buy AAPL in the morning, exit at lunch, and the afternoon re-entry is a
    duplicate of the morning order for the rest of the session.
    """

    store = OrderLifecycleStore(InMemoryLifecycleBackend())
    posted: list[str] = []

    def transport(**kwargs):
        posted.append(kwargs["client_order_id"])
        return SimpleNamespace(
            id=f"broker-{len(posted)}", status="filled",
            filled_qty=float(kwargs["qty"]), filled_avg_price=100.0,
        )

    morning = intent(decision_at=NOW, quote_at=NOW)
    service = LiveOrderService(
        account_id=morning.account_id,
        instance_id=morning.instance_id,
        snapshot_provider=lambda current: snapshot(current, quote_at=current.quote_at),
        transport=transport,
        lookup_by_client_id=lambda _cid: None,
        lifecycle_store=store,
    )
    assert service.submit(morning).accepted is True
    assert store.require(morning.idempotency_key).state is LifecycleState.FILLED

    # 4 hours later, same session date: a NEW decision to buy the same name.
    later = NOW + timedelta(hours=4)
    afternoon = intent(
        decision_at=later, quote_at=later, quantity=Decimal("7"),
        reason="re-entry after a completed round trip",
    )
    assert afternoon.idempotency_key == morning.idempotency_key
    result = service.submit(afternoon)
    assert result.accepted is False
    assert result.decision.reason_codes == ("idempotency.terminal_requires_retry",)
    assert len(posted) == 1  # the re-entry never reached the broker


# ---------------------------------------------------------------- FINDING 3
def test_d_three_rejections_kill_the_symbol_for_the_whole_session():
    """Buys are keyed on the trading DATE, and retry escalation is bounded at
    max_terminal_retries. Three definite rejections (e.g. three sub-1-share
    extended-hours attempts at 09:00 ET) burn every ordinal, so the same buy
    can no longer be placed at 10:00 ET when it would have been legal.
    """

    store = OrderLifecycleStore(InMemoryLifecycleBackend())
    attempts: list[str] = []

    def transport(**kwargs):
        attempts.append(kwargs["client_order_id"])
        raise BrokerPreflightBlocked("extended-hours order floors to 0 shares")

    order = intent()
    service = LiveOrderService(
        account_id=order.account_id,
        instance_id=order.instance_id,
        snapshot_provider=lambda current: snapshot(current),
        transport=transport,
        lookup_by_client_id=lambda _cid: None,
        lifecycle_store=store,
    )
    for _ in range(3):
        service.submit(intent())
    assert len(attempts) == 3

    # Regular session opens; the order is now perfectly legal.
    rth = NOW + timedelta(hours=2)
    legal = intent(decision_at=rth, quote_at=rth)
    outcome = service.submit(legal)
    assert outcome.accepted is False
    assert outcome.decision.reason_codes == ("idempotency.terminal_retry_exhausted",)
    assert len(attempts) == 3  # never even attempted


# ---------------------------------------------------------------- FINDING 4
def test_e_extended_hours_floor_silently_truncates_a_protective_exit():
    """submit_order floors a fractional extended-hours qty to whole shares and
    posts the truncated order. A full exit of 10.6 shares becomes a sale of
    10; the caller is told the exit succeeded and 0.6 shares stay at risk.
    """

    client = _Client()
    adapter = _adapter(client)
    adapter.submit_order(
        "AAPL", "sell", qty=10.6, notional=None, order_type="limit",
        limit_price=99.0, tif="day", extended_hours=True,
        client_order_id="cid-exit",
    )
    assert float(client.submitted[0].qty) == 10.0  # 0.6 shares never sold


# ---------------------------------------------------------------- FINDING 5
def test_g_a_zero_share_extended_hours_exit_raises_a_definitive_rejection():
    """When the floor leaves nothing, submit_order raises
    BrokerPreflightBlocked, whose broker_definitive_rejection is True. For a
    SELL that is a terminal REJECTED on a position that still exists.
    """

    client = _Client()
    adapter = _adapter(client)
    with pytest.raises(BrokerPreflightBlocked) as excinfo:
        adapter.submit_order(
            "AAPL", "sell", qty=0.42, notional=None, order_type="limit",
            limit_price=99.0, tif="day", extended_hours=True,
            client_order_id="cid-tiny-exit",
        )
    assert getattr(excinfo.value, "broker_definitive_rejection", False) is True
    assert client.submitted == []


# ---------------------------------------------------------------- FINDING 6
def test_h_one_broker_order_without_a_lifecycle_row_blocks_every_exit():
    """`unknown_broker_order` is raised for ANY broker order the lifecycle
    store does not know -- a manual UI order, an order from the legacy
    adapter.buy()/WAL path, anything placed before the service existed.
    healthy goes False, broker.py maps that to positions=unhealthy, and the
    gate lists `positions` as REQUIRED even for reduce_only. So a stray order
    blocks risk exits for as long as it stays in the 30-day history window --
    exactly the failure the UNKNOWN-lifecycle fix says it eliminated.
    """

    from live_orders import (
        AuthoritativeBrokerSnapshot,
        BrokerOrderSnapshot,
        Health,
        StartupReconciler,
        UnifiedOrderGate,
    )

    store = OrderLifecycleStore(InMemoryLifecycleBackend())
    order = intent()
    legacy = BrokerOrderSnapshot(
        client_order_id="alpacama-legacy-wal-cid",   # made by make_client_order_id
        broker_order_id="broker-legacy",
        symbol="AAPL",
        side=OrderSide.BUY,
        requested_quantity=Decimal("3"),
        status="filled",
        cumulative_quantity=Decimal("3"),
        cumulative_average_price=Decimal("100"),
        cumulative_fees=Decimal("0"),
        updated_at=datetime.now(timezone.utc) - timedelta(days=9),
    )
    snap = AuthoritativeBrokerSnapshot(
        account_id=order.account_id,
        instance_id=order.instance_id,
        observed_at=datetime.now(timezone.utc),
        positions=(),
        orders=(legacy,),
        broker_available=True,
        positions_stable=True,
        orders_complete=True,
    )
    result = StartupReconciler(
        lifecycle_store=store, event_applier=lambda event: None
    ).reconcile(snap)
    assert result.healthy is False
    assert result.issues == ("unknown_broker_order:broker-legacy",)

    # broker.py:_reconcile_alpaca_ownership -> positions="unhealthy".
    exit_intent = intent(
        side=OrderSide.SELL, reduce_only=True, quantity=Decimal("4"),
        reason="circuit_breaker_sell",
    )
    decision = UnifiedOrderGate().evaluate(
        exit_intent,
        snapshot(exit_intent, positions=Health.UNHEALTHY),
    )
    assert decision.allowed is False
    assert "dependency.positions.unhealthy" in decision.reason_codes


# ---------------------------------------------------------------- FINDING 7
def test_i_the_funded_bootstrap_can_never_fire_because_the_method_does_not_exist():
    """broker.py:_broker_count("get_all_positions") -- AlpacaAdapter has no
    such method (that is the alpaca-py CLIENT's name; the adapter exposes
    refresh_positions/get_positions). getattr returns None, callable(None) is
    False, so the count is -1 and the bootstrap refuses every time. Fix #2 is
    inert: a funded account still boots sell-only.
    """

    from live_risk_state import (
        RiskStateBootstrapRefused,
        initialize_live_risk_state,
    )

    adapter = _adapter(_Client())
    assert getattr(adapter, "get_all_positions", None) is None

    # Verbatim reproduction of the helper in broker.py.
    def _broker_count(fn_name):
        try:
            fn = getattr(adapter, fn_name, None)
            return len(fn() or ()) if callable(fn) else -1
        except Exception:
            return -1

    assert _broker_count("get_all_positions") == -1

    with pytest.raises(RiskStateBootstrapRefused) as excinfo:
        initialize_live_risk_state(
            "instance-1", "acct-1", 10000,
            datetime.now(timezone.utc), paper=False,
            open_position_count=_broker_count("get_all_positions"),
            open_order_count=_broker_count("list_open_orders"),
            trading_blocked=False,
        )
    assert "open position count is unknown" in str(excinfo.value)


def test_j_the_open_order_count_is_fail_open_not_minus_one():
    """The other arm has the opposite defect. list_open_orders swallows every
    exception and returns [], so an unreadable orders endpoint reports "no
    working orders" instead of -1 -- the exact assumption the docstring says
    it refuses to make.
    """

    class _Broken(_Client):
        def get_orders(self, filter=None):
            raise RuntimeError("503 service unavailable")

    adapter = _adapter(_Broken())
    assert adapter.list_open_orders() == []   # not an exception, not -1

    def _broker_count(fn_name):
        try:
            fn = getattr(adapter, fn_name, None)
            return len(fn() or ()) if callable(fn) else -1
        except Exception:
            return -1

    assert _broker_count("list_open_orders") == 0  # "flat", on a dead endpoint
