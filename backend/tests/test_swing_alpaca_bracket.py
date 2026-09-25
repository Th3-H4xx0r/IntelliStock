"""swing-port Task 3: Alpaca bracket orders, their legs, confirmed cancels,
and multi-leg rows that must not break reconciliation.

The two EB payloads below were produced by the PRE-change adapter on
2026-09-24. Never edit them to make a test pass."""
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from broker_adapters.errors import BrokerPreflightBlocked, FractionalNotAllowed
from live_orders import LifecycleState
from swing_alpaca_fakes import (
    FakeTradingClient,
    enum,
    make_adapter,
    order_row,
    request_json,
)

EB_MARKET_BUY = (
    '{"client_order_id": "alpacama-26078e450b92fad1c9e11db27d9425f41505f-0", '
    '"extended_hours": false, "qty": 12.34567891, "side": "buy", '
    '"symbol": "TQQQ", "time_in_force": "day", "type": "market"}')
EB_EXTENDED_LIMIT_SELL = (
    '{"client_order_id": "alpacama-f6fe0e571a021062fc2ad71af1e5194a440b9-0", '
    '"extended_hours": true, "limit_price": 329.42, "qty": 4.0, '
    '"side": "sell", "symbol": "GLD", "time_in_force": "day", '
    '"type": "limit"}')
BRACKET = (
    '{"client_order_id": "swingpap-bracket-0", "extended_hours": false, '
    '"order_class": "bracket", "qty": 5.0, "side": "buy", '
    '"stop_loss": {"stop_price": 188.0}, "symbol": "AAPL", '
    '"take_profit": {"limit_price": 218.0}, "time_in_force": "gtc", '
    '"type": "market"}')


class _ApiError(Exception):
    status_code = 403


def _eb_adapter():
    client = FakeTradingClient()
    return client, make_adapter(client, instance_id="alpaca-main")


def test_eb_requests_are_byte_identical():
    client, adapter = _eb_adapter()
    adapter.submit_order(
        "TQQQ", "buy", 12.34567891, None, "market", None, "day", False,
        "alpacama-26078e450b92fad1c9e11db27d9425f41505f-0")
    adapter.submit_order(
        "GLD", "sell", 4.0, None, "limit", 329.42, "day", True,
        "alpacama-f6fe0e571a021062fc2ad71af1e5194a440b9-0")
    assert [type(r).__name__ for r in client.submitted] == [
        "MarketOrderRequest", "LimitOrderRequest"]
    assert request_json(client.submitted[0]) == EB_MARKET_BUY
    assert request_json(client.submitted[1]) == EB_EXTENDED_LIMIT_SELL


def test_explicit_none_keywords_are_the_eb_request_too():
    client, adapter = _eb_adapter()
    adapter.submit_order(
        "TQQQ", "buy", 12.34567891, None, "market", None, "day", False,
        "alpacama-26078e450b92fad1c9e11db27d9425f41505f-0",
        order_class=None, take_profit=None, stop_loss=None,
        position_intent=None, asset_class=None)
    assert request_json(client.submitted[0]) == EB_MARKET_BUY


