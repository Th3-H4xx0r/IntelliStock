"""FW-str-I1 (swing-port final review, strategies slice Important 1).

A swing exit is ST's market sell at the open: a plain MARKET DAY sell that
Alpaca queues for the open, never the extended-hours limit at last x 0.995
that EB's builder makes before 09:30 ET. Its bracket legs are cancelled only
after the gate accepts the sell, inside the service, and a sell that fails
after its legs were cancelled raises a red alert (the lane re-sends it on the
next tick). EB never sends the hint and its document never enables the swing
lane, so EB's builder and submit call are unchanged (pinned below and in
test_swing_broker_bracket.py)."""
import datetime as datetime_module
from decimal import Decimal
from types import SimpleNamespace

import pytest

from broker_adapters.alpaca import AlpacaAdapter
from broker_adapters.errors import InsufficientBuyingPower
from live_orders import (
    Health,
    InMemoryLifecycleBackend,
    LiveOrderService,
    OrderLifecycleStore,
    OrderSide,
    OrderSource,
)
from swing_broker_harness import extract, function_source, source
from swing_live_fixtures import bracket_intent, equity_snapshot, leg_ref, parent_ref

#: Monday 2026-10-05 09:20 ET, the swing lane's scan tick, before the open.
PREMARKET = datetime_module.datetime(2026, 10, 5, 13, 20,
                                     tzinfo=datetime_module.timezone.utc)
PAPER = SimpleNamespace(account_id="acct-1", instance_id="instance-1")


class _Styled:
    """The live portfolio_emulator is the adapter; its real session styling."""
    _order_style_for_now = AlpacaAdapter._order_style_for_now

    def __init__(self, positions):
        self._positions = positions


def _builder():
    return extract(("_build_strategy_stock_intent", "_build_bracket_intent"),
                   namespace={"datetime": datetime_module})[
        "_build_strategy_stock_intent"]


def _sell(build, **changes):
    values = dict(symbol="AAPL", decision=-1, price=100.0,
                  current_time=PREMARKET, cash_to_use=0, sell_fraction=1.0,
                  action_intents={"swing_stop_exit"}, is_risk_exit=False,
                  risk_snapshot_id="risk-1", quote_at=PREMARKET)
    values.update(changes)
    return build(PAPER, _Styled({"AAPL": 12}), **values)


# --- the builder ---------------------------------------------------------------

def test_a_premarket_swing_exit_is_a_plain_market_day_sell():
    """The probe's case: 12 shares at $100 at 09:20 ET."""
    order = _sell(_builder(), next_open_sell=True)
    assert (order.side, order.order_type, order.limit_price, order.tif,
            order.extended_hours, order.quantity, order.reduce_only) == (
        OrderSide.SELL, "market", None, "day", False, Decimal("12"), True)


def test_the_same_sell_without_the_flag_keeps_the_eb_conversion():
    """The contrast (and today's EB behaviour): an extended-hours limit."""
    order = _sell(_builder())
    assert (order.order_type, order.limit_price, order.extended_hours) == (
        "limit", Decimal("99.5"), True)


def test_a_plain_sell_in_regular_hours_matches_the_eb_style():
    """In regular hours the session style is already market DAY, so the swing
    exit and the old path build the same order."""
    rth = datetime_module.datetime(2026, 10, 5, 14, 40,
                                   tzinfo=datetime_module.timezone.utc)
    build = _builder()
    plain = _sell(build, next_open_sell=True, current_time=rth, quote_at=rth)
    styled = _sell(build, current_time=rth, quote_at=rth)
    assert plain.identity_payload == styled.identity_payload
    assert (plain.order_type, plain.extended_hours, plain.limit_price) == (
        styled.order_type, styled.extended_hours, styled.limit_price)


def test_the_flag_never_changes_a_buy():
    build = _builder()
    args = dict(decision=1, cash_to_use=1000, action_intents={"swing_entry"})
    assert (_sell(build, next_open_sell=True, **args).identity_payload
            == _sell(build, **args).identity_payload)


@pytest.mark.parametrize("hint,intents,expected", [
    ({"sell_fraction": 1.0, "fill_at_next_open": True}, set(), True),
    ({"sell_fraction": 1.0}, {"swing_rsi_exit"}, True),      # a re-sent exit
    ({"sell_fraction": 1.0}, {"swing_target_exit", "sell"}, True),
    ({}, {"swing_stop_exit"}, True),
    ({"sell_fraction": 1.0, "fill_at_next_open": "yes"}, set(), False),
    ({"sell_fraction": 1.0, "fill_at_next_open": False}, {"sell"}, False),
    ({}, {"eb_rebalance"}, False),                           # EB
    ({}, set(), False),
    (None, None, False),
    ("junk", ["junk"], False),
])
def test_a_swing_exit_is_named_by_the_hint_or_the_lanes_exit_intents(
        hint, intents, expected):
    is_exit = extract(("_swing_next_open_exit",),
                      assigns=("_SWING_EXIT_INTENTS",))["_swing_next_open_exit"]
    assert is_exit(hint, intents) is expected


