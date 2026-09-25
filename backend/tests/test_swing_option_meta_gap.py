"""FW-lo-I3 (swing-port final review, live-orders slice Important 3), with
the coordinator's follow-up ruling.

A stream fill of a NEW sell-to-open adds a short row to the adapter's option
map. The contract's meta (type, underlying, strike) is only looked up by
refresh_positions and the activities poller, so the row was born untyped
(option_type "", strike 0) while the map still read complete. Its collateral
then counted nowhere, and the next put of the batch was admitted uncovered:
$11,000 committed against $10,120 in the reviewer's probe.

Follow-up ruling: a fill on an option order WE placed carries its intent's
contract meta (ConfirmedFill.underlying / option_type / strike / expiry), so
the new row is born typed and the map stays complete: the next put of the
batch is gated on the real collateral. A row of unknown origin (a fill with
no intent meta) still marks the map INCOMPLETE, which fails closed for any
new sell-to-open (collateral and the 25% cap) and for plan B's duplicate-put
check until a refresh fills the meta in. The snapshot also treats any short
row with an unknown type, underlying or strike as unknown. EB's account holds
no options and its fills carry no meta; none of this runs there."""
import datetime as dtm
import threading
from decimal import Decimal
from types import SimpleNamespace

from broker_adapters.base import OptionPositionDTO
from live_orders import (
    BrokerOrderEvent,
    ConfirmedFill,
    InMemoryLifecycleBackend,
    LifecycleState,
    LiveOrderService,
    OrderIntent,
    OrderLifecycleStore,
    OrderSide,
    OrderSource,
)
from swing_alpaca_fakes import FakeTradingClient, account, contract_row, make_adapter
from swing_broker_harness import extract

RTH = dtm.datetime(2026, 10, 5, 14, 45, tzinfo=dtm.timezone.utc)   # Mon 10:45 ET
PUT_A = "XYZ261009P00060000"
PUT_B = "QRS261009P00050000"


def _world(contracts=None, *, cash="10000", equity="100000"):
    client = FakeTradingClient(account_row=account(cash=cash, equity=equity),
                               contracts_by_symbol=dict(contracts or {}))
    adapter = make_adapter(client)
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
                 namespace={"datetime": dtm,
                            "_live_order_dependency_lock": threading.Lock(),
                            "_live_order_dependency_state": state,
                            "_live_stock_order_service": None,
                            "_live_risk_state": SimpleNamespace(
                                max_order_notional=Decimal("100000")),
                            "instance_id": "swing-paper", "MODE_LIVE": "live",
                            "mode": "live", "live_broker_type": "alpaca",
                            "live_brokerage_id": "acct-1"})
    sent = []

    def transport(**kw):
        sent.append(kw)
        return SimpleNamespace(status="accepted", broker_order_id=f"b-{len(sent)}",
                               id=f"b-{len(sent)}", filled_qty=0,
                               filled_avg_price=None, submitted_at_utc=RTH)

    service = LiveOrderService(
        account_id="acct-1", instance_id="swing-paper",
        snapshot_provider=lambda i: ns["_live_option_dependency_snapshot"](
            adapter, i, now_utc=RTH),
        transport=transport, event_handler=adapter.apply_lifecycle_event,
        lifecycle_store=OrderLifecycleStore(InMemoryLifecycleBackend()))
    ns["_live_stock_order_service"] = service
    return adapter, service, ns, sent


def _put(ns, occ, underlying, strike, **changes):
    ns["_live_option_quotes"][occ] = {"price": Decimal("1.20"), "quote_at": RTH,
                                      "fetched_at": RTH}
    values = dict(account_id="acct-1", instance_id="swing-paper",
                  source=OrderSource.STRATEGY, reason="wheel_sto_put", symbol=occ,
                  side=OrderSide.SELL, quantity=Decimal(1), reduce_only=False,
                  decision_at=RTH, quote_at=RTH, risk_snapshot_id="risk-1",
                  order_type="limit", limit_price=Decimal("1.14"), tif="day",
                  asset_class="us_option", position_intent="sell_to_open",
                  contract_multiplier=100, underlying=underlying,
                  option_type="put", strike=Decimal(strike), expiry="2026-10-09")
    values.update(changes)
    return OrderIntent(**values)