# --- Extra EB pins (L2 implementer, 2026-09-24) -------------------------------
# Every payload below was printed by the PRE-change adapter (HEAD 5d1e02d)
# before any Task 3 edit. They cover the remaining shapes EB and the legacy
# buy()/sell() shims send: notional market buy, regular-hours limit buy,
# market sell, extended-hours fractional floor, crypto GTC, and the
# fractional-rejection whole-share retry (both POSTs). Never edit them to make
# a test pass.
EB_EXTRA_PINS = [
    (("SPY", "buy", None, 250.0, "market", None, "day", False,
      "alpacama-notional-0"),
     "MarketOrderRequest",
     '{"client_order_id": "alpacama-notional-0", "extended_hours": false, '
     '"notional": 250.0, "side": "buy", "symbol": "SPY", '
     '"time_in_force": "day", "type": "market"}'),
    (("GDX", "buy", 3.21987654, None, "limit", 41.07, "day", False,
      "alpacama-rthlimit-0"),
     "LimitOrderRequest",
     '{"client_order_id": "alpacama-rthlimit-0", "extended_hours": false, '
     '"limit_price": 41.07, "qty": 3.21987654, "side": "buy", '
     '"symbol": "GDX", "time_in_force": "day", "type": "limit"}'),
    (("XLE", "sell", 7.5, None, "market", None, "day", False,
      "alpacama-mktsell-0"),
     "MarketOrderRequest",
     '{"client_order_id": "alpacama-mktsell-0", "extended_hours": false, '
     '"qty": 7.5, "side": "sell", "symbol": "XLE", '
     '"time_in_force": "day", "type": "market"}'),
    (("TQQQ", "sell", 4.7, None, "limit", 88.5, "day", True,
      "alpacama-extfloor-0"),
     "LimitOrderRequest",
     '{"client_order_id": "alpacama-extfloor-0", "extended_hours": true, '
     '"limit_price": 88.5, "qty": 4.0, "side": "sell", "symbol": "TQQQ", '
     '"time_in_force": "day", "type": "limit"}'),
    (("BTC/USD", "buy", 0.0123, None, "market", None, "gtc", False,
      "alpacama-crypto-0"),
     "MarketOrderRequest",
     '{"client_order_id": "alpacama-crypto-0", "extended_hours": false, '
     '"qty": 0.0123, "side": "buy", "symbol": "BTC/USD", '
     '"time_in_force": "gtc", "type": "market"}'),
]
EB_FRACTIONAL_RETRY = [
    '{"client_order_id": "alpacama-bbgi-0", "extended_hours": false, '
    '"qty": 2.5, "side": "buy", "symbol": "BBGI", "time_in_force": "day", '
    '"type": "market"}',
    '{"client_order_id": "alpacama-bbgi-0", "extended_hours": false, '
    '"qty": 2.0, "side": "buy", "symbol": "BBGI", "time_in_force": "day", '
    '"type": "market"}',
]


@pytest.mark.parametrize("args,kind,payload", EB_EXTRA_PINS)
def test_every_other_eb_request_shape_is_byte_identical(args, kind, payload):
    client, adapter = _eb_adapter()
    adapter.submit_order(*args)
    assert [type(r).__name__ for r in client.submitted] == [kind]
    assert request_json(client.submitted[0]) == payload


def test_the_eb_fractional_retry_sends_the_same_two_requests():
    client = FakeTradingClient(submit_error=_ApiError(
        'asset "BBGI" is not fractionable 40310000'))
    adapter = make_adapter(client, instance_id="alpaca-main")
    with pytest.raises(FractionalNotAllowed):
        adapter.submit_order("BBGI", "buy", 2.5, None, "market", None, "day",
                             False, "alpacama-bbgi-0")
    assert [request_json(r) for r in client.submitted] == EB_FRACTIONAL_RETRY


def test_an_eb_submit_returns_the_same_legacy_order_ref_and_wal_row():
    client, adapter = _eb_adapter()
    cid = "alpacama-26078e450b92fad1c9e11db27d9425f41505f-0"
    ref = adapter.submit_order("TQQQ", "buy", 12.34567891, None, "market",
                               None, "day", False, cid)
    assert (ref.broker_order_id, ref.client_order_id, ref.symbol, ref.side,
            ref.qty, ref.status, ref.filled_qty, ref.filled_avg_price,
            ref.submitted_at_utc) == (
        "broker-1", cid, "TQQQ", "buy", 12.34567891, "accepted", 0.0, None,
        datetime(2026, 10, 5, 13, 15, tzinfo=timezone.utc))
    row = adapter._wal.get(cid)
    assert (row.symbol, row.side, row.qty, row.notional, row.state,
            row.broker_order_id) == (
        "TQQQ", "buy", 12.34567891, None, "submitted", "broker-1")


