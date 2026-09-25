"""Fix wave round 2, item 3 (the FW1 re-review's out-of-scope finding, ruled
in): refresh_positions issues its REST GET outside the lock and then rebinds
the option map under it. A stream sell-to-open fill applied between the two
was dropped from a map that read COMPLETE, and its lifecycle row, already
FILLED, no longer counted as pending: one refresh window (about 3 s, inside a
multi-put batch) of FW-lo-I3's over-admission.

Now option rows created or changed by confirmed fills AFTER the GET started
are reconciled at the rebind: the GET already saw them (keep the GET), or it
predates them (keep what the fills made); anything else, or a row of unknown
contract, marks the map incomplete (fail closed). EB has no option fills, so
its rebind is byte-identical (probe fw1/probe_refresh_rebind_eb.py)."""
import datetime as dtm
from decimal import Decimal

from live_orders import BrokerOrderEvent, ConfirmedFill, LifecycleState, OrderSide
from swing_alpaca_fakes import (
    FakeTradingClient,
    account,
    contract_row,
    make_adapter,
    option_position,
)

RTH = dtm.datetime(2026, 10, 5, 14, 45, tzinfo=dtm.timezone.utc)
PUT_A = "XYZ261009P00060000"
PUT_B = "QRS261009P00050000"
CONTRACTS = {PUT_A: contract_row(PUT_A, underlying="XYZ", strike=60.0,
                                 expiration="2026-10-09"),
             PUT_B: contract_row(PUT_B, underlying="QRS", strike=50.0,
                                 expiration="2026-10-09")}


