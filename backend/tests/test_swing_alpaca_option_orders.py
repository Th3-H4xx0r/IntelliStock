"""swing-port Task 5: option orders, option positions (signed) and option
fills, kept apart from the long-only equity mirror."""
import json
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from broker_adapters.errors import (
    BrokerError,
    BrokerPreflightBlocked,
    FractionalNotAllowed,
    OptionsNotPermitted,
)
from live_orders import BrokerOrderEvent, ConfirmedFill, LifecycleState, OrderSide
from swing_alpaca_fakes import (
    T0,
    FakeOptionsTradingClient,
    FakeTradingClient,
    contract_row,
    enum,
    make_adapter,
    option_position,
    request_json,
)

OCC = "APH261002P00130000"
STO = (
    '{"client_order_id": "swingpap-sto-0", "extended_hours": false, '
    '"limit_price": 1.23, "position_intent": "sell_to_open", "qty": 1.0, '
    '"side": "sell", "symbol": "APH261002P00130000", "time_in_force": "day", '
    '"type": "limit"}')
BTC = (
    '{"client_order_id": "swingpap-btc-0", "extended_hours": false, '
    '"position_intent": "buy_to_close", "qty": 1.0, "side": "buy", '
    '"symbol": "APH261002P00130000", "time_in_force": "day", '
    '"type": "market"}')


class _ApiError(Exception):
    status_code = 403


def _sto(adapter, cid="swingpap-sto-0", **changes):
    args = dict(symbol=OCC, side="sell", qty=1.0, notional=None,
                order_type="limit", limit_price=1.23, tif="day",
                extended_hours=False, client_order_id=cid)
    kwargs = dict(asset_class="us_option", position_intent="sell_to_open")
    for key, value in changes.items():
        (kwargs if key in kwargs else args)[key] = value
    return adapter.submit_order(**args, **kwargs)


def test_sell_to_open_request_shape():
    client = FakeTradingClient()
    adapter = make_adapter(client)
    _sto(adapter)
    assert request_json(client.submitted[0]) == STO


def test_buy_to_close_skips_the_pdt_preflight_buy_to_open_does_not():
    client = FakeTradingClient()
    adapter = make_adapter(client)
    calls = []
    adapter._preflight_buy = lambda **kw: calls.append(kw)
    adapter.submit_order(OCC, "buy", 1, None, "market", None, "day", False,
                         "swingpap-btc-0", asset_class="us_option",
                         position_intent="buy_to_close")
    assert calls == []
    assert request_json(client.submitted[0]) == BTC
    adapter.submit_order(OCC, "buy", 1, None, "limit", 0.5, "day", False,
                         "swingpap-bto-0", asset_class="us_option",
                         position_intent="buy_to_open")
    assert len(calls) == 1


def test_an_eb_buy_still_runs_the_pdt_preflight():
    client = FakeTradingClient()
    adapter = make_adapter(client, instance_id="alpaca-main")
    calls = []
    adapter._preflight_buy = lambda **kw: calls.append(kw)
    adapter.submit_order("TQQQ", "buy", 2.5, None, "market", None, "day",
                         False, "alpacama-pdt-0")
    assert [c["symbol"] for c in calls] == ["TQQQ"]


@pytest.mark.parametrize("changes", [
    {"qty": 1.5},
    {"qty": None, "notional": 123.0},
    {"extended_hours": True},
    {"position_intent": "buy_to_open"},
    {"position_intent": None},
    {"order_type": "limit", "limit_price": None},
    {"order_type": "stop"},
    {"tif": "ioc"},
])
def test_a_malformed_option_order_is_refused_before_the_wal(changes):
    client = FakeTradingClient()
    adapter = make_adapter(client)
    with pytest.raises(BrokerPreflightBlocked):
        _sto(adapter, cid="swingpap-bad-0", **changes)
    assert client.submitted == []
    assert adapter._wal.get("swingpap-bad-0") is None


def test_an_option_rejection_is_options_not_permitted_not_fractional():
    client = FakeTradingClient(submit_error=_ApiError(
        '{"code":40310000,"message":"account not eligible to trade '
        'uncovered option contracts"}'))
    adapter = make_adapter(client)
    with pytest.raises(OptionsNotPermitted) as info:
        _sto(adapter)
    assert info.value.broker_definitive_rejection is True
    assert len(client.submitted) == 1