#: (event, side, filled_qty, filled_avg_price) -> the event the PRE-change
#: _normalized_trade_update produced (event_id, state, side, symbol, qty, avg).
EB_STREAM_PINS = [
    (("fill", "buy", "5", "88.12"),
     ("38db23f78a7ec9e4c25e1bfaeadf0ba0ae39224e0e08c028b71ded3364ff8ba2",
      "filled", "buy", "TQQQ", "5", "88.12")),
    (("partial_fill", "sell", "2", "90"),
     ("ab4cc8ad9676ae244e20e158b417274f67c6809758220b51dcf255a908a930c6",
      "partial", "sell", "TQQQ", "2", "90")),
    (("new", "buy", "0", None),
     ("699cf5f522506e28fb313008e4cbff568bea212813558860192cbe1afe2c09b3",
      "acknowledged", "buy", "TQQQ", "0", "None")),
    (("canceled", "sell", "0", None),
     ("1d71ab33e4c076bbba0f4e58cae57b38eb397e1c1c7482835ce65510c56eb6b9",
      "canceled", "sell", "TQQQ", "0", "None")),
    (("done_for_day", "buy", "1", "3"),
     ("b7b0f9b498bc08b5394bf61cb849e37d88f5ff8d4a1dc61ba31525619cba7b04",
      "expired", "buy", "TQQQ", "1", "3")),
]


@pytest.mark.parametrize("given,expected", EB_STREAM_PINS)
def test_eb_stream_events_are_byte_identical(given, expected):
    from swing_alpaca_fakes import T0

    kind, side, filled, average = given
    client, adapter = _eb_adapter()
    adapter._order_event_account_id = "acct-1"
    event = adapter._normalized_trade_update(SimpleNamespace(
        event=kind, timestamp=T0, message="m", order=order_row(
            id="b-" + kind, client_order_id="alpacama-" + kind,
            symbol="tqqq", side=enum(side), filled_qty=filled,
            filled_avg_price=average)))
    assert (event.event_id, event.state.value, event.side.value, event.symbol,
            str(event.cumulative_quantity),
            str(event.cumulative_average_price)) == expected


def test_eb_reconciliation_orders_are_unchanged():
    from decimal import Decimal

    from swing_alpaca_fakes import T0

    eb = order_row(id="eb-1", client_order_id="alpacama-x-0", symbol="TQQQ",
                   status=enum("filled"), filled_qty="5",
                   filled_avg_price="88")
    eb2 = order_row(id="eb-2", client_order_id="alpacama-y-0", symbol="GLD",
                    side=enum("sell"), status=enum("new"), filled_qty="0")
    client = FakeTradingClient(orders=[eb, eb2])
    adapter = make_adapter(client, instance_id="alpaca-main")
    snap = adapter.capture_reconciliation_snapshot(account_id="acct-1")
    assert (snap.broker_available, snap.orders_complete) == (True, True)
    assert [(o.client_order_id, o.broker_order_id, o.symbol, o.side.value,
             o.requested_quantity, o.status, o.cumulative_quantity,
             o.cumulative_average_price, o.cumulative_fees, o.updated_at)
            for o in snap.orders] == [
        ("alpacama-x-0", "eb-1", "TQQQ", "buy", Decimal("5"), "filled",
         Decimal("5"), Decimal("88"), Decimal("0"), T0),
        ("alpacama-y-0", "eb-2", "GLD", "sell", Decimal("5"), "new",
         Decimal("0"), None, Decimal("0"), T0),
    ]


def test_a_bracket_is_a_whole_share_gtc_market_bracket():
    client = FakeTradingClient()
    adapter = make_adapter(client)
    ref = adapter.submit_order(
        "AAPL", "buy", 5.0, None, "market", None, "gtc", False,
        "swingpap-bracket-0", order_class="bracket", take_profit=218.0,
        stop_loss=188.0)
    assert request_json(client.submitted[0]) == BRACKET
    assert ref.order_class == "bracket"
    assert adapter._wal.get("swingpap-bracket-0") is not None


