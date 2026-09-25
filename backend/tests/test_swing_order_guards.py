"""swing-port Task 11: bracket legs are exits, not pending or working
orders; the kill rung and halt leave risk-reducing orders working.

Ruling F1 (A-live pre-flight): only an OPTION buy/sell-to-close
(asset_class us_option) and a bracket child leg are risk-reducing. An EB stock
order is cancelled by halt and the kill rung exactly as before, whatever
position_intent Alpaca tags it with. The EB pins below were computed on the
pre-change code."""
import datetime as datetime_module
import hashlib
import itertools
import json
import sys
from types import SimpleNamespace

import pytest

from broker_adapters.base import OrderRef
from live_pending_orders import LivePendingOrdersUnavailable, live_pending_symbols
from live_risk_state import cancel_open_buy_orders
from swing_alpaca_fakes import FakeTradingClient, enum, make_adapter, order_row
from swing_broker_harness import extract

OCC = "APH261009P00130000"


def _bracket_rows():
    parent = order_row(id="p", client_order_id="swingpap-p-0", symbol="AAPL",
                       status=enum("filled"), order_class=enum("bracket"))
    tp = order_row(id="tp", client_order_id="leg-tp", symbol="AAPL",
                   side=enum("sell"), status=enum("new"),
                   order_class=enum("bracket"), type=enum("limit"))
    sl = order_row(id="sl", client_order_id="leg-sl", symbol="AAPL",
                   side=enum("sell"), status=enum("held"),
                   order_class=enum("bracket"), type=enum("stop"))
    return parent, tp, sl


def test_ordered_today_ignores_bracket_legs_but_keeps_the_parent():
    parent, tp, sl = _bracket_rows()
    eb_sell = order_row(id="e", client_order_id="alpacama-e-0", symbol="GLD",
                        side=enum("sell"), status=enum("new"))
    adapter = make_adapter(FakeTradingClient(orders=[parent, tp, sl, eb_sell]))
    assert adapter.refresh_orders_today() == {"AAPL": {"buy"}, "GLD": {"sell"}}
    assert adapter.ordered_today("AAPL", "buy") is True
    assert adapter.ordered_today("AAPL", "sell") is False


def test_the_pending_guard_ignores_legs_but_not_a_held_simple_order():
    tp = OrderRef("tp", "leg-tp", "AAPL", "sell", 5.0, "new", order_class="bracket")
    sl = OrderRef("sl", "leg-sl", "AAPL", "sell", 5.0, "held", order_class="bracket")
    odd = OrderRef("x", "c-x", "MSFT", "buy", 1.0, "held", order_class="simple")
    eb = OrderRef("e", "c-e", "TQQQ", "buy", 1.0, "new")
    adapter = SimpleNamespace(list_open_orders_strict=lambda: [tp, sl, odd, eb])
    assert live_pending_symbols(adapter) == ("MSFT", "TQQQ")


def test_the_pending_guard_still_fails_closed_on_a_malformed_leg():
    """The leg skip sits AFTER the symbol/status checks: a leg without a
    usable status is still an unreadable book, not a skipped row."""
    leg = OrderRef("tp", "leg-tp", "AAPL", "sell", 5.0, "", order_class="bracket")
    adapter = SimpleNamespace(list_open_orders_strict=lambda: [leg])
    with pytest.raises(LivePendingOrdersUnavailable):
        live_pending_symbols(adapter)


LEG = SimpleNamespace(broker_order_id="tp", symbol="TQQQ", side="sell",
                      status="new", order_class="bracket")
HELD = SimpleNamespace(broker_order_id="sl", symbol="TQQQ", side="sell",
                       status="held", order_class="bracket")
PLAIN = SimpleNamespace(broker_order_id="s", symbol="TQQQ", side="sell",
                        status="new", order_class="simple")