def test_an_unanswered_option_submit_stays_ambiguous():
    """A transport failure (no HTTP answer) is never mapped to
    OptionsNotPermitted, even when its text mentions an option: it stays an
    ambiguous transport error, retried under the same client order id."""
    client = FakeTradingClient(submit_error=ConnectionError(
        "option order connection reset"))
    adapter = make_adapter(client)
    with pytest.raises(BrokerError) as info:
        _sto(adapter)
    assert not isinstance(info.value, OptionsNotPermitted)
    assert info.value.broker_definitive_rejection is False


def test_an_equity_fractional_rejection_still_takes_the_whole_share_retry():
    client = FakeTradingClient(submit_error=_ApiError(
        'asset "BBGI" is not fractionable 40310000'))
    adapter = make_adapter(client, instance_id="alpaca-main")
    with pytest.raises(FractionalNotAllowed):
        adapter.submit_order("BBGI", "buy", 2.5, None, "market", None, "day",
                             False, "alpacama-bbgi-0")
    assert len(client.submitted) == 2
    assert client.submitted[1].qty == 2.0


def _fill(symbol, side, position_delta, cash_delta, price):
    event = BrokerOrderEvent(
        event_id=f"{symbol}-{side}", account_id="acct-1",
        instance_id="swing-paper", client_order_id=f"cid-{side}",
        broker_order_id=f"b-{side}", symbol=symbol,
        side=OrderSide(side), state=LifecycleState.FILLED,
        cumulative_quantity=Decimal("1"),
        cumulative_average_price=Decimal(price),
        cumulative_fees=Decimal("0"), occurred_at=T0)
    return ConfirmedFill(
        event=event, incremental_quantity=Decimal("1"),
        incremental_price=Decimal(price), incremental_fees=Decimal("0"),
        position_delta=Decimal(position_delta), cash_delta=Decimal(cash_delta),
        asset_class="us_option", contract_multiplier=100)


def test_short_option_sign_survives_refresh_and_fills():
    """Review Focus 4."""
    client = FakeOptionsTradingClient(
        positions=[option_position()],
        contracts_by_symbol={OCC: contract_row()})
    adapter = make_adapter(client, clean_room=True)
    assert OCC not in adapter._positions
    held = adapter._option_positions[OCC]
    assert (held.qty, held.underlying, held.option_type, held.strike,
            held.expiry, held.multiplier) == (-1, "APH", "put", 130.0,
                                              "2026-10-02", 100)
    assert adapter._option_positions_complete is True
    # After a healthy reconcile the clean-room filter still never adopts it.
    adapter._reconciliation_healthy = True
    adapter._external_positions = {}
    rows = adapter.refresh_positions()
    assert OCC not in adapter._positions
    assert adapter._option_positions[OCC].qty == -1
    row = next(r for r in rows if r.symbol == OCC)
    assert (row.asset_class, row.side, row.multiplier, row.unrealized_pl,
            row.underlying, row.strike) == ("us_option", "short", 100, 13.0,
                                            "APH", 130.0)
    # Ruling F4: the option type comes from Alpaca's contract fields.
    assert (row.option_type, row.expiry) == ("put", "2026-10-02")
    assert adapter.list_option_positions() == [adapter._option_positions[OCC]]
    # A buy-to-close fill moves -1 to 0 and removes it; cash moves x100.
    cash = adapter._cash
    fill = _fill(OCC, "buy", "1", "-40", "0.40")
    adapter.apply_lifecycle_event(fill.event, fill)
    assert OCC not in adapter._option_positions
    assert adapter._cash == pytest.approx(cash - 40)
    assert OCC not in adapter._positions
    # A sell-to-open fill re-creates the short from the cached contract.
    fill = _fill(OCC, "sell", "-1", "123", "1.23")
    adapter.apply_lifecycle_event(fill.event, fill)
    assert adapter._option_positions[OCC].qty == -1
    assert adapter._option_positions[OCC].underlying == "APH"
    assert OCC not in adapter._positions
    # Option fills never reach the equity trade history.
    assert adapter._trades == []
    # Broker truth without the position empties the map.
    client.positions = []
    adapter.refresh_positions()
    assert adapter._option_positions == {}