def test_the_exit_intents_are_the_swing_lanes():
    from strategies import strategy_swing
    ns = extract(("_swing_next_open_exit",), assigns=("_SWING_EXIT_INTENTS",))
    assert ns["_SWING_EXIT_INTENTS"] == frozenset(strategy_swing.INTENT_EXIT.values())


# --- the service hook ------------------------------------------------------------

def _sell_intent(**changes):
    values = dict(source=OrderSource.STRATEGY, reason="swing_stop_exit",
                  side=OrderSide.SELL, quantity=Decimal("5"), reduce_only=True,
                  order_type="market", tif="day", order_class=None,
                  take_profit_price=None, stop_loss_price=None)
    values.update(changes)
    return bracket_intent(**values)


def _service(events, *, snapshot=None, transport=None, store=None):
    def provider(intent):
        events.append("gate-read")
        return (snapshot or (lambda i: equity_snapshot(
            i, position_quantity=Decimal("5"))))(intent)

    def send(**kwargs):
        events.append("transport")
        if transport is not None:
            return transport(**kwargs)
        return SimpleNamespace(status="accepted", broker_order_id="b-sell",
                               id="b-sell", filled_qty=0, filled_avg_price=None)

    return LiveOrderService(
        account_id="acct-1", instance_id="instance-1",
        snapshot_provider=provider, transport=send,
        lifecycle_store=store or OrderLifecycleStore(InMemoryLifecycleBackend()))


def test_the_hook_runs_after_the_gate_allows_and_before_anything_exists():
    events = []
    service = _service(events)
    seen = []

    def hook(intent, decision):
        events.append("hook")
        seen.append((intent.symbol, decision.allowed,
                     list(service.lifecycle_store.list_for_instance("instance-1"))))

    assert service.submit(_sell_intent(), before_submit=hook).accepted
    assert events == ["gate-read", "hook", "transport"]
    assert seen == [("AAPL", True, [])]


def test_the_hook_is_not_called_when_the_gate_refuses():
    events = []
    service = _service(events, snapshot=lambda i: equity_snapshot(
        i, position_quantity=Decimal("5"), quote=Health.UNKNOWN))
    out = service.submit(_sell_intent(),
                         before_submit=lambda *_a: events.append("hook"))
    assert not out.decision.allowed
    assert "dependency.quote.unknown" in out.decision.reason_codes
    assert events == ["gate-read"]


def test_a_hook_that_raises_leaves_no_row_and_no_reservation():
    events = []
    service = _service(events)
    intent = _sell_intent()

    def hook(*_args):
        raise ValueError("order deferred: AAPL bracket legs did not confirm")

    with pytest.raises(ValueError, match="order deferred"):
        service.submit(intent, before_submit=hook)
    assert list(service.lifecycle_store.list_for_instance("instance-1")) == []
    assert service.reservation_for(intent.idempotency_key) is None
    assert events == ["gate-read"]
    # The next tick's sell goes out normally.
    assert service.submit(intent).accepted


def test_without_a_hook_the_submit_is_unchanged():
    events = []
    assert _service(events).submit(_sell_intent()).accepted
    assert events == ["gate-read", "transport"]


# --- _submit_swing_sell ----------------------------------------------------------

class _LegAdapter:
    def __init__(self, events, *, confirmed=True, working=()):
        self.events = events
        self.confirmed = confirmed
        self.working = list(working)
        self.cancel_calls = []

    def list_open_orders_strict(self, limit=200):
        return list(self.working)

    def cancel_orders_confirmed(self, order_ids, timeout_s=10.0, *,
                                booked_fills=None):
        self.events.append("cancel-legs")
        self.cancel_calls.append(sorted(order_ids))
        return self.confirmed


def _open_leg(symbol="AAPL"):
    return SimpleNamespace(broker_order_id="broker-leg-sl", symbol=symbol,
                           side="sell", status="held", order_class="bracket")


def _submit(alerts=None, lines=None, kinds=None):
    def record(instance_id, symbol, detail, *, kind="unprotected", **_kw):
        (alerts if alerts is not None else []).append((instance_id, symbol, detail))
        (kinds if kinds is not None else []).append(kind)

    ns = extract(("_submit_swing_sell", "_cancel_bracket_legs_confirmed"),
                 namespace={"_swing_unprotected_alert": record})
    return ns["_submit_swing_sell"]