def _helpers():
    return extract(("_core_sell_may_be_working", "_eb_buy_may_be_working",
                    "_eb_order_may_be_working", "_eb_sell_leg_may_be_working"))


def _book(*refs):
    return SimpleNamespace(list_open_orders_strict=lambda limit=200: list(refs))


def _quiet(*_args, **_kwargs):
    return None


def test_bracket_legs_are_not_working_orders_for_the_eb_helpers():
    ns = _helpers()
    book = _book(LEG, HELD)
    assert ns["_core_sell_may_be_working"](book, "TQQQ", _quiet) is False
    assert ns["_eb_order_may_be_working"](book, _quiet) is False
    assert ns["_eb_sell_leg_may_be_working"](book, "TQQQ", _quiet) is False
    assert ns["_eb_buy_may_be_working"](book, _quiet) is False


def test_eb_shaped_orders_still_count_as_working():
    ns = _helpers()
    book = _book(PLAIN)
    assert ns["_core_sell_may_be_working"](book, "TQQQ", _quiet) is True
    assert ns["_eb_order_may_be_working"](book, _quiet) is True
    assert ns["_eb_sell_leg_may_be_working"](book, "TQQQ", _quiet) is True


def test_the_kill_rung_cancels_opening_buys_but_not_a_buy_to_close():
    btc = SimpleNamespace(broker_order_id="btc", symbol=OCC, side="buy",
                          status="new", order_class="simple",
                          position_intent="buy_to_close",
                          asset_class="us_option")
    parent = SimpleNamespace(broker_order_id="parent", symbol="AAPL",
                             side="buy", status="new", order_class="bracket")
    plain = SimpleNamespace(broker_order_id="plain", symbol="TQQQ",
                            side="buy", status="new")
    cancelled = []
    adapter = SimpleNamespace(
        list_open_orders_strict=lambda: [btc, parent, plain],
        cancel_order=lambda oid: cancelled.append(oid) or True)
    assert cancel_open_buy_orders(adapter, log=_quiet) == 2
    assert cancelled == ["parent", "plain"]


def _halt_ns(monkeypatch):
    monkeypatch.setitem(sys.modules, "live_alerts",
                        SimpleNamespace(alert_halt=lambda **kw: None))
    return extract(("_execute_live_command",), check=(), namespace={
        "datetime": datetime_module, "instance_id": "swing-paper",
        "get_conn_retry": lambda **kw: None,
        "r": SimpleNamespace(update=lambda *a, **k: None),
        "time": SimpleNamespace(sleep=lambda seconds: None),
    })


def _halt(ns, *orders):
    cancelled = []
    adapter = SimpleNamespace(
        list_open_orders=lambda limit=500: list(orders),
        cancel_order=lambda oid: cancelled.append(oid) or True)
    ok, error, result = ns["_execute_live_command"](
        adapter, {"type": "halt", "payload": {"reason": "test"}})
    assert ok is True, error
    return cancelled, result


def test_halt_leaves_risk_reducing_orders_working(monkeypatch):
    ns = _halt_ns(monkeypatch)
    leg = SimpleNamespace(broker_order_id="tp", symbol="AAPL", side="sell",
                          status="new", order_class="bracket")
    btc = SimpleNamespace(broker_order_id="btc", symbol=OCC, side="buy",
                          status="new", position_intent="buy_to_close",
                          asset_class="us_option")
    buy = SimpleNamespace(broker_order_id="buy", symbol="MSFT", side="buy",
                          status="new")
    cancelled, result = _halt(ns, leg, btc, buy)
    assert set(cancelled) == {"buy"} and result["orders_canceled"] == 2


# --- ruling F1: EB stock orders are cancelled whatever Alpaca tags them -------