def test_unknown_contract_fields_keep_the_short_but_mark_collateral_unknown():
    client = FakeOptionsTradingClient(positions=[option_position()])
    adapter = make_adapter(client, clean_room=True)
    held = adapter._option_positions[OCC]
    assert held.qty == -1 and held.strike == 0.0 and held.underlying == ""
    assert adapter._option_positions_complete is False
    lookups = len(client.contract_lookups)
    adapter.refresh_positions()
    assert len(client.contract_lookups) == lookups   # negative-cached 60 s


def test_a_stale_positions_clobber_marks_the_option_map_incomplete():
    """When the REST outage outlives the staleness cap, refresh_positions
    clobbers to empty. An empty option map read that way is NOT proof that no
    short puts are open, so it must not read as complete."""
    client = FakeOptionsTradingClient(
        positions=[option_position()],
        contracts_by_symbol={OCC: contract_row()})
    adapter = make_adapter(client, clean_room=True)
    assert adapter._option_positions_complete is True

    def _down():
        raise ConnectionError("positions endpoint down")

    client.get_all_positions = _down
    adapter._positions_stale_since = 1.0      # long past the 600 s cap
    adapter.refresh_positions()
    assert adapter._option_positions == {}
    assert adapter._option_positions_complete is False


@pytest.mark.parametrize("clean_room", [False, True])
def test_an_unreadable_option_row_marks_the_map_incomplete(clean_room):
    """A row whose fields cannot be read must not silently drop out of a map
    that still reads complete: that would hide an open short put from the
    collateral check. It stays out of the equity mirror too."""
    client = FakeOptionsTradingClient(
        positions=[option_position(avg_entry_price="n/a")],
        contracts_by_symbol={OCC: contract_row()})
    adapter = make_adapter(client, clean_room=clean_room)
    assert OCC not in adapter._option_positions
    assert adapter._option_positions_complete is False
    assert OCC not in adapter._positions


@pytest.mark.parametrize("clean_room", [False, True])
def test_a_long_option_never_enters_the_equity_mirror(clean_room):
    """The clean-room filter drops a SHORT contract on its own; a LONG one
    (buy_to_open is a legal intent) would be adopted after a healthy
    reconcile, and legacy mode adopts every broker row. Neither may reach the
    equity mirror: contracts live only in _option_positions."""
    call = "APH261016C00140000"
    equity = SimpleNamespace(symbol="TQQQ", qty="10", market_value="880",
                             avg_entry_price="80",
                             asset_class=enum("us_equity"))
    client = FakeOptionsTradingClient(
        positions=[equity, option_position(
            call, qty="2", side=enum("long"), market_value="300",
            current_price="1.5", unrealized_pl="20")],
        contracts_by_symbol={call: contract_row(
            call, kind="call", strike=140.0, expiration="2026-10-16")})
    adapter = make_adapter(client, clean_room=clean_room)
    if clean_room:
        adapter._reconciliation_healthy = True
        adapter._external_positions = {}
    rows = adapter.refresh_positions()
    assert adapter._positions == {"TQQQ": 10.0}
    assert call not in adapter._last_prices
    held = adapter._option_positions[call]
    assert (held.qty, held.option_type, held.strike) == (2, "call", 140.0)
    assert [(r.symbol, r.asset_class, r.side) for r in rows] == [
        ("TQQQ", None, None), (call, "us_option", "long")]


def test_an_equity_only_account_leaves_the_option_map_empty():
    equity = SimpleNamespace(symbol="TQQQ", qty="10", market_value="880",
                             avg_entry_price="80")
    client = FakeTradingClient(positions=[equity])
    adapter = make_adapter(client, instance_id="alpaca-main")
    assert adapter._option_positions == {}
    assert adapter._option_positions_complete is True
    assert adapter._positions == {"TQQQ": 10.0}
    row = adapter.refresh_positions()[0]
    assert row.asset_class is None and row.multiplier == 1