def _fill(service, intent, broker_id="b-1"):
    return service.apply_broker_event(BrokerOrderEvent(
        event_id=f"fill-{intent.symbol}", account_id="acct-1",
        instance_id="swing-paper", client_order_id=intent.idempotency_key,
        broker_order_id=broker_id, symbol=intent.symbol, side=OrderSide.SELL,
        state=LifecycleState.FILLED, cumulative_quantity=Decimal(1),
        cumulative_average_price=Decimal("1.20"), cumulative_fees=Decimal(0),
        occurred_at=RTH))


def test_the_reviewers_probe_book_gates_the_second_put_on_real_collateral():
    """Put A (strike 60) fills on the stream before put B (strike 50) of the
    same batch is gated. A's row is born typed from A's intent, so B sees
    A's $6,000: $10,120 - $6,000 = $4,120 < $5,000, refused on the cash."""
    adapter, service, ns, sent = _world()
    assert adapter._option_positions_complete is True
    put_a = _put(ns, PUT_A, "XYZ", "60")
    assert service.submit(put_a).accepted
    assert _fill(service, put_a).applied
    assert adapter._cash == 10120.0
    row = adapter._option_positions[PUT_A]
    assert (row.qty, row.option_type, row.underlying, row.strike, row.expiry) == (
        -1, "put", "XYZ", 60.0, "2026-10-09")
    assert adapter._option_positions_complete is True
    put_b = _put(ns, PUT_B, "QRS", "50")
    out = service.submit(put_b)
    assert not out.decision.allowed
    assert out.decision.reason_codes == ("option.collateral_insufficient",)
    assert len(sent) == 1            # only put A reached the broker


def test_the_second_put_goes_out_when_the_cash_covers_both():
    adapter, service, ns, sent = _world(cash="20000")
    put_a = _put(ns, PUT_A, "XYZ", "60")
    assert service.submit(put_a).accepted and _fill(service, put_a).applied
    assert adapter._option_positions_complete is True
    # $20,120 - $6,000 = $14,120 covers B's $5,000.
    out = service.submit(_put(ns, PUT_B, "QRS", "50"))
    assert out.accepted, out.decision.reason_codes
    assert len(sent) == 2


def test_the_25_percent_cap_counts_the_stamped_row():
    """A second put on A's underlying sees A's collateral against the cap."""
    adapter, service, ns, _sent = _world(cash="100000", equity="30000")
    adapter._account_equity = 30000.0
    put_a = _put(ns, PUT_A, "XYZ", "60")
    assert service.submit(put_a).accepted and _fill(service, put_a).applied
    other = _put(ns, "XYZ261016P00020000", "XYZ", "20", expiry="2026-10-16")
    out = service.submit(other)
    # $6,000 + $2,000 > 25% of $30,000.
    assert out.decision.reason_codes == ("option.underlying_cap",)


def _unknown_origin_fill(adapter, symbol=PUT_A):
    """A fill applied with no intent meta: nothing says what contract it is."""
    event = BrokerOrderEvent(
        event_id="stray", account_id="acct-1", instance_id="swing-paper",
        client_order_id="ui-stray", broker_order_id="b-stray", symbol=symbol,
        side=OrderSide.SELL, state=LifecycleState.FILLED,
        cumulative_quantity=Decimal(1), cumulative_average_price=Decimal("1.20"),
        cumulative_fees=Decimal(0), occurred_at=RTH)
    adapter.apply_lifecycle_event(event, ConfirmedFill(
        event=event, incremental_quantity=Decimal(1),
        incremental_price=Decimal("1.20"), incremental_fees=Decimal(0),
        position_delta=Decimal(-1), cash_delta=Decimal("120"),
        asset_class="us_option", contract_multiplier=100))


def test_a_row_of_unknown_origin_still_marks_the_map_incomplete():
    adapter, service, ns, sent = _world()
    _unknown_origin_fill(adapter)
    assert adapter._option_positions[PUT_A].option_type == ""
    assert adapter._option_positions_complete is False
    out = service.submit(_put(ns, PUT_B, "QRS", "50"))
    assert not out.decision.allowed
    assert "option.collateral_unknown" in out.decision.reason_codes
    assert "option.underlying_cap_unknown" in out.decision.reason_codes
    assert sent == []