@pytest.mark.parametrize("tags", [
    {},
    {"position_intent": "sell_to_close"},
    {"position_intent": "sell_to_close", "asset_class": "us_equity"},
    {"position_intent": enum("sell_to_close"), "asset_class": enum("us_equity")},
    {"position_intent": "sell_to_close", "order_class": "simple"},
])
def test_halt_still_cancels_an_eb_stock_sell(monkeypatch, tags):
    ns = _halt_ns(monkeypatch)
    sell = SimpleNamespace(broker_order_id="eb-sell", symbol="GLD",
                           side="sell", status="new", **tags)
    cancelled, result = _halt(ns, sell)
    assert cancelled == ["eb-sell", "eb-sell"]
    assert result["orders_canceled"] == 2


@pytest.mark.parametrize("tags", [
    {},
    {"position_intent": "buy_to_open"},
    {"position_intent": "buy_to_close"},
    {"position_intent": "buy_to_close", "asset_class": "us_equity"},
    {"position_intent": enum("buy_to_close"), "asset_class": enum("us_equity")},
])
def test_the_kill_rung_still_cancels_an_eb_stock_buy(tags):
    buy = SimpleNamespace(broker_order_id="eb-buy", symbol="TQQQ", side="buy",
                          status="new", **tags)
    cancelled = []
    adapter = SimpleNamespace(
        list_open_orders_strict=lambda: [buy],
        cancel_order=lambda oid: cancelled.append(oid) or True)
    assert cancel_open_buy_orders(adapter, log=_quiet) == 1
    assert cancelled == ["eb-buy"]


def test_an_option_sell_to_close_survives_halt_too(monkeypatch):
    ns = _halt_ns(monkeypatch)
    stc = SimpleNamespace(broker_order_id="stc", symbol=OCC, side="sell",
                          status="new", position_intent="sell_to_close",
                          asset_class="us_option")
    sto = SimpleNamespace(broker_order_id="sto", symbol=OCC, side="sell",
                          status="new", position_intent="sell_to_open",
                          asset_class="us_option")
    cancelled, _result = _halt(ns, stc, sto)
    assert cancelled == ["sto", "sto"]


# --- EB pins, computed from the PRE-change code at 9b1c370 --------------------

_EB_SIDES = ("buy", "sell")
_EB_STATUSES = ("new", "accepted", "partially_filled", "pending_new", "held",
                "filled", "canceled")
_EB_CLASSES = (None, "simple", "SIMPLE")
_EB_INTENTS = (None, "buy_to_open", "sell_to_close", "buy_to_close")
_EB_ASSETS = (None, "us_equity")
_EB_SYMBOLS = ("TQQQ", "GLD")


def _eb_refs():
    refs = []
    for n, (side, status, cls, intent, asset, symbol) in enumerate(
            itertools.product(_EB_SIDES, _EB_STATUSES, _EB_CLASSES,
                              _EB_INTENTS, _EB_ASSETS, _EB_SYMBOLS)):
        values = {"broker_order_id": f"b-{n}", "client_order_id": f"alpacama-{n}-0",
                  "symbol": symbol, "side": side, "status": status}
        if cls is not None:
            values["order_class"] = cls
        if intent is not None:
            values["position_intent"] = intent
        if asset is not None:
            values["asset_class"] = asset
        refs.append(SimpleNamespace(**values))
    return refs


def _eb_books():
    refs = _eb_refs()
    books = [[ref] for ref in refs]
    books += [[refs[i], refs[(i * 7 + 3) % len(refs)]] for i in range(len(refs))]
    return books


def _digest(rows):
    return hashlib.sha256(json.dumps(rows, sort_keys=True, default=str)
                          .encode()).hexdigest()


#: 672 single-order books plus 672 two-order books of EB-shaped refs:
#: buy/sell x 7 statuses (held included) x no/simple/SIMPLE class x no/4 kinds
#: of position_intent x no/us_equity asset class x 2 symbols.
EB_HELPERS_DIGEST = (
    "07d58f3b9ce0b603164f2d0b3065b4213337290bd4f9dfbdff79d3cb9f1fca08")