# --- EB invariance pins (L2 implementer, 2026-09-24) -------------------------
# Every expected value below was printed by the PRE-Task-5 adapter (HEAD
# e73755b) on the same inputs before any Task 5 edit. They cover the legacy
# (non-clean-room) mirror, the production deferred clean-room path before and
# after a healthy reconcile, the REST-outage preserve path, and two EB
# lifecycle fills. Never edit them to make a test pass.

def _equity_book():
    def pos(sym, qty, mv, avg, **kw):
        return SimpleNamespace(symbol=sym, qty=qty, market_value=mv,
                               avg_entry_price=avg, **kw)

    return [pos("TQQQ", "10", "880", "80", asset_class=enum("us_equity")),
            pos("GLD", "4.5", "1490.4", "300", asset_class=enum("us_equity")),
            pos("XLE", "0", "0", "0", asset_class=enum("us_equity")),
            pos("ZERO", "3", None, None),
            pos("BAD", "abc", "1", "1"),
            pos("SHRT", "-2", "-50", "25", asset_class=enum("us_equity"))]


def _state(adapter, rows):
    for row in rows:
        # New PositionDTO fields stay at their defaults on every equity row.
        assert (row.asset_class, row.side, row.multiplier, row.underlying,
                row.option_type, row.strike, row.expiry) == (
            None, None, 1, None, None, None, None)
    return json.dumps({
        "positions": dict(sorted(adapter._positions.items())),
        "last_prices": dict(sorted(adapter._last_prices.items())),
        "external": {
            k: {kk: vv for kk, vv in v.items() if kk != "first_seen_utc"}
            for k, v in sorted(adapter._external_positions.items())},
        "rows": [[r.symbol, r.qty, r.avg_entry_price, r.market_value,
                  r.created_at_utc] for r in rows],
    }, sort_keys=True, default=str)


_ROWS = ('"rows": [["TQQQ", 10.0, 80.0, 880.0, null], '
         '["GLD", 4.5, 300.0, 1490.4, null], ["XLE", 0.0, 0.0, 0.0, null], '
         '["ZERO", 3.0, 0.0, 0.0, null], ["SHRT", -2.0, 25.0, -50.0, null]]')
_PENDING = (
    '"external": {"GLD": {"market_value": 1490.4, "note": "startup '
    'reconciliation pending", "qty": 4.5}, "SHRT": {"market_value": -50.0, '
    '"note": "startup reconciliation pending", "qty": -2.0}, "TQQQ": '
    '{"market_value": 880.0, "note": "startup reconciliation pending", '
    '"qty": 10.0}, "XLE": {"market_value": 0.0, "note": "startup '
    'reconciliation pending", "qty": 0.0}, "ZERO": {"market_value": 0.0, '
    '"note": "startup reconciliation pending", "qty": 3.0}}')
_PRICES = '"last_prices": {"GLD": 331.20000000000005, "TQQQ": 88.0}'
_LEGACY_POSITIONS = (
    '"positions": {"GLD": 4.5, "SHRT": -2.0, "TQQQ": 10.0, "XLE": 0.0, '
    '"ZERO": 3.0}')
EB_LEGACY_INIT = ('{"external": {}, ' + _PRICES + ', ' + _LEGACY_POSITIONS
                  + ', "rows": []}')
EB_LEGACY_REFRESH = ('{"external": {}, ' + _PRICES + ', ' + _LEGACY_POSITIONS
                     + ', ' + _ROWS + '}')
EB_LEGACY_REST_FAILURE = (
    '{"external": {}, ' + _PRICES + ', ' + _LEGACY_POSITIONS + ', '
    '"rows": [["TQQQ", 10.0, 0.0, 880.0, null], ["GLD", 4.5, 0.0, 1490.4, '
    'null], ["XLE", 0.0, 0.0, 0.0, null], ["ZERO", 3.0, 0.0, 0.0, null], '
    '["SHRT", -2.0, 0.0, 0.0, null]]}')
EB_DEFERRED_INIT = '{' + _PENDING + ', ' + _PRICES + ', "positions": {}, "rows": []}'
EB_DEFERRED_UNHEALTHY = ('{' + _PENDING + ', ' + _PRICES + ', "positions": {}, '
                         + _ROWS + '}')