def test_a_refresh_that_fills_the_meta_in_restores_the_map():
    adapter, service, ns, _sent = _world(
        {PUT_A: contract_row(PUT_A, underlying="XYZ", strike=60.0,
                             expiration="2026-10-09")})
    _unknown_origin_fill(adapter)
    assert adapter._option_positions_complete is False
    from swing_alpaca_fakes import option_position
    adapter._client.positions = [option_position(PUT_A, qty="-1")]
    adapter.refresh_positions()
    assert adapter._option_positions_complete is True
    row = adapter._option_positions[PUT_A]
    assert (row.option_type, row.underlying, row.strike) == ("put", "XYZ", 60.0)
    out = service.submit(_put(ns, PUT_B, "QRS", "50"))
    assert "option.collateral_insufficient" in out.decision.reason_codes


def test_an_eb_stock_fill_carries_no_contract_meta():
    """The service stamps meta only from an us_option intent."""
    from live_order_task8_helpers import broker_ref, intent as equity_intent

    fills = []
    buy = equity_intent(account_id="brk-alpaca-main", instance_id="alpaca-main",
                        symbol="TQQQ", reason="eb_rebalance")
    svc = LiveOrderService(
        account_id="brk-alpaca-main", instance_id="alpaca-main",
        snapshot_provider=None, transport=None,
        lifecycle_store=OrderLifecycleStore(InMemoryLifecycleBackend()),
        confirmed_fill_handler=fills.append)
    svc.lifecycle_store.create_intent(buy)
    assert svc.apply_broker_event(BrokerOrderEvent(
        event_id="eb-fill", account_id="brk-alpaca-main", instance_id="alpaca-main",
        client_order_id=buy.idempotency_key, broker_order_id="b-eb",
        symbol="TQQQ", side=OrderSide.BUY, state=LifecycleState.FILLED,
        cumulative_quantity=buy.quantity, cumulative_average_price=Decimal("88"),
        cumulative_fees=Decimal(0), occurred_at=RTH)).applied
    (fill,) = fills
    assert (fill.asset_class, fill.underlying, fill.option_type, fill.strike,
            fill.expiry) == ("us_equity", None, None, None, None)


def test_a_fill_of_a_contract_whose_meta_is_cached_keeps_the_map_complete():
    adapter, service, ns, _sent = _world(
        {PUT_A: contract_row(PUT_A, underlying="XYZ", strike=60.0,
                             expiration="2026-10-09")})
    assert adapter.option_contract_meta(PUT_A) is not None
    put_a = _put(ns, PUT_A, "XYZ", "60")
    assert service.submit(put_a).accepted and _fill(service, put_a).applied
    assert adapter._option_positions_complete is True
    assert adapter._option_positions[PUT_A].option_type == "put"


def _snapshot_with(row, intent_kind="sell_to_open"):
    adapter, service, ns, _sent = _world()
    adapter._option_positions = {row.symbol: row}
    adapter._option_positions_complete = True    # the stale "complete" flag
    intent = _put(ns, PUT_B, "QRS", "50")
    return ns["_live_option_dependency_snapshot"](adapter, intent, now_utc=RTH)


def _row(**changes):
    values = dict(symbol=PUT_A, underlying="XYZ", option_type="put", strike=60.0,
                  expiry="2026-10-09", qty=-1, avg_entry_price=1.2,
                  current_price=None, market_value=None, unrealized_pl=None)
    values.update(changes)
    return OptionPositionDTO(**values)


def test_a_typed_short_row_counts_its_collateral():
    snap = _snapshot_with(_row())
    assert snap.open_short_put_collateral == Decimal("6000")


def test_a_short_row_with_unknown_meta_makes_the_collateral_unknown():
    for change in ({"option_type": ""}, {"underlying": ""}, {"strike": 0.0},
                   {"option_type": None}, {"strike": None}):
        snap = _snapshot_with(_row(**change))
        assert snap.open_short_put_collateral is None, change
        assert snap.underlying_put_collateral is None, change


def test_a_long_row_with_unknown_meta_does_not_block_a_put():
    """Only a SHORT row carries collateral; a long contract with no meta is
    not a cash obligation."""
    snap = _snapshot_with(_row(qty=1, option_type="", strike=0.0))
    assert snap.open_short_put_collateral == Decimal("0")