EB_KILL_DIGEST = (
    "427864226086ebddc070590b07d4e2e67cc525ea1e63be91316cc375dbd11b28")
EB_HALT_DIGEST = (
    "b4cac188f0f9bae38d61158586a839ff41b62e7b80d64c22435d98a0d8048aa9")
EB_PENDING_DIGEST = (
    "4b7f6727a0a04272a746c69165b0216af51f9278d64467f53e3865c08ee468db")
EB_ORDERED_TODAY_DIGEST = (
    "31df2f7a92f048413625ce734397f554b987cee2e0eef4ceb75d8f1df7ae9bef")


def _helper_rows():
    ns = _helpers()
    rows = []
    for book in _eb_books():
        said = []

        def say(message, color="white"):
            said.append((message, color))

        adapter = _book(*book)
        rows.append([
            ns["_core_sell_may_be_working"](adapter, "TQQQ", say),
            ns["_eb_buy_may_be_working"](adapter, say),
            ns["_eb_order_may_be_working"](adapter, say),
            ns["_eb_sell_leg_may_be_working"](adapter, "TQQQ", say),
            said,
        ])
    return rows


def _kill_rows():
    rows = []
    for book in _eb_books():
        cancelled = []
        adapter = SimpleNamespace(
            list_open_orders_strict=lambda book=book: list(book),
            cancel_order=lambda oid: cancelled.append(oid) or True)
        rows.append([cancel_open_buy_orders(adapter, log=_quiet), cancelled])
    return rows


def _halt_rows(ns):
    rows = []
    for book in _eb_books():
        cancelled, result = _halt(ns, *book)
        rows.append([cancelled, result])
    return rows


def _pending_rows():
    rows = []
    for book in _eb_books():
        adapter = SimpleNamespace(list_open_orders_strict=lambda book=book: list(book))
        rows.append(list(live_pending_symbols(adapter)))
    return rows


def _ordered_today_rows():
    client = FakeTradingClient()
    adapter = make_adapter(client)
    rows = []
    for book in _eb_books():
        client.orders = {}
        for ref in book:
            tags = {"order_class": enum(getattr(ref, "order_class", "simple")),
                    "position_intent": (enum(ref.position_intent)
                                        if getattr(ref, "position_intent", None)
                                        else None)}
            if getattr(ref, "asset_class", None):
                tags["asset_class"] = enum(ref.asset_class)
            client.orders[ref.broker_order_id] = order_row(
                id=ref.broker_order_id, client_order_id=ref.client_order_id,
                symbol=ref.symbol, side=enum(ref.side),
                status=enum(ref.status), **tags)
        out = adapter.refresh_orders_today()
        rows.append({sym: sorted(sides) for sym, sides in out.items()})
    return rows


def test_eb_working_order_helpers_are_byte_identical():
    assert _digest(_helper_rows()) == EB_HELPERS_DIGEST


def test_eb_kill_rung_cancels_are_byte_identical():
    assert _digest(_kill_rows()) == EB_KILL_DIGEST


def test_eb_halt_cancels_are_byte_identical(monkeypatch):
    assert _digest(_halt_rows(_halt_ns(monkeypatch))) == EB_HALT_DIGEST


def test_eb_pending_symbols_are_byte_identical():
    assert _digest(_pending_rows()) == EB_PENDING_DIGEST


def test_eb_orders_today_are_byte_identical():
    assert _digest(_ordered_today_rows()) == EB_ORDERED_TODAY_DIGEST