class RacingClient(FakeTradingClient):
    """get_all_positions answers what it held when the GET started, and a
    stream fill lands while the answer is in flight."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.during_get = None
        self.answer_after = None

    def get_all_positions(self):
        answer = list(self.positions)
        hook, self.during_get = self.during_get, None
        if hook is not None:
            hook()
            if self.answer_after is not None:
                answer = list(self.answer_after)
        return answer


_seq = [0]


def _fill(adapter, symbol, delta, *, meta=True, strike="60", underlying="XYZ"):
    _seq[0] += 1
    side = OrderSide.SELL if delta < 0 else OrderSide.BUY
    event = BrokerOrderEvent(
        event_id=f"fill-{_seq[0]}", account_id="acct-1", instance_id="swing-paper",
        client_order_id=f"swingpap-{_seq[0]}", broker_order_id=f"b-{_seq[0]}",
        symbol=symbol, side=side, state=LifecycleState.FILLED,
        cumulative_quantity=Decimal(abs(delta)),
        cumulative_average_price=Decimal("1.20"), cumulative_fees=Decimal(0),
        occurred_at=RTH)
    extra = (dict(underlying=underlying, option_type="put", strike=Decimal(strike),
                  expiry="2026-10-09") if meta else {})
    adapter.apply_lifecycle_event(event, ConfirmedFill(
        event=event, incremental_quantity=Decimal(abs(delta)),
        incremental_price=Decimal("1.20"), incremental_fees=Decimal(0),
        position_delta=Decimal(delta), cash_delta=Decimal(-delta * 120),
        asset_class="us_option", contract_multiplier=100, **extra))


def _adapter(positions=()):
    client = RacingClient(account_row=account(cash="10000", equity="100000"),
                          positions=list(positions),
                          contracts_by_symbol=dict(CONTRACTS))
    adapter = make_adapter(client)
    adapter.refresh_positions()
    assert adapter._option_positions_complete is True
    return client, adapter


def test_a_put_that_fills_during_the_get_survives_the_rebind():
    """The reviewer's scenario: put A fills on the stream while a refresh's
    GET (taken before the fill) is in flight."""
    client, adapter = _adapter()
    client.during_get = lambda: _fill(adapter, PUT_A, -1)
    adapter.refresh_positions()
    row = adapter._option_positions[PUT_A]
    assert (row.qty, row.option_type, row.underlying, row.strike) == (
        -1, "put", "XYZ", 60.0)
    assert adapter._option_positions_complete is True


def test_the_next_put_is_then_gated_on_that_collateral():
    import threading
    from types import SimpleNamespace

    from live_orders import (
        InMemoryLifecycleBackend,
        LiveOrderService,
        OrderIntent,
        OrderLifecycleStore,
        OrderSource,
    )
    from swing_broker_harness import extract

    client, adapter = _adapter()
    client.during_get = lambda: _fill(adapter, PUT_A, -1)
    adapter.refresh_positions()
    adapter._account_equity = 100000.0
    state = {n: "healthy" for n in ("kill_switch", "cash", "positions",
                                    "persistence", "risk_state", "watchdog")}
    state.update({f"{n}_at": RTH for n in ("kill_switch", "cash", "positions",
                                           "persistence", "risk_state",
                                           "watchdog")})
    state["risk_snapshot_id"] = "risk-1"
    ns = extract(("_live_option_dependency_snapshot",
                  "_pending_sell_to_open_collateral",
                  "_open_order_idempotency_keys"),
                 assigns=("_live_option_quotes",),
                 namespace={"datetime": dtm, "_live_order_dependency_lock": threading.Lock(),
                            "_live_order_dependency_state": state,
                            "_live_stock_order_service": None,
                            "_live_risk_state": SimpleNamespace(
                                max_order_notional=Decimal("100000")),
                            "instance_id": "swing-paper", "MODE_LIVE": "live",
                            "mode": "live", "live_broker_type": "alpaca",
                            "live_brokerage_id": "acct-1"})
    service = LiveOrderService(
        account_id="acct-1", instance_id="swing-paper",
        snapshot_provider=lambda i: ns["_live_option_dependency_snapshot"](
            adapter, i, now_utc=RTH),
        transport=lambda **kw: SimpleNamespace(status="accepted", broker_order_id="b",
                                               id="b", filled_qty=0,
                                               filled_avg_price=None),
        lifecycle_store=OrderLifecycleStore(InMemoryLifecycleBackend()))
    ns["_live_stock_order_service"] = service
    ns["_live_option_quotes"][PUT_B] = {"price": Decimal("1.20"), "quote_at": RTH,
                                        "fetched_at": RTH}
    put_b = OrderIntent(
        account_id="acct-1", instance_id="swing-paper", source=OrderSource.STRATEGY,
        reason="wheel_sto_put", symbol=PUT_B, side=OrderSide.SELL,
        quantity=Decimal(1), reduce_only=False, decision_at=RTH, quote_at=RTH,
        risk_snapshot_id="risk-1", order_type="limit", limit_price=Decimal("1.14"),
        tif="day", asset_class="us_option", position_intent="sell_to_open",
        contract_multiplier=100, underlying="QRS", option_type="put",
        strike=Decimal("50"), expiry="2026-10-09")
    out = service.submit(put_b)
    # $10,120 - A's $6,000 < B's $5,000: refused on the real collateral.
    assert out.decision.reason_codes == ("option.collateral_insufficient",)


def test_a_mid_refresh_fill_of_unknown_contract_survives_but_marks_incomplete():
    client, adapter = _adapter()
    client.during_get = lambda: _fill(adapter, PUT_A, -1, meta=False)
    adapter.refresh_positions()
    assert adapter._option_positions[PUT_A].qty == -1
    assert adapter._option_positions_complete is False


def test_a_get_that_already_saw_the_fill_is_taken_as_is():
    client, adapter = _adapter()
    client.during_get = lambda: _fill(adapter, PUT_A, -1)
    client.answer_after = [option_position(PUT_A, qty="-1")]
    adapter.refresh_positions()
    assert adapter._option_positions[PUT_A].qty == -1
    assert adapter._option_positions_complete is True


def test_a_close_that_fills_during_the_get_is_not_resurrected():
    client, adapter = _adapter([option_position(PUT_A, qty="-1")])
    assert adapter._option_positions[PUT_A].qty == -1
    client.during_get = lambda: _fill(adapter, PUT_A, +1)       # buy_to_close
    adapter.refresh_positions()                                   # GET says -1
    assert PUT_A not in adapter._option_positions
    assert adapter._option_positions_complete is True


def test_a_mid_refresh_change_the_get_cannot_explain_fails_closed():
    client, adapter = _adapter([option_position(PUT_A, qty="-2")])
    client.during_get = lambda: _fill(adapter, PUT_A, -1)       # -2 -> -3
    client.answer_after = [option_position(PUT_A, qty="-5")]    # neither
    adapter.refresh_positions()
    assert adapter._option_positions_complete is False


def test_a_mid_refresh_fill_on_an_unreadable_get_row_fails_closed():
    client, adapter = _adapter()
    client.during_get = lambda: _fill(adapter, PUT_A, -1)
    client.answer_after = [option_position(PUT_A, qty="n/a")]
    adapter.refresh_positions()
    assert adapter._option_positions_complete is False


def test_without_a_mid_refresh_fill_the_rebind_is_as_before():
    client, adapter = _adapter([option_position(PUT_A, qty="-1")])
    _fill(adapter, PUT_B, -1, strike="50", underlying="QRS")    # before the GET
    client.positions = [option_position(PUT_A, qty="-1"),
                        option_position(PUT_B, qty="-1")]
    adapter.refresh_positions()
    assert {s: r.qty for s, r in adapter._option_positions.items()} == {
        PUT_A: -1, PUT_B: -1}
    assert adapter._option_positions_complete is True
    client.positions = [option_position(PUT_A, qty="-1")]       # B expired
    adapter.refresh_positions()
    assert set(adapter._option_positions) == {PUT_A}