EB_DEFERRED_HEALTHY = (
    '{"external": {"GLD": {"market_value": 0.0, "note": "x", "qty": 1.5}}, '
    + _PRICES + ', "positions": {"GLD": 3.0, "TQQQ": 10.0, "ZERO": 3.0}, '
    + _ROWS + '}')
EB_FILLS = (
    '{"cash": 11313.39, "last_prices": {"GLD": 331.2, "TQQQ": 88.5}, '
    '"positions": {"SHRT": -2.0, "TQQQ": 12.0, "XLE": 0.0, "ZERO": 3.0}, '
    '"trades": [{"action": "buy", "cash_after": 9823.0, "client_order_id": '
    '"alpacama-x-0", "fees": 0.0, "order_id": "b1", "price": 88.5, "shares": '
    '2.0, "ticker": "TQQQ", "timestamp": "2026-10-05T13:15:00+00:00", '
    '"total": 177.0}, {"action": "sell", "cash_after": 11313.39, '
    '"client_order_id": "alpacama-y-0", "fees": 0.01, "order_id": "b2", '
    '"price": 331.2, "shares": 4.5, "ticker": "GLD", "timestamp": '
    '"2026-10-05T13:15:00+00:00", "total": 1490.4}]}')


def test_eb_legacy_equity_mirror_is_byte_identical():
    client = FakeTradingClient(positions=_equity_book())
    adapter = make_adapter(client, instance_id="alpaca-main")
    assert _state(adapter, []) == EB_LEGACY_INIT
    assert _state(adapter, adapter.refresh_positions()) == EB_LEGACY_REFRESH
    assert adapter._option_positions == {}
    assert adapter._option_positions_complete is True


def test_eb_clean_room_equity_mirror_is_byte_identical():
    client = FakeTradingClient(positions=_equity_book())
    adapter = make_adapter(client, instance_id="alpaca-main", clean_room=True)
    assert _state(adapter, []) == EB_DEFERRED_INIT
    assert _state(adapter, adapter.refresh_positions()) == EB_DEFERRED_UNHEALTHY
    adapter._reconciliation_healthy = True
    adapter._external_positions = {
        "GLD": {"qty": 1.5, "market_value": 0.0, "note": "x"}}
    assert _state(adapter, adapter.refresh_positions()) == EB_DEFERRED_HEALTHY
    assert adapter._option_positions == {}


def test_eb_rest_outage_preserve_path_is_byte_identical():
    client = FakeTradingClient(positions=_equity_book())
    adapter = make_adapter(client, instance_id="alpaca-main")

    def _down():
        raise Exception("503")

    client.get_all_positions = _down
    assert _state(adapter, adapter.refresh_positions()) == EB_LEGACY_REST_FAILURE


def test_eb_lifecycle_fills_are_byte_identical():
    client = FakeTradingClient(positions=_equity_book())
    adapter = make_adapter(client, instance_id="alpaca-main")
    buy = BrokerOrderEvent(
        event_id="e1", account_id="acct-1", instance_id="alpaca-main",
        client_order_id="alpacama-x-0", broker_order_id="b1", symbol="TQQQ",
        side=OrderSide.BUY, state=LifecycleState.FILLED,
        cumulative_quantity=Decimal("2"),
        cumulative_average_price=Decimal("88.5"),
        cumulative_fees=Decimal("0"), occurred_at=T0)
    adapter.apply_lifecycle_event(buy, ConfirmedFill(
        event=buy, incremental_quantity=Decimal("2"),
        incremental_price=Decimal("88.5"), incremental_fees=Decimal("0"),
        position_delta=Decimal("2"), cash_delta=Decimal("-177")))
    sell = BrokerOrderEvent(
        event_id="e2", account_id="acct-1", instance_id="alpaca-main",
        client_order_id="alpacama-y-0", broker_order_id="b2", symbol="GLD",
        side=OrderSide.SELL, state=LifecycleState.FILLED,
        cumulative_quantity=Decimal("4.5"),
        cumulative_average_price=Decimal("331.2"),
        cumulative_fees=Decimal("0.01"), occurred_at=T0)
    adapter.apply_lifecycle_event(sell, ConfirmedFill(
        event=sell, incremental_quantity=Decimal("4.5"),
        incremental_price=Decimal("331.2"), incremental_fees=Decimal("0.01"),
        position_delta=Decimal("-4.5"), cash_delta=Decimal("1490.39")))
    got = json.dumps({
        "positions": dict(sorted(adapter._positions.items())),
        "cash": adapter._cash,
        "last_prices": dict(sorted(adapter._last_prices.items())),
        "trades": [{k: (v.isoformat() if hasattr(v, "isoformat") else v)
                    for k, v in t.items()} for t in adapter._trades],
    }, sort_keys=True, default=str)
    assert got == EB_FILLS
    assert adapter._option_positions == {}