@pytest.mark.parametrize("args,kwargs", [
    (("AAPL", "buy", 5.5, None, "market", None, "gtc", False), {}),
    (("AAPL", "buy", None, 1000.0, "market", None, "gtc", False), {}),
    (("AAPL", "buy", 5.0, None, "limit", 200.0, "gtc", False), {}),
    (("AAPL", "buy", 5.0, None, "market", None, "gtc", True), {}),
    (("AAPL", "sell", 5.0, None, "market", None, "gtc", False), {}),
    (("AAPL", "buy", 5.0, None, "market", None, "ioc", False), {}),
    (("AAPL", "buy", 5.0, None, "market", None, "gtc", False),
     {"take_profit": None}),
    (("AAPL", "buy", 5.0, None, "market", None, "gtc", False),
     {"stop_loss": 220.0}),
])
def test_a_malformed_bracket_is_refused_before_anything_is_recorded(args, kwargs):
    client = FakeTradingClient()
    adapter = make_adapter(client)
    legs = {"take_profit": 218.0, "stop_loss": 188.0}
    legs.update(kwargs)
    with pytest.raises(BrokerPreflightBlocked):
        adapter.submit_order(*args, "swingpap-bad-0", order_class="bracket",
                             **legs)
    assert client.submitted == []
    assert adapter._wal.get("swingpap-bad-0") is None


def test_a_bracket_rejection_never_takes_the_fractional_retry():
    client = FakeTradingClient(submit_error=_ApiError(
        'asset "AAPL" is not fractionable 40310000'))
    adapter = make_adapter(client)
    with pytest.raises(FractionalNotAllowed):
        adapter.submit_order(
            "AAPL", "buy", 5.0, None, "market", None, "gtc", False,
            "swingpap-bracket-0", order_class="bracket", take_profit=218.0,
            stop_loss=188.0)
    assert len(client.submitted) == 1


def _bracket_with_legs():
    tp = order_row(id="leg-tp", client_order_id="7d1e-tp", side=enum("sell"),
                   status=enum("new"), order_class=enum("bracket"),
                   type=enum("limit"), limit_price="218")
    sl = order_row(id="leg-sl", client_order_id="7d1e-sl", side=enum("sell"),
                   status=enum("held"), order_class=enum("bracket"),
                   type=enum("stop"), stop_price="188")
    parent = order_row(id="parent-1", client_order_id="swingpap-bracket-0",
                       status=enum("filled"), filled_qty="5",
                       filled_avg_price="200", order_class=enum("bracket"),
                       legs=[tp, sl])
    return parent, tp, sl


def test_get_order_with_legs_reads_nested_and_classifies_each_leg():
    parent, tp, sl = _bracket_with_legs()
    client = FakeTradingClient(orders=[parent])
    adapter = make_adapter(client)
    ref = adapter.get_order_with_legs("parent-1")
    order_id, request = client.by_id_requests[0]
    assert order_id == "parent-1" and request.nested is True
    assert ref.order_class == "bracket" and ref.filled_qty == 5.0
    assert [(leg.client_order_id, leg.order_type, leg.status) for leg in ref.legs] \
        == [("7d1e-tp", "limit", "new"), ("7d1e-sl", "stop", "held")]
    assert ref.legs[0].limit_price == 218.0
    assert ref.legs[1].stop_price == 188.0


def test_an_eb_order_ref_reads_as_a_simple_order_with_no_legs():
    client, adapter = _eb_adapter()
    ref = adapter._to_orderref(order_row())
    assert ref.order_class == "simple" and ref.legs == ()
    assert ref.position_intent is None and ref.order_type == "market"