def _log(lines):
    return lambda message, color="white": lines.append((color, message))


def test_a_gate_refusal_leaves_the_legs_working():
    """Scenario (b) of the review: a stale pre-market mark. Today the legs were
    already gone; now the stop stays and nothing is cancelled."""
    events, alerts, lines = [], [], []
    adapter = _LegAdapter(events, working=[_open_leg()])
    service = _service(events, snapshot=lambda i: equity_snapshot(
        i, position_quantity=Decimal("5"), quote=Health.UNHEALTHY))
    out = _submit(alerts)(adapter, service, _sell_intent(), log=_log(lines))
    assert not out.decision.allowed
    assert adapter.cancel_calls == [] and "transport" not in events
    assert alerts == []


def test_an_accepted_sell_cancels_the_legs_then_goes_out():
    events, alerts, lines = [], [], []
    adapter = _LegAdapter(events, working=[_open_leg()])
    out = _submit(alerts)(adapter, _service(events), _sell_intent(),
                          log=_log(lines))
    assert out.accepted
    assert events == ["gate-read", "cancel-legs", "transport"]
    assert adapter.cancel_calls == [["broker-leg-sl"]]
    assert alerts == []


def test_unconfirmed_legs_defer_the_sell_with_the_old_message():
    events, alerts = [], []
    adapter = _LegAdapter(events, confirmed=False, working=[_open_leg()])
    service = _service(events)
    with pytest.raises(ValueError) as deferred:
        _submit(alerts)(adapter, service, _sell_intent())
    assert str(deferred.value) == (
        "order deferred: AAPL bracket legs did not confirm cancelled within "
        "10s; the sell waits a tick")
    assert "transport" not in events
    assert list(service.lifecycle_store.list_for_instance("instance-1")) == []
    # Round 2 (must-check 2(d)): the cancel WAS sent, so the stop may be gone.
    assert len(alerts) == 1


class _Refused(InsufficientBuyingPower):
    status_code = 403


def test_a_refused_sell_after_the_legs_were_cancelled_is_a_red_alert():
    events, alerts, lines = [], [], []
    adapter = _LegAdapter(events, working=[_open_leg()])

    def refuse(**_kwargs):
        raise _Refused("insufficient qty available for order")

    out = _submit(alerts)(adapter, _service(events, transport=refuse),
                          _sell_intent(), log=_log(lines))
    assert not out.accepted
    ((instance_id, symbol, detail),) = alerts
    assert (instance_id, symbol) == ("instance-1", "AAPL")
    assert "broker.rejected" in detail
    assert any(color == "red" and "UNPROTECTED" in message
               for color, message in lines)


def test_an_unknown_outcome_after_the_legs_were_cancelled_is_alerted_too():
    events, alerts = [], []
    adapter = _LegAdapter(events, working=[_open_leg()])

    def drop(**_kwargs):
        raise ConnectionError("socket closed")

    kinds = []
    out = _submit(alerts, kinds=kinds)(adapter, _service(events, transport=drop),
                                       _sell_intent())
    assert out.uncertain and not out.accepted
    assert len(alerts) == 1 and "may not have been placed" in alerts[0][2]
    # Round 2, minor 1: the outcome is unknown, not "not placed".
    assert kinds == ["unknown"]


def test_a_failed_sell_with_no_legs_to_cancel_raises_no_alert():
    events, alerts = [], []
    adapter = _LegAdapter(events)

    def refuse(**_kwargs):
        raise _Refused("insufficient qty available for order")

    out = _submit(alerts)(adapter, _service(events, transport=refuse),
                          _sell_intent())
    assert not out.accepted and adapter.cancel_calls == [] and alerts == []


def test_a_submit_that_raises_after_the_legs_were_cancelled_is_alerted():
    events, alerts = [], []
    adapter = _LegAdapter(events, working=[_open_leg()])
    service = _service(events)

    class Broken:
        instance_id = "instance-1"
        lifecycle_store = service.lifecycle_store

        def enqueue(self, intent, *, before_submit=None):
            before_submit(intent, None)
            raise RuntimeError("lifecycle backend down")

    kinds = []
    with pytest.raises(RuntimeError):
        _submit(alerts, kinds=kinds)(adapter, Broken(), _sell_intent())
    assert len(alerts) == 1 and "RuntimeError" in alerts[0][2]
    # Round 2, minor 1: a raise can come after a successful transport.
    assert kinds == ["unknown"]