# --- L2 review fixes (controller ruling, 2026-09-25): fail closed ------------

def test_a_failed_first_positions_read_leaves_the_option_map_incomplete():
    """Fix 1. The adapter used to start at complete=True, so a positions
    endpoint that was down at construction left "no options, complete" on an
    account that may hold a short put."""
    client = FakeOptionsTradingClient(positions=[option_position()])

    def _down():
        raise ConnectionError("positions endpoint down")

    client.get_all_positions = _down
    adapter = make_adapter(client, clean_room=True)
    assert adapter._option_positions == {}
    assert adapter._option_positions_complete is False
    client.get_all_positions = lambda: [option_position()]
    client.contracts_by_symbol = {OCC: contract_row()}
    adapter.refresh_positions()
    assert adapter._option_positions[OCC].qty == -1
    assert adapter._option_positions_complete is True


@pytest.mark.parametrize("clean_room", [False, True])
def test_a_garbage_market_value_never_drops_an_option_row(clean_room):
    """Fix 2. The asset class is read before the equity float parsing, so a
    market value the equity path cannot parse no longer drops a short put
    silently. The option branch reads it as unknown and keeps the row."""
    client = FakeOptionsTradingClient(
        positions=[option_position(market_value="n/a")],
        contracts_by_symbol={OCC: contract_row()})
    adapter = make_adapter(client, clean_room=clean_room)
    held = adapter._option_positions[OCC]
    assert (held.qty, held.market_value, held.strike) == (-1, None, 130.0)
    assert adapter._option_positions_complete is True
    assert OCC not in adapter._positions


@pytest.mark.parametrize("clean_room", [False, True])
def test_an_unparseable_option_quantity_marks_the_map_incomplete(clean_room):
    """Fix 2 (and L2 concern 4). An option row whose qty cannot be read is a
    miss, never a silent drop."""
    client = FakeOptionsTradingClient(
        positions=[option_position(qty="n/a")],
        contracts_by_symbol={OCC: contract_row()})
    adapter = make_adapter(client, clean_room=clean_room)
    assert OCC not in adapter._option_positions
    assert adapter._option_positions_complete is False
    assert OCC not in adapter._positions


def test_an_equity_row_with_a_garbage_market_value_is_still_skipped():
    """EB: the equity path is unchanged -- an unparseable row is skipped."""
    rows = [SimpleNamespace(symbol="TQQQ", qty="10", market_value="n/a",
                            avg_entry_price="80",
                            asset_class=enum("us_equity")),
            SimpleNamespace(symbol="GLD", qty="4", market_value="1200",
                            avg_entry_price="290",
                            asset_class=enum("us_equity"))]
    adapter = make_adapter(FakeTradingClient(positions=rows),
                           instance_id="alpaca-main")
    assert adapter._positions == {"GLD": 4.0}
    assert adapter._option_positions_complete is True


class _AnsweredOptionError(Exception):
    status_code = 422


@pytest.mark.parametrize("error,mapped,definitive", [
    (_AnsweredOptionError("options trading not approved"), True, True),
    (ConnectionError("option order connection reset"), False, False),
    (TimeoutError("read timed out on option order"), False, False),
])
def test_option_errors_map_only_when_alpaca_answered(error, mapped, definitive):
    """Fix 3. "option" in the text is Alpaca's refusal only when Alpaca
    answered (an HTTP response is present)."""
    adapter = make_adapter(FakeTradingClient(submit_error=error))
    with pytest.raises(BrokerError) as info:
        _sto(adapter)
    assert isinstance(info.value, OptionsNotPermitted) is mapped
    assert info.value.broker_definitive_rejection is definitive