def test_order_refs_carry_alpacas_asset_class_so_halts_classify_correctly():
    """L1 ruling (F1): is_risk_reducing_order trusts position_intent only on a
    us_option order, so _to_orderref must copy Alpaca's asset_class. Without
    it an option buy-to-close would be cancelled on halt; with it an EB stock
    sell that Alpaca tags sell_to_close is still cancelled."""
    from broker_adapters.base import is_risk_reducing_order

    client, adapter = _eb_adapter()
    option_btc = adapter._to_orderref(order_row(
        id="opt-btc", symbol="APH261002P00130000", side=enum("buy"),
        asset_class=enum("us_option"), position_intent=enum("buy_to_close")))
    eb_sell = adapter._to_orderref(order_row(
        id="eb-sell", symbol="TQQQ", side=enum("sell"),
        asset_class=enum("us_equity"), position_intent=enum("sell_to_close")))
    assert (option_btc.asset_class, option_btc.position_intent) == (
        "us_option", "buy_to_close")
    assert (eb_sell.asset_class, eb_sell.position_intent) == (
        "us_equity", "sell_to_close")
    assert is_risk_reducing_order(option_btc) is True
    assert is_risk_reducing_order(eb_sell) is False


def test_real_alpaca_py_order_models_read_through_to_orderref():
    """The fakes use SimpleNamespace enums; this runs the real alpaca-py
    0.43.5 pydantic models (no network) through _to_orderref."""
    from uuid import uuid4

    from alpaca.trading.models import Order

    def _order(**changes):
        values = dict(
            id=uuid4(), client_order_id="cid", created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            submitted_at=datetime.now(timezone.utc), symbol="AAPL",
            asset_class="us_equity", qty="5", filled_qty="0",
            order_class="simple", type="market", side="buy",
            time_in_force="gtc", status="accepted", extended_hours=False)
        values.update(changes)
        return Order(**values)

    tp = _order(side="sell", order_class="bracket", type="limit",
                limit_price="218", status="new", client_order_id="tp")
    sl = _order(side="sell", order_class="bracket", type="stop",
                stop_price="188", status="held", client_order_id="sl")
    parent = _order(order_class="bracket", status="filled", filled_qty="5",
                    filled_avg_price="200", legs=[tp, sl])
    client, adapter = _eb_adapter()
    ref = adapter._to_orderref(parent)
    assert (ref.order_class, ref.asset_class, ref.order_type, ref.side) == (
        "bracket", "us_equity", "market", "buy")
    assert [(leg.client_order_id, leg.order_type, leg.status,
             leg.limit_price, leg.stop_price) for leg in ref.legs] == [
        ("tp", "limit", "new", 218.0, None),
        ("sl", "stop", "held", None, 188.0)]
    option = adapter._to_orderref(_order(
        symbol="APH261002P00130000", asset_class="us_option",
        position_intent="buy_to_close", time_in_force="day"))
    assert (option.asset_class, option.position_intent) == (
        "us_option", "buy_to_close")


def _clock(step=0.5):
    now = [0.0]

    def clock():
        value = now[0]
        now[0] += step
        return value

    return clock


def _leg_client():
    parent, tp, sl = _bracket_with_legs()
    return FakeTradingClient(orders=[parent, tp, sl])


def test_cancel_is_confirmed_when_every_leg_reports_canceled():
    client = _leg_client()
    client.status_script = {"leg-tp": [("canceled", "0")],
                            "leg-sl": [("held", "0"), ("canceled", "0")]}
    adapter = make_adapter(client)
    assert adapter.cancel_orders_confirmed(
        ["leg-tp", "leg-sl"], timeout_s=5.0, poll_interval_s=0.0,
        sleep=lambda _s: None, clock=_clock()) is True
    assert client.cancelled == ["leg-tp", "leg-sl"]


def test_an_unconfirmed_cancel_times_out_false():
    client = _leg_client()
    client.status_script = {"leg-sl": [("held", "0")]}
    adapter = make_adapter(client)
    assert adapter.cancel_orders_confirmed(
        ["leg-sl"], timeout_s=1.0, poll_interval_s=0.0,
        sleep=lambda _s: None, clock=_clock()) is False