def test_a_definite_refusal_after_the_cancel_stays_not_placed():
    events, alerts, kinds = [], [], []
    adapter = _LegAdapter(events, working=[_open_leg()])

    def refuse(**_kwargs):
        raise _Refused("insufficient qty available for order")

    _submit(alerts, kinds=kinds)(adapter, _service(events, transport=refuse),
                                 _sell_intent())
    assert kinds == ["unprotected"]


def test_the_unknown_outcome_alert_says_check_open_orders(monkeypatch):
    from swing_trader import notify

    sent = []
    monkeypatch.setattr(notify, "_sink", lambda **kw: sent.append(kw))
    ns = extract(("_swing_unprotected_alert",), check=())
    at = datetime_module.datetime(2026, 10, 5, 14, 0, tzinfo=datetime_module.timezone.utc)
    alert = ns["_swing_unprotected_alert"]
    assert alert("swing-paper", "AAPL", "RuntimeError: x", kind="unknown", now_utc=at)
    assert alert("swing-paper", "AAPL", "again", kind="unknown", now_utc=at) is False
    # The unprotected alert has its own once-per-session slot.
    assert alert("swing-paper", "AAPL", "refused", now_utc=at)
    unknown, not_placed = sent
    assert unknown["push_title"].startswith("EXIT OUTCOME UNKNOWN — check open orders")
    assert "AAPL" in unknown["push_title"] and "(URGENT)" in unknown["push_title"]
    assert "NOT PLACED" not in unknown["body"]
    assert not_placed["push_title"].startswith("EXIT NOT PLACED")


# --- the live loop wiring (source assertions: the loop is module-level) -------

def _gate_block():
    text = source()
    start = text.index("if _is_alpaca_stock_gate:")
    return text[start:text.index("_submission = _es_fut.result(", start)]


def test_the_loop_no_longer_cancels_legs_before_the_gate():
    block = _gate_block()
    assert "_cancel_bracket_legs_confirmed(" not in block
    assert "_submit_swing_sell," in block


def test_a_swing_sell_is_only_a_sell_on_a_swing_document():
    block = _gate_block()
    decl = block[block.index("_swing_sell = ("):block.index("_risk_exit_intents =")]
    assert "decision == -1" in decl
    assert '_lane_enabled(_cached_strategies, "strategy_swing")' in decl
    assert decl.index("decision == -1") < decl.index("_lane_enabled(")


def test_eb_keeps_its_exact_enqueue_call():
    """EB pin: a document without a swing lane submits exactly as before."""
    block = _gate_block()
    assert ("_PRICE_FETCH_EXECUTOR.submit(\n"
            "                                                _live_stock_order_service.enqueue,\n"
            "                                                _stock_intent,\n"
            "                                            )") in block
    assert "if _swing_sell" in block


def test_the_builder_call_passes_the_next_open_flag_only_for_a_swing_sell():
    block = _gate_block()
    call = block[block.index("_build_strategy_stock_intent("):]
    assert "next_open_sell=(" in call
    flag = call[call.index("next_open_sell=("):]
    assert flag.index("_swing_sell") < flag.index("_swing_next_open_exit(")


def test_the_leg_cancel_lives_in_the_after_the_gate_hook():
    body = function_source("_submit_swing_sell")
    assert "before_submit=" in body and "_cancel_bracket_legs_confirmed(" in body


# --- round 2, item 1 (must-check 2(d)): a leg cancel ATTEMPTED and the exit
# not sent is a red alert, once per symbol per session -------------------------

UNPROTECTED = ("position may be unprotected — the bracket stop may have been "
               "cancelled; the exit will retry next tick")


class _RaisingCancel(_LegAdapter):
    def cancel_orders_confirmed(self, order_ids, timeout_s=10.0, *,
                                booked_fills=None):
        self.events.append("cancel-legs")
        self.cancel_calls.append(sorted(order_ids))
        raise ConnectionError("reset after the DELETE went out")


@pytest.mark.parametrize("adapter_kind", ["unconfirmed", "raised"])
def test_a_cancel_sent_but_not_confirmed_is_alerted_before_the_deferral(adapter_kind):
    events, alerts, lines = [], [], []
    adapter = (_LegAdapter(events, confirmed=False, working=[_open_leg()])
               if adapter_kind == "unconfirmed"
               else _RaisingCancel(events, working=[_open_leg()]))
    service = _service(events)
    with pytest.raises(ValueError, match="^order deferred: AAPL bracket legs"):
        _submit(alerts)(adapter, service, _sell_intent(), log=_log(lines))
    assert adapter.cancel_calls == [["broker-leg-sl"]] and "transport" not in events
    ((instance_id, symbol, detail),) = alerts
    assert (instance_id, symbol) == ("instance-1", "AAPL")
    assert any(color == "red" and "UNPROTECTED" in message
               for color, message in lines)