def test_an_eb_transport_failure_is_unchanged():
    client = FakeTradingClient(submit_error=ConnectionError("reset"))
    adapter = make_adapter(client, instance_id="alpaca-main")
    with pytest.raises(BrokerError) as info:
        adapter.submit_order("TQQQ", "buy", 2.0, None, "market", None, "day",
                             False, "alpacama-reset-0")
    assert type(info.value) is BrokerError
    assert info.value.broker_definitive_rejection is False


@pytest.mark.parametrize("answer", [None, {"message": "forbidden"}, "x"])
def test_a_non_list_activities_answer_fails_closed(answer):
    """Fix 4. A 200 whose body is not a list is not "no activities"."""
    client = FakeOptionsTradingClient()
    client.get = lambda path, data=None: answer
    adapter = make_adapter(client)
    with pytest.raises(BrokerError, match="OPASN"):
        adapter.get_option_activities(types=("OPASN",))


def test_an_empty_activities_list_is_still_no_activities():
    client = FakeOptionsTradingClient(activities={"OPASN": []})
    adapter = make_adapter(client)
    assert adapter.get_option_activities(types=("OPASN",)) == []


def test_a_sideless_stream_event_is_logged_once_then_dropped(monkeypatch):
    """Fix 5: the drop is not silent."""
    import broker_adapters.alpaca as alpaca_module

    lines = []
    monkeypatch.setattr(alpaca_module, "_alog",
                        lambda service, msg, color="white":
                        lines.append((service, msg, color)))
    adapter = make_adapter(FakeTradingClient())
    adapter._order_event_account_id = "acct-1"
    event = adapter._normalized_trade_update(SimpleNamespace(
        event="fill", order=SimpleNamespace(
            id="mleg-1", client_order_id="ui-mleg", symbol="", side=None,
            position_intent=None, order_class=enum("mleg"),
            status=enum("filled"), filled_qty="1", filled_avg_price="1.2"),
        message=""))
    assert event is None
    assert len(lines) == 1 and lines[0][2] == "yellow"
    assert "ui-mleg" in lines[0][1] and "fill" in lines[0][1]


# --- fix wave FW1 item 8 (live-orders review M-2): only Alpaca's own refusal --

def _answered(status, text):
    error = Exception(text)
    error.status_code = status
    return error


@pytest.mark.parametrize("error", [
    _answered(503, f"503 Service Unavailable while routing {OCC}"),
    _answered(500, f'{{"message":"internal error on option order {OCC}"}}'),
    _answered(422, f'{{"code":42210000,"message":"invalid expiration for option {OCC}"}}'),
    _answered(422, '{"code":42210000,"message":"limit price must be in 0.05 '
                   'increments for this option"}'),
])
def test_an_answered_error_that_merely_names_an_option_keeps_its_own_type(error):
    """M-2: any answered error whose text said "option" read as "options not
    permitted", including a 5xx whose body names the contract, so the alert
    named the wrong cause. Still definitive (Alpaca answered), as before."""
    adapter = make_adapter(FakeTradingClient(submit_error=error))
    with pytest.raises(BrokerError) as info:
        _sto(adapter)
    assert not isinstance(info.value, OptionsNotPermitted)
    assert info.value.broker_definitive_rejection is True


@pytest.mark.parametrize("error", [
    _answered(403, '{"code":40310000,"message":"account not eligible to trade '
                   'uncovered option contracts"}'),
    _answered(422, "options trading not approved"),
    _answered(403, '{"message":"options trading is not enabled for this account"}'),
    _answered(403, "account is not approved for options trading level 2"),
])
def test_alpacas_options_refusals_still_map_to_options_not_permitted(error):
    adapter = make_adapter(FakeTradingClient(submit_error=error))
    with pytest.raises(OptionsNotPermitted) as info:
        _sto(adapter)
    assert info.value.broker_definitive_rejection is True