def test_a_leg_that_fills_during_the_cancel_is_not_confirmed():
    """Review Focus 3: the position changed under the caller, so a sell sized
    off the old position must wait for the next tick's broker truth."""
    client = _leg_client()
    client.status_script = {"leg-tp": [("filled", "5")],
                            "leg-sl": [("canceled", "0")]}
    adapter = make_adapter(client)
    assert adapter.cancel_orders_confirmed(
        ["leg-tp", "leg-sl"], timeout_s=5.0, poll_interval_s=0.0,
        sleep=lambda _s: None, clock=_clock()) is False


def test_nothing_to_cancel_is_confirmed_without_a_call():
    client = _leg_client()
    adapter = make_adapter(client)
    assert adapter.cancel_orders_confirmed([], timeout_s=1.0) is True
    assert client.cancelled == [] and client.by_id_requests == []


def test_list_closed_orders_filters_by_symbol_and_date():
    parent, _tp, _sl = _bracket_with_legs()
    client = FakeTradingClient(orders=[parent])
    adapter = make_adapter(client)
    refs = adapter.list_closed_orders(["aapl"], "2026-09-01")
    request = client.order_requests[-1]
    assert request.symbols == ["AAPL"]
    assert str(getattr(request.status, "value", request.status)) == "closed"
    assert request.after == datetime(2026, 9, 1, tzinfo=timezone.utc)
    assert [ref.broker_order_id for ref in refs] == ["parent-1"]


def test_the_eb_order_walks_send_no_symbols_filter():
    """_walk_orders gains an optional symbols filter; EB's reconcile walks
    never pass it, so their requests carry no symbols key."""
    client, adapter = _eb_adapter()
    adapter.capture_reconciliation_snapshot(account_id="acct-1")
    assert client.order_requests
    assert all("symbols" not in r.to_request_fields()
               for r in client.order_requests)


def test_a_multi_leg_order_does_not_make_the_account_unavailable():
    mleg = order_row(id="mleg-1", client_order_id="ui-mleg", symbol=None,
                     side=None, order_class=enum("mleg"), status=enum("filled"))
    sideless = order_row(id="opt-1", client_order_id="ui-opt",
                         symbol="APH261002P00130000", side=None,
                         position_intent=enum("sell_to_open"),
                         order_class=enum("simple"), status=enum("filled"),
                         filled_qty="1", filled_avg_price="1.2")
    eb = order_row(id="eb-1", client_order_id="alpacama-x-0", symbol="TQQQ",
                   status=enum("filled"), filled_qty="5",
                   filled_avg_price="88")
    client = FakeTradingClient(orders=[mleg, sideless, eb])
    adapter = make_adapter(client)
    snap = adapter.capture_reconciliation_snapshot(account_id="acct-1")
    assert snap.broker_available is True
    assert sorted(o.broker_order_id for o in snap.orders) == ["eb-1", "opt-1"]
    assert {o.broker_order_id: o.side.value for o in snap.orders}["opt-1"] == "sell"


def test_stream_events_held_and_pending_cancel_are_acknowledged():
    client, adapter = _eb_adapter()
    adapter._order_event_account_id = "acct-1"
    for kind in ("held", "pending_cancel"):
        event = adapter._normalized_trade_update(SimpleNamespace(
            event=kind, order=order_row(side=enum("sell")), message=""))
        assert event.state is LifecycleState.ACKNOWLEDGED


def test_a_sideless_stream_event_is_dropped_not_raised():
    client, adapter = _eb_adapter()
    adapter._order_event_account_id = "acct-1"
    event = adapter._normalized_trade_update(SimpleNamespace(
        event="fill", order=order_row(side=None, order_class=enum("mleg")),
        message=""))
    assert event is None