def test_the_real_halt_and_kill_loops_keep_only_risk_reducers(monkeypatch):
    """L1 checked the F1 skip rule on a comprehension until Task 11 wired
    it; this runs the real halt loop and kill rung over the same EB shapes
    (with and without Alpaca's position_intent, enum-valued fields, a held
    simple sell), an option buy-to-close and a bracket leg."""
    from test_swing_adapter_contract import EB_STOCK_ORDERS

    option_btc = OrderRef(
        "b-opt", "swingpap-btc-0", "APH261002P00130000", "buy", 1.0, "new",
        order_class="simple", position_intent="buy_to_close",
        asset_class="us_option", order_type="limit", limit_price=0.4)
    leg = OrderRef("b-leg", "leg-tp", "AAPL", "sell", 5.0, "new",
                   order_class="bracket", order_type="limit", limit_price=218.0)
    eb = list(EB_STOCK_ORDERS.values())
    working = [*eb, option_btc, leg]
    cancelled, result = _halt(_halt_ns(monkeypatch), *working)
    assert cancelled == [o.broker_order_id for o in eb] * 2
    assert result["orders_canceled"] == 2 * len(eb)
    killed = []
    adapter = SimpleNamespace(
        list_open_orders_strict=lambda: list(working),
        cancel_order=lambda oid: killed.append(oid) or True)
    cancel_open_buy_orders(adapter, log=_quiet)
    assert killed == [o.broker_order_id for o in eb
                      if str(getattr(o, "side", "")).lower() == "buy"]
    assert killed  # the EB buy tagged buy_to_close is still cancelled


# --- fix wave FW-lo-I4: the kill rung cancels a working sell-to-open ----------

def test_the_kill_rung_cancels_a_working_sell_to_open_but_leaves_stock_sells():
    """A sell-to-open put is a SELL that OPENS a strike x 100 obligation; the
    kill rung exists to stop new exposure. EB's stock sells stay working,
    whatever position_intent Alpaca tags them with (ruling F1)."""
    sto = OrderRef("sto", "swingpap-sto-0", OCC, "sell", 1.0, "new",
                   order_class="simple", position_intent="sell_to_open",
                   asset_class="us_option", order_type="limit", limit_price=1.14)
    sto_enum = SimpleNamespace(broker_order_id="sto-enum", symbol=OCC,
                               side=enum("sell"), status=enum("new"),
                               order_class=enum("simple"),
                               position_intent=enum("sell_to_open"),
                               asset_class=enum("us_option"))
    stc = OrderRef("stc", "swingpap-stc-0", OCC, "sell", 1.0, "new",
                   order_class="simple", position_intent="sell_to_close",
                   asset_class="us_option")
    eb_sell = OrderRef("eb-sell", "alpacama-s-0", "GLD", "sell", 4.0, "new")
    eb_tagged = OrderRef("eb-tagged", "alpacama-t-0", "GDX", "sell", 3.0, "new",
                         position_intent="sell_to_open", asset_class="us_equity")
    eb_untyped = SimpleNamespace(broker_order_id="eb-untyped", symbol="XLE",
                                 side="sell", status="new",
                                 position_intent="sell_to_open")
    leg = OrderRef("leg", "leg-sl", "AAPL", "sell", 5.0, "held",
                   order_class="bracket")
    buy = OrderRef("buy", "alpacama-b-0", "TQQQ", "buy", 2.0, "new")
    cancelled = []
    adapter = SimpleNamespace(
        list_open_orders_strict=lambda: [sto, sto_enum, stc, eb_sell, eb_tagged,
                                         eb_untyped, leg, buy],
        cancel_order=lambda oid: cancelled.append(oid) or True)
    assert cancel_open_buy_orders(adapter, log=_quiet) == 3
    assert cancelled == ["sto", "sto-enum", "buy"]


def test_a_sell_to_open_the_broker_will_not_cancel_is_reported():
    sto = OrderRef("sto", "swingpap-sto-0", OCC, "sell", 1.0, "new",
                   position_intent="sell_to_open", asset_class="us_option")
    lines = []
    adapter = SimpleNamespace(list_open_orders_strict=lambda: [sto],
                              cancel_order=lambda oid: False)
    assert cancel_open_buy_orders(
        adapter, log=lambda message, color="red": lines.append(message)) == 0
    assert any("sell-to-open" in line and OCC in line for line in lines)