def test_no_cancel_sent_no_alert():
    """The book was unreadable, so no cancel went out: the stop is intact."""
    events, alerts = [], []

    class Unreadable(_LegAdapter):
        def list_open_orders_strict(self, limit=200):
            raise ConnectionError("orders endpoint down")

    with pytest.raises(ValueError, match="order deferred"):
        _submit(alerts)(Unreadable(events), _service(events), _sell_intent())
    assert alerts == []


def _real_alert(sent, now):
    ns = extract(("_swing_unprotected_alert",), check=())
    return lambda symbol, detail="d", **kw: ns["_swing_unprotected_alert"](
        "swing-paper", symbol, detail, now_utc=now, **kw)


def test_the_unprotected_alert_is_red_urgent_and_once_per_symbol_per_session(
        monkeypatch):
    from swing_trader import notify

    sent = []
    monkeypatch.setattr(notify, "_sink", lambda **kw: sent.append(kw))
    monday = datetime_module.datetime(2026, 10, 5, 14, 0,
                                      tzinfo=datetime_module.timezone.utc)
    alert = _real_alert(sent, monday)
    assert alert("AAPL") is True
    assert alert("AAPL", "again") is False            # throttled
    assert alert("MSFT") is True
    tuesday = _real_alert(sent, monday + datetime_module.timedelta(days=1))
    assert tuesday("AAPL") is True
    assert len(sent) == 3
    first = sent[0]
    assert first["category"] == "swing_exit"
    assert "(URGENT)" in first["push_title"] and "AAPL" in first["push_title"]
    assert UNPROTECTED in first["body"] and UNPROTECTED in first["push_body"]


def test_the_reviewers_probe_now_raises_one_alert(monkeypatch):
    """fw1rr/probe_partial_cancel.py: cancels requested for both legs, the
    confirm never comes, 0 orders sent -> exactly 1 push (was 0)."""
    from swing_trader import notify

    alerts, lines = [], []
    monkeypatch.setattr(notify, "_sink", lambda **kw: alerts.append(kw))
    ns = extract(("_submit_swing_sell", "_cancel_bracket_legs_confirmed",
                  "_swing_unprotected_alert"), check=())

    class Adapter:
        def __init__(self):
            self.cancel_calls = []

        def list_open_orders_strict(self):
            return [SimpleNamespace(broker_order_id="leg-tp", symbol="AAPL",
                                    side="sell", order_class="bracket", status="new"),
                    SimpleNamespace(broker_order_id="leg-sl", symbol="AAPL",
                                    side="sell", order_class="bracket", status="held")]

        def cancel_orders_confirmed(self, ids, timeout_s=10.0, *, booked_fills=None):
            self.cancel_calls.append(list(ids))
            return False

    events = []
    adapter = Adapter()
    for _tick in range(2):                     # two ticks, one alert
        with pytest.raises(ValueError, match="order deferred"):
            ns["_submit_swing_sell"](adapter, _service(events), _sell_intent(),
                                     log=_log(lines))
    assert adapter.cancel_calls == [["leg-tp", "leg-sl"]] * 2
    assert "transport" not in events
    assert len(alerts) == 1 and UNPROTECTED in alerts[0]["body"]
    assert sum(1 for c, m in lines if c == "red" and "UNPROTECTED" in m) == 2


def test_the_lane_re_sends_the_exit_while_only_its_legs_are_working():
    """The deferral re-arms nothing in the engine: the lane re-sends its
    pending exit next tick because a bracket leg (even pending_cancel or
    held) is not a working exit sell."""
    from strategies.strategy_swing import StrategySwing, _PENDING_EXIT_KEY

    cache = {_PENDING_EXIT_KEY: {"AAPL": {"reason": "stop_loss",
                                          "intent": "swing_stop_exit",
                                          "since": "2026-10-05"}}}
    book = [SimpleNamespace(symbol="AAPL", side="sell", order_class="bracket",
                            status=status) for status in ("pending_cancel", "held")]
    decisions, sizes, intents = {}, {}, {}
    StrategySwing()._reemit_exits(
        "2026-10-05", lambda: ({"AAPL": {"qty": 12}}, None, {"AAPL"}), cache, book,
        decisions, sizes, intents)
    assert decisions == {"AAPL": -1}
    assert intents == {"AAPL": "swing_stop_exit"}
