"""swing-port Task 10: the wheel lane's option orders go through
LiveOrderService with a REST options quote and an options-aware dependency
snapshot. Spec section 6.1 broker item 2; section 9 fixes 1, 3 and 8."""
import dataclasses
import datetime as datetime_module
import enum
import hashlib
import itertools
import json
import sys
import threading
import types
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from broker_adapters.base import OptionPositionDTO, OptionSnapshotDTO
from live_orders import (
    DependencySnapshot,
    Health,
    InMemoryLifecycleBackend,
    LiveOrderService,
    OrderLifecycleStore,
    OrderSide,
    OrderSource,
    UnifiedOrderGate,
)
from live_order_task8_helpers import broker_ref, intent as equity_intent
from market_marks import MarketMark, MarkQuality, MarkSource
from swing_broker_harness import extract, source
from swing_live_fixtures import OCC, RTH, buy_to_close, option_intent, option_snapshot

ORDER = {"signal_id": None, "underlying": "APH", "contract": OCC,
         "option_type": "put", "strike": 130.0, "expiry": "2026-10-09",
         "position_intent": "sell_to_open", "qty": 1, "order_type": "limit",
         "limit_price": 1.2, "tif": "day", "reason": "wheel_sto_put"}
BTC = dict(ORDER, position_intent="buy_to_close", order_type="market",
           limit_price=None, reason="wheel_btc_itm")


def _snap(symbol, *, bid=1.1, ask=1.3, last=1.2, stamp=RTH):
    return OptionSnapshotDTO(symbol, bid, ask, last, 0.3, -0.25, 0.05, -0.04,
                             0.1, stamp.isoformat() if stamp else None)


class _Quotes:
    def __init__(self, **overrides):
        self.overrides = overrides
        self.calls = []

    def get_option_snapshots(self, contracts):
        self.calls.append(list(contracts))
        return {c: _snap(c, **self.overrides) for c in contracts}


def _executor(alerts=None):
    """The executor with its notification sender recorded, never sent."""
    sent = alerts if alerts is not None else []
    ns = extract(("_execute_option_intents", "_build_option_intent",
                  "_refresh_option_quote"),
                 assigns=("_live_option_quotes", "_auto_close_alerts"),
                 namespace={
                     "datetime": datetime_module,
                     "_wheel_alert": lambda title, message, *, priority=0: (
                         sent.append((title, message, priority)) or True),
                 })
    return ns


def _service(provider=option_snapshot, *, lookup=None):
    calls = []
    order = option_intent()
    service = LiveOrderService(
        account_id=order.account_id, instance_id=order.instance_id,
        snapshot_provider=provider,
        transport=lambda **kw: calls.append(kw) or SimpleNamespace(
            status="accepted", broker_order_id=f"b-{len(calls)}",
            id=f"b-{len(calls)}", filled_qty=0, filled_avg_price=None),
        lifecycle_store=OrderLifecycleStore(InMemoryLifecycleBackend()),
        lookup_by_client_id=lookup)
    return service, calls


def _lines():
    lines = []
    return lines, lambda message, color="white": lines.append((message, color))


def test_a_sell_to_open_goes_through_the_service_with_a_rest_quote():
    ns = _executor()
    service, calls = _service()
    lines, log = _lines()
    results = ns["_execute_option_intents"](
        [ORDER], order_service=service, adapter=_Quotes(), now_utc=RTH,
        risk_snapshot_id="risk-1", log=log)
    assert results[0]["status"] == "submitted", (results, lines)
    assert calls[0]["asset_class"] == "us_option"
    assert calls[0]["position_intent"] == "sell_to_open"
    assert (calls[0]["symbol"], calls[0]["qty"], calls[0]["limit_price"]) == (
        OCC, 1.0, 1.2)
    assert calls[0]["client_order_id"] == results[0]["client_order_id"]
    assert ns["_live_option_quotes"][OCC]["price"] == Decimal("1.2")


def test_a_rerun_minutes_later_is_deduped_not_resold():
    """Spec section 9 fix 1, end to end: one put per contract per session."""
    ns = _executor()
    service, calls = _service()
    execute = ns["_execute_option_intents"]
    execute([ORDER], order_service=service, adapter=_Quotes(), now_utc=RTH,
            risk_snapshot_id="risk-1")
    later = RTH + timedelta(minutes=7)
    rerun = execute([dict(ORDER, qty=2, limit_price=1.1)],
                    order_service=service,
                    adapter=_Quotes(stamp=later), now_utc=later,
                    risk_snapshot_id="risk-1")
    assert len(calls) == 1
    assert rerun[0]["status"] == "blocked"
    assert "idempotency.open_order_exists" in rerun[0]["reason_codes"]


def test_a_refused_lane_places_nothing():
    ns = _executor()
    service, calls = _service()
    lines, log = _lines()
    results = ns["_execute_option_intents"](
        [ORDER], order_service=service, adapter=_Quotes(), now_utc=RTH,
        risk_snapshot_id="risk-1", refused_reason="options_trading_level=0",
        log=log)
    assert results[0]["status"] == "refused" and calls == []
    assert any(color == "red" and "options_trading_level=0" in message
               for message, color in lines)


@pytest.mark.parametrize("bad", [
    dict(ORDER, qty=1.5), dict(ORDER, qty=True), dict(ORDER, qty=0),
    dict(ORDER, position_intent="sell_short"), dict(ORDER, expiry="soon"),
    "not-a-dict",
])
def test_a_malformed_order_is_dropped_loudly(bad):
    ns = _executor()
    service, calls = _service()
    lines, log = _lines()
    results = ns["_execute_option_intents"](
        [bad], order_service=service, adapter=_Quotes(), now_utc=RTH,
        risk_snapshot_id="risk-1", log=log)
    assert results[0]["status"] in ("invalid", "no_quote") and calls == []
    assert any(color == "red" for _message, color in lines)


def test_no_usable_quote_places_nothing():
    ns = _executor()
    service, calls = _service()
    results = ns["_execute_option_intents"](
        [ORDER], order_service=service,
        adapter=_Quotes(bid=None, ask=None, last=None), now_utc=RTH,
        risk_snapshot_id="risk-1")
    assert results[0]["status"] == "no_quote" and calls == []


def test_a_gate_refusal_is_logged_with_its_reasons():
    ns = _executor()
    service, calls = _service(
        provider=lambda cur: option_snapshot(cur, available_cash=Decimal("100")))
    lines, log = _lines()
    results = ns["_execute_option_intents"](
        [ORDER], order_service=service, adapter=_Quotes(), now_utc=RTH,
        risk_snapshot_id="risk-1", log=log)
    assert results[0]["status"] == "blocked" and calls == []
    assert "option.collateral_insufficient" in results[0]["reason_codes"]
    assert any("ORDER GATE BLOCKED" in m and "option.collateral_insufficient"
               in m for m, c in lines if c == "red")


@pytest.fixture
def signal_updates(monkeypatch):
    updates = []
    store = types.ModuleType("swing_trader.signals_store")
    store.update_signal = lambda signal_id, patch: updates.append(
        (signal_id, patch))
    package = types.ModuleType("swing_trader")
    package.__path__ = []
    package.signals_store = store
    monkeypatch.setitem(sys.modules, "swing_trader", package)
    monkeypatch.setitem(sys.modules, "swing_trader.signals_store", store)
    return updates


def test_signal_ids_are_written_back(signal_updates):
    updates = signal_updates
    ns = _executor()
    service, _calls = _service()
    results = ns["_execute_option_intents"](
        [dict(ORDER, signal_id="sig-1"),
         dict(ORDER, signal_id="sig-2", contract="APH261009P00125000",
              strike=125.0, qty=1.5)],
        order_service=service, adapter=_Quotes(), now_utc=RTH,
        risk_snapshot_id="risk-1")
    assert updates[0] == ("sig-1", {"status": "submitted",
                                    "order_client_id": results[0]["client_order_id"]})
    assert updates[1] == ("sig-2", {"status": "failed", "order_client_id": None})


def test_a_quote_is_reused_for_60_seconds_then_refreshed():
    ns = _executor()
    refresh = ns["_refresh_option_quote"]
    adapter = _Quotes()
    first = refresh(adapter, OCC, RTH)
    assert refresh(adapter, OCC, RTH + timedelta(seconds=60)) is first
    refresh(adapter, OCC, RTH + timedelta(seconds=61))
    assert len(adapter.calls) == 2
    assert first["price"] == Decimal("1.2") and first["quote_at"] == RTH


def test_a_one_sided_book_falls_back_to_the_last_trade():
    ns = _executor()
    quote = ns["_refresh_option_quote"](_Quotes(bid=None, last=0.95), OCC, RTH)
    assert quote["price"] == Decimal("0.95")


def test_buy_to_close_intents_are_risk_exits():
    ns = _executor()
    service, _calls = _service()
    order = ns["_build_option_intent"](
        service, dict(ORDER, position_intent="buy_to_close",
                      order_type="market", limit_price=None,
                      reason="wheel_btc_itm"),
        quote_at=RTH, decision_at=RTH, risk_snapshot_id="risk-1")
    assert (order.side, order.reduce_only, order.source, order.limit_price,
            order.contract_multiplier) == (OrderSide.BUY, True,
                                           OrderSource.RISK_EXIT, None, 100)


# --- F3: a duplicate refusal never overwrites "submitted" ---------------------

def test_a_duplicate_refusal_leaves_the_submitted_signal_alone(signal_updates):
    """Ruling F3: the rerun of a working put is refused
    idempotency.open_order_exists. Writing "failed" over "submitted" would
    drop the signal from plan B's open statuses while the put is working."""
    alerts = []
    ns = _executor(alerts)
    service, calls = _service()
    execute = ns["_execute_option_intents"]
    execute([dict(ORDER, signal_id="sig-1")], order_service=service,
            adapter=_Quotes(), now_utc=RTH, risk_snapshot_id="risk-1")
    later = RTH + timedelta(minutes=7)
    lines, log = _lines()
    rerun = execute([dict(ORDER, signal_id="sig-1")], order_service=service,
                    adapter=_Quotes(stamp=later), now_utc=later,
                    risk_snapshot_id="risk-1", log=log)
    assert rerun[0]["status"] == "blocked"
    assert rerun[0]["reason_codes"] == ("idempotency.open_order_exists",)
    assert len(calls) == 1
    assert [status for _sid, patch in signal_updates
            for status in [patch["status"]]] == ["submitted"]
    assert alerts == []
    assert not any("ORDER GATE BLOCKED" in m for m, _c in lines)


def test_a_filled_put_rerun_is_a_duplicate_too(signal_updates):
    """The same session's put already FILLED: the service refuses
    idempotency.terminal_requires_retry. Still a duplicate, never "failed"."""
    ns = _executor()
    service, calls = _service()
    execute = ns["_execute_option_intents"]
    (first,) = execute([dict(ORDER, signal_id="sig-1")], order_service=service,
                       adapter=_Quotes(), now_utc=RTH, risk_snapshot_id="risk-1")
    record = service.lifecycle_store.require(first["client_order_id"])
    from live_orders import BrokerOrderEvent, LifecycleState
    service.apply_broker_event(BrokerOrderEvent(
        event_id="fill-1", account_id=record.intent.account_id,
        instance_id=record.intent.instance_id,
        client_order_id=record.client_order_id, broker_order_id="b-1",
        symbol=OCC, side=OrderSide.SELL, state=LifecycleState.FILLED,
        cumulative_quantity=Decimal("1"),
        cumulative_average_price=Decimal("1.2"),
        cumulative_fees=Decimal("0"), occurred_at=RTH))
    later = RTH + timedelta(minutes=30)
    (rerun,) = execute([dict(ORDER, signal_id="sig-1")], order_service=service,
                       adapter=_Quotes(stamp=later), now_utc=later,
                       risk_snapshot_id="risk-1")
    assert rerun["reason_codes"] == ("idempotency.terminal_requires_retry",)
    assert [patch["status"] for _sid, patch in signal_updates] == ["submitted"]
    assert len(calls) == 1


def test_an_unknown_outcome_is_never_written_back_as_failed(signal_updates):
    """A submit that raised may have reached the broker (the service applies
    the broker's answer after the transport returned). Like "uncertain", it
    leaves the signal as it was."""
    ns = _executor()

    class _Raising:
        account_id, instance_id = "acct-1", "instance-1"

        def submit(self, intent):
            raise RuntimeError("lifecycle store unreachable")

    lines, log = _lines()
    (result,) = ns["_execute_option_intents"](
        [dict(ORDER, signal_id="sig-9")], order_service=_Raising(),
        adapter=_Quotes(), now_utc=RTH, risk_snapshot_id="risk-1", log=log)
    assert result["status"] == "error"
    assert signal_updates == []
    assert any("sig-9" in m and c == "red" for m, c in lines)


# --- G7 review I-3: an auto-close that did not go out is shouted --------------

# L5 review ruling: an options-level refusal never refuses a buy_to_close
# (risk-reducing, F1), so "refused" is not a buy_to_close outcome any more;
# see test_an_options_level_refusal_never_blocks_a_buy_to_close.
@pytest.mark.parametrize("case", ["blocked", "no_quote", "error",
                                  "uncertain", "invalid"])
def test_a_buy_to_close_that_was_not_submitted_alerts_urgently(case):
    alerts = []
    ns = _executor(alerts)
    kwargs = dict(adapter=_Quotes(), now_utc=RTH, risk_snapshot_id="risk-1")
    order = dict(BTC)
    if case == "blocked":        # no short on the book: the gate refuses
        service, _calls = _service()
    elif case == "no_quote":
        service, _calls = _service()
        kwargs["adapter"] = _Quotes(bid=None, ask=None, last=None)
    elif case == "error":
        service = SimpleNamespace(
            account_id="acct-1", instance_id="instance-1",
            submit=lambda intent: (_ for _ in ()).throw(RuntimeError("boom")))
    elif case == "uncertain":
        service = SimpleNamespace(
            account_id="acct-1", instance_id="instance-1",
            submit=lambda intent: SimpleNamespace(
                accepted=False, uncertain=True,
                decision=SimpleNamespace(allowed=True,
                                         reason_codes=("broker.order.outcome_unknown",))))
    else:
        service, _calls = _service()
        order["qty"] = 1.5
    (result,) = ns["_execute_option_intents"]([order], order_service=service,
                                              **kwargs)
    assert result["status"] == case
    ((title, message, priority),) = alerts
    # L4 review M-1: only a definite refusal tells the operator to close by
    # hand; an unknown outcome says the close may be working.
    if case in ("error", "uncertain"):
        assert title == f"AUTO-CLOSE UNCONFIRMED — {OCC} — CHECK OPEN ORDERS"
        assert "may not have been placed — check open orders" in message
    else:
        assert title == f"AUTO-CLOSE FAILED — {OCC} — CLOSE MANUALLY IMMEDIATELY"
        assert "was NOT placed" in message
    assert priority == 2
    assert OCC in message


# --- L5 review ruling: the options-level refusal refuses opening orders only --

@pytest.mark.parametrize("refusal", ["options_trading_level=0",
                                     "options level unreadable (RuntimeError)"])
def test_an_options_level_refusal_never_blocks_a_buy_to_close(refusal):
    """A buy_to_close is risk-reducing (F1): the options-level verdict must
    not keep a short put open, and must not spend the session's definite
    AUTO-CLOSE alert slot on a close it refused itself."""
    alerts = []
    ns = _executor(alerts)
    service, calls = _service(provider=lambda cur: option_snapshot(
        cur, position_quantity=Decimal("-1")))
    (result,) = ns["_execute_option_intents"](
        [dict(BTC)], order_service=service, adapter=_Quotes(), now_utc=RTH,
        risk_snapshot_id="risk-1", refused_reason=refusal)
    assert result["status"] == "submitted" and len(calls) == 1
    assert calls[0]["position_intent"] == "buy_to_close"
    assert alerts == []
    assert ns["_auto_close_alerts"]["sent"] == set()


def test_an_options_level_refusal_lets_a_sell_to_close_through():
    ns = _executor()
    service, calls = _service(provider=lambda cur: option_snapshot(
        cur, position_quantity=Decimal("1")))
    stc = dict(ORDER, position_intent="sell_to_close", order_type="market",
               limit_price=None)
    (result,) = ns["_execute_option_intents"](
        [stc], order_service=service, adapter=_Quotes(), now_utc=RTH,
        risk_snapshot_id="risk-1", refused_reason="options_trading_level=0")
    assert result["status"] != "refused"
    assert result["client_order_id"] is not None


@pytest.mark.parametrize("position_intent", ["sell_to_open", "buy_to_open",
                                             None, "sell_short"])
def test_an_options_level_refusal_refuses_every_opening_or_unknown_order(
        position_intent):
    ns = _executor()
    service, calls = _service()
    quotes = _Quotes()
    (result,) = ns["_execute_option_intents"](
        [dict(ORDER, position_intent=position_intent)], order_service=service,
        adapter=quotes, now_utc=RTH, risk_snapshot_id="risk-1",
        refused_reason="options_trading_level=0")
    assert result["status"] == "refused" and calls == [] and quotes.calls == []


def test_a_submitted_buy_to_close_sends_no_failure_alert():
    alerts = []
    ns = _executor(alerts)
    service, calls = _service(provider=lambda cur: option_snapshot(
        cur, position_quantity=Decimal("-1")))
    (result,) = ns["_execute_option_intents"](
        [dict(BTC)], order_service=service, adapter=_Quotes(), now_utc=RTH,
        risk_snapshot_id="risk-1")
    assert result["status"] == "submitted" and len(calls) == 1
    assert alerts == []


def test_a_working_buy_to_close_rerun_is_not_an_auto_close_failure():
    """Same minute, same key: the close is already at the broker. Telling
    the operator to close by hand would double the close."""
    alerts = []
    ns = _executor(alerts)
    service, calls = _service(provider=lambda cur: option_snapshot(
        cur, position_quantity=Decimal("-1")))
    execute = ns["_execute_option_intents"]
    execute([dict(BTC)], order_service=service, adapter=_Quotes(),
            now_utc=RTH, risk_snapshot_id="risk-1")
    (rerun,) = execute([dict(BTC)], order_service=service, adapter=_Quotes(),
                       now_utc=RTH, risk_snapshot_id="risk-1")
    assert rerun["reason_codes"] == ("idempotency.open_order_exists",)
    assert len(calls) == 1 and alerts == []


@pytest.mark.parametrize("case", ["refused", "blocked", "invalid", "no_quote"])
def test_a_refused_put_with_a_signal_is_failed_and_corrected(case, signal_updates):
    alerts = []
    ns = _executor(alerts)
    kwargs = dict(adapter=_Quotes(), now_utc=RTH, risk_snapshot_id="risk-1")
    order = dict(ORDER, signal_id="sig-4")
    service, calls = _service()
    reason = {"refused": "options_trading_level=0",
              "blocked": "option.collateral_insufficient",
              "invalid": "qty", "no_quote": "no usable options"}[case]
    if case == "refused":
        kwargs["refused_reason"] = "options_trading_level=0"
    elif case == "blocked":
        service, calls = _service(provider=lambda cur: option_snapshot(
            cur, available_cash=Decimal("100")))
    elif case == "invalid":
        order["qty"] = 1.5
    else:
        kwargs["adapter"] = _Quotes(bid=None, ask=None, last=None)
    (result,) = ns["_execute_option_intents"]([order], order_service=service,
                                              **kwargs)
    assert result["status"] == case and calls == []
    assert signal_updates == [("sig-4", {"status": "failed",
                                         "order_client_id": result["client_order_id"]})]
    ((title, message, priority),) = alerts
    assert message.startswith("Put order NOT placed: ")
    assert reason in message
    assert OCC in title and priority == 1


def test_a_refused_put_without_a_signal_sends_no_correction():
    alerts = []
    ns = _executor(alerts)
    service, _calls = _service()
    ns["_execute_option_intents"](
        [dict(ORDER)], order_service=service, adapter=_Quotes(), now_utc=RTH,
        risk_snapshot_id="risk-1", refused_reason="options_trading_level=0")
    assert alerts == []


def test_the_wheel_alert_goes_through_notifications_notify(monkeypatch):
    sent = []
    module = types.ModuleType("notifications")
    module.notify = lambda **kwargs: sent.append(kwargs)
    monkeypatch.setitem(sys.modules, "notifications", module)
    ns = extract(("_wheel_alert",), namespace={
        "instance_id": "wheel-paper", "_log": lambda *a, **k: None})
    assert ns["_wheel_alert"]("AUTO-CLOSE FAILED — X", "the short put is open",
                              priority=2) is True
    ((kwargs,),) = [sent]
    assert kwargs["category"] == "wheel_position_alert"
    assert kwargs["instance_id"] == "wheel-paper"
    assert kwargs["title"] == "AUTO-CLOSE FAILED — X"
    assert kwargs["body"] == ("WHEEL ALERT [wheel-paper] AUTO-CLOSE FAILED — X "
                              "(URGENT)\nthe short put is open")
    assert kwargs["push_title"] == "AUTO-CLOSE FAILED — X (URGENT)"
    assert kwargs["discord_channel"]


def test_a_failing_notification_never_raises(monkeypatch):
    module = types.ModuleType("notifications")

    def boom(**kwargs):
        raise RuntimeError("outbox down")

    module.notify = boom
    monkeypatch.setitem(sys.modules, "notifications", module)
    lines = []
    ns = extract(("_wheel_alert",), namespace={
        "instance_id": "wheel-paper",
        "_log": lambda m, c="white": lines.append((m, c))})
    assert ns["_wheel_alert"]("t", "m", priority=1) is False
    assert any(c == "red" for _m, c in lines)


# --- the options dependency snapshot ------------------------------------------

def _position(symbol, underlying, strike, qty=-1):
    return OptionPositionDTO(symbol, underlying, "put", strike, "2026-10-09",
                             qty, 1.2, 1.1, -110.0, 10.0)


def _snapshot_ns(service, *, state=None):
    now = RTH
    base = {name: "healthy" for name in (
        "kill_switch", "cash", "positions", "persistence", "risk_state",
        "watchdog")}
    base.update({f"{name}_at": now for name in (
        "kill_switch", "cash", "positions", "persistence", "risk_state",
        "watchdog")})
    base["risk_snapshot_id"] = "risk-1"
    base.update(state or {})
    return extract(
        ("_live_option_dependency_snapshot", "_pending_sell_to_open_collateral",
         "_open_order_idempotency_keys"),
        assigns=("_live_option_quotes",),
        namespace={
            "datetime": datetime_module,
            "_live_order_dependency_lock": threading.Lock(),
            "_live_order_dependency_state": base,
            "_live_stock_order_service": service,
            "_live_risk_state": SimpleNamespace(
                max_order_notional=Decimal("10000")),
            "instance_id": "instance-1", "MODE_LIVE": "live", "mode": "live",
            "live_broker_type": "alpaca", "live_brokerage_id": "acct-1",
        })


def _adapter(**changes):
    values = dict(
        _option_positions={
            "APH261009P00130000": _position("APH261009P00130000", "APH", 130.0),
            "MSFT261009P00400000": _position("MSFT261009P00400000", "MSFT", 400.0),
        },
        _option_positions_complete=True, _cash=50000.0,
        _account_equity=100000.0, _instance_id="instance-1",
        _positions_stale_since=None)
    values.update(changes)
    return SimpleNamespace(**values)


def _pending_service():
    pending = option_intent(symbol="APH261009P00125000", strike=Decimal("125"))
    service = LiveOrderService(
        account_id=pending.account_id, instance_id=pending.instance_id,
        snapshot_provider=option_snapshot,
        transport=lambda **kw: broker_ref(pending),
        lifecycle_store=OrderLifecycleStore(InMemoryLifecycleBackend()))
    assert service.submit(pending).accepted
    return service


def test_the_option_snapshot_counts_open_and_pending_put_collateral():
    service = _pending_service()
    ns = _snapshot_ns(service)
    order = option_intent(symbol="APH261009P00120000", strike=Decimal("120"))
    ns["_live_option_quotes"][order.symbol] = {
        "price": Decimal("1.10"), "quote_at": RTH, "fetched_at": RTH}
    snap = ns["_live_option_dependency_snapshot"](_adapter(), order, now_utc=RTH)
    assert isinstance(snap, DependencySnapshot)
    assert (snap.asset_class, snap.regular_session_open, snap.market_open) == (
        "us_option", True, True)
    assert snap.open_short_put_collateral == Decimal("53000")
    assert snap.pending_sell_to_open_collateral == Decimal("12500")
    assert snap.underlying_put_collateral == Decimal("25500")
    assert snap.account_equity == Decimal("100000")
    assert (snap.quote_price, snap.quote, snap.calendar) == (
        Decimal("1.10"), Health.HEALTHY, Health.HEALTHY)
    assert snap.position_quantity == Decimal("0")
    decision = UnifiedOrderGate().evaluate(order, snap)
    assert "option.collateral_insufficient" in decision.reason_codes


def test_a_held_short_is_the_signed_position_and_premarket_is_not_rth():
    ns = _snapshot_ns(_pending_service())
    order = option_intent(symbol="APH261009P00130000")
    premarket = RTH - timedelta(hours=1, minutes=45)       # 09:15 ET
    ns["_live_option_quotes"][order.symbol] = {
        "price": Decimal("1.10"), "quote_at": premarket, "fetched_at": premarket}
    snap = ns["_live_option_dependency_snapshot"](_adapter(), order,
                                                  now_utc=premarket)
    assert snap.position_quantity == Decimal("-1")
    assert snap.regular_session_open is False and snap.market_open is True


def test_incomplete_contract_fields_make_collateral_unknown():
    ns = _snapshot_ns(_pending_service())
    order = option_intent()
    snap = ns["_live_option_dependency_snapshot"](
        _adapter(_option_positions_complete=False), order, now_utc=RTH)
    assert snap.open_short_put_collateral is None
    assert snap.underlying_put_collateral is None
    assert snap.quote is Health.UNKNOWN


def test_an_unreadable_lifecycle_store_makes_pending_collateral_unknown():
    """L3 review M3: unknown pending is None, never 0."""
    class _Unreadable:
        account_id, instance_id = "acct-1", "instance-1"

        @property
        def lifecycle_store(self):
            raise RuntimeError("store down")

    ns = _snapshot_ns(_Unreadable())
    order = option_intent()
    ns["_live_option_quotes"][order.symbol] = {
        "price": Decimal("1.10"), "quote_at": RTH, "fetched_at": RTH}
    snap = ns["_live_option_dependency_snapshot"](_adapter(), order, now_utc=RTH)
    assert snap.pending_sell_to_open_collateral is None
    assert snap.open_short_put_collateral is None
    decision = UnifiedOrderGate().evaluate(order, snap)
    assert "option.collateral_unknown" in decision.reason_codes


def test_a_stale_option_quote_is_unhealthy():
    ns = _snapshot_ns(_pending_service())
    order = option_intent()
    ns["_live_option_quotes"][order.symbol] = {
        "price": Decimal("1.10"), "quote_at": RTH - timedelta(seconds=61),
        "fetched_at": RTH}
    snap = ns["_live_option_dependency_snapshot"](_adapter(), order, now_utc=RTH)
    assert snap.quote is Health.UNHEALTHY


def test_a_stale_positions_flag_makes_positions_unhealthy():
    ns = _snapshot_ns(_pending_service())
    snap = ns["_live_option_dependency_snapshot"](
        _adapter(_positions_stale_since=1.0), option_intent(), now_utc=RTH)
    assert snap.positions is Health.UNHEALTHY


def test_the_equity_provider_dispatches_only_option_intents():
    marker = object()
    ns = extract(
        ("_live_order_dependency_snapshot", "_open_order_idempotency_keys"),
        namespace={
            "datetime": datetime_module,
            "_live_order_dependency_lock": threading.Lock(),
            "_live_order_dependency_state": {"risk_snapshot_id": "risk-1"},
            "_live_risk_state": None, "MODE_LIVE": "live",
            "_live_stock_order_service": None, "instance_id": "instance-1",
            "_live_option_dependency_snapshot":
                lambda adapter, intent: (marker, intent),
            # Fix wave FW-lo-I5: read only when cash is negative.
            "_lane_enabled": lambda strategies, lane: False,
        })
    provider = ns["_live_order_dependency_snapshot"]
    order = option_intent()
    assert provider(SimpleNamespace(), order) == (marker, order)
    equity = provider(SimpleNamespace(_positions={"AAPL": 5}, _market_marks=None,
                                      _positions_stale_since=None, _cash=1000.0,
                                      _instance_id="instance-1"),
                      equity_intent())
    assert isinstance(equity, DependencySnapshot)
    assert equity.asset_class == "us_equity"


def test_the_loop_runs_option_orders_after_the_stock_loop():
    text = source()
    stock_loop = text.index("for symbol in _exec_order:")
    call = text.index("_execute_option_intents(\n", stock_loop)
    snapshot = text.index("## Save portfolio snapshot every loop", stock_loop)
    assert stock_loop < call < snapshot
    guard = text[text.rindex("if (", stock_loop, call):call]
    assert "nexus_option_orders" in guard and "MODE_LIVE" in guard


# --- EB pin: the equity snapshot provider, computed from the PRE-change code --

class _Frozen(datetime_module.datetime):
    """``now`` is pinned so a snapshot is a pure function of its inputs."""

    @classmethod
    def now(cls, tz=None):
        return cls(2026, 9, 24, 13, 31, 5, tzinfo=datetime_module.timezone.utc)


_FROZEN = SimpleNamespace(datetime=_Frozen, timezone=datetime_module.timezone,
                          timedelta=datetime_module.timedelta)
_NOW = _Frozen.now()


def _ago(seconds):
    return _Frozen.fromtimestamp(_NOW.timestamp() - seconds,
                                 datetime_module.timezone.utc)


def _mark(symbol, age, price=88.12):
    return MarketMark(symbol=symbol, price=price, bid=price - 0.01,
                      ask=price + 0.01, bid_size=100, ask_size=100,
                      observed_at=_ago(age), received_at=_ago(age),
                      source=MarkSource.STREAM_QUOTE, feed="iex",
                      quality=MarkQuality.SINGLE_EXCHANGE, session="regular")


def _eb_provider(state, risk_state):
    def never(adapter, intent):
        raise AssertionError("an EB intent reached the option snapshot")

    return extract(
        ("_live_order_dependency_snapshot", "_open_order_idempotency_keys"),
        check=(),
        namespace={
            "datetime": _FROZEN,
            "_live_order_dependency_lock": threading.Lock(),
            "_live_order_dependency_state": state,
            "_live_risk_state": risk_state, "MODE_LIVE": "live",
            "mode": "live", "live_broker_type": "alpaca",
            "live_brokerage_id": "brk-alpaca-main",
            "_live_stock_order_service": None, "instance_id": "alpaca-main",
            "_live_option_dependency_snapshot": never,
        })["_live_order_dependency_snapshot"]


def _plain(value):
    if isinstance(value, datetime_module.datetime):
        return value.isoformat()
    if isinstance(value, datetime_module.timedelta):
        return value.total_seconds()
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, (frozenset, set)):
        return sorted(_plain(v) for v in value)
    if isinstance(value, Decimal):
        return str(value)
    return value


_EB_ORDERS = (
    equity_intent(account_id="brk-alpaca-main", instance_id="alpaca-main",
                  symbol="TQQQ", reason="eb_rebalance"),
    equity_intent(account_id="brk-alpaca-main", instance_id="alpaca-main",
                  symbol="GLD", reason="eb_rotation_trim", side=OrderSide.SELL,
                  quantity=Decimal("3"), reduce_only=True),
    equity_intent(account_id="brk-alpaca-main", instance_id="alpaca-main",
                  symbol="XLE", reason="eb_sweep", quantity=Decimal("0.5")),
)
_EB_STATES = (
    {},
    {"kill_switch": "healthy", "cash": "healthy", "positions": "healthy",
     "calendar": "healthy", "persistence": "healthy", "risk_state": "healthy",
     "watchdog": "healthy", "market_open": True, "risk_snapshot_id": "risk-9",
     "positions_at": _ago(2), "kill_switch_at": _ago(1), "cash_at": _ago(1),
     "calendar_at": _ago(1), "persistence_at": _ago(1),
     "risk_state_at": _ago(1), "watchdog_at": _ago(1)},
    {"positions": "unhealthy", "market_open": False, "cash": "unknown",
     "positions_at": "not-a-time"},
)
_EB_RISK = (None, SimpleNamespace(max_order_notional=Decimal("4200"),
                                  max_leveraged_notional=Decimal("4200"),
                                  max_symbol_notional=Decimal("1200")))


def _eb_adapters():
    for marks, stale, positions in itertools.product(
            ("fresh", "stale", "none", "absent"), (None, 1.0),
            ({"TQQQ": 5.0, "GLD": 3.0}, {})):
        values = {"_positions": dict(positions), "_cash": 6041.43,
                  "_instance_id": "alpaca-main", "_account_id": "PA-1",
                  "_positions_stale_since": stale}
        if marks == "fresh":
            values["_market_marks"] = {s: _mark(s, 1) for s in ("TQQQ", "GLD", "XLE")}
        elif marks == "stale":
            values["_market_marks"] = {s: _mark(s, 3600) for s in ("TQQQ", "GLD", "XLE")}
        elif marks == "none":
            values["_market_marks"] = None
        yield SimpleNamespace(**values)


#: sha256 of 288 EB equity snapshots (3 intents x 3 dependency states x 2
#: risk states x 16 adapters: fresh/stale/no/absent mark books, stale
#: positions or not, a held book or none), computed on the pre-change
#: provider at 1f77306 with now pinned. The option-only pending field is left
#: out: L3 review M3 moved its default from 0 to None one commit earlier.
#: A moved digest is the regression.
EB_SNAPSHOT_DIGEST = (
    "6cf6715b66640588e5ef234b355c4fa83947b3ea887107aa37059f2e6d4a6002")


def test_eb_equity_snapshots_are_byte_identical():
    rows = []
    for state, risk in itertools.product(_EB_STATES, _EB_RISK):
        provider = _eb_provider(dict(state), risk)
        for adapter, order in itertools.product(list(_eb_adapters()), _EB_ORDERS):
            snap = provider(adapter, order)
            rows.append({field.name: _plain(getattr(snap, field.name))
                         for field in dataclasses.fields(snap)
                         if field.name != "pending_sell_to_open_collateral"})
    assert len(rows) == 288
    digest = hashlib.sha256(json.dumps(rows, sort_keys=True,
                                       default=str).encode()).hexdigest()
    assert digest == EB_SNAPSHOT_DIGEST


# --- L4 review fixes (routed to L5): retried order ids, per-order isolation,
# --- honest close-failure alerts, one alert per contract per session ----------

def _scripted_service(*statuses, provider=option_snapshot):
    """A real service whose broker answers each submit with the next status
    (the last one repeats)."""
    calls = []
    order = option_intent()
    script = list(statuses)

    def transport(**kw):
        calls.append(kw)
        status = script.pop(0) if len(script) > 1 else script[0]
        return SimpleNamespace(status=status, broker_order_id=f"b-{len(calls)}",
                               id=f"b-{len(calls)}", filled_qty=0,
                               filled_avg_price=None)

    service = LiveOrderService(
        account_id=order.account_id, instance_id=order.instance_id,
        snapshot_provider=provider, transport=transport,
        lifecycle_store=OrderLifecycleStore(InMemoryLifecycleBackend()))
    return service, calls


def test_a_retried_put_reports_the_key_it_was_sent_under(signal_updates):
    """I-1: the broker cancelled the first put with nothing filled; the same
    decision re-emitted that session goes out under retry ordinal 1. The
    signal, the result and the log carry that key, not the dead one."""
    ns = _executor()
    service, calls = _scripted_service("canceled", "accepted")
    execute = ns["_execute_option_intents"]
    (first,) = execute([dict(ORDER, signal_id="sig-1")], order_service=service,
                       adapter=_Quotes(), now_utc=RTH, risk_snapshot_id="risk-1")
    lines, log = _lines()
    (second,) = execute([dict(ORDER, signal_id="sig-2")], order_service=service,
                        adapter=_Quotes(), now_utc=RTH,
                        risk_snapshot_id="risk-1", log=log)
    assert first["client_order_id"].endswith("-0")
    assert second["status"] == "submitted"
    assert second["client_order_id"].endswith("-1")
    assert calls[1]["client_order_id"] == second["client_order_id"]
    assert signal_updates[-1] == ("sig-2", {
        "status": "submitted", "order_client_id": second["client_order_id"]})
    assert any(second["client_order_id"] in m and c == "green"
               for m, c in lines)


@pytest.mark.parametrize("poison", [
    dict(ORDER, limit_price="abc"),          # decimal.InvalidOperation
    dict(ORDER, qty=float("inf")),           # OverflowError
    dict(ORDER, strike="not-a-strike"),      # InvalidOperation in OrderIntent
])
def test_a_malformed_entry_never_stops_the_buy_to_close_behind_it(poison):
    """I-2: one bad entry costs only itself; the close queued behind it is
    still attempted (here it goes out)."""
    alerts = []
    ns = _executor(alerts)
    service, calls = _service(provider=lambda cur: option_snapshot(
        cur, position_quantity=Decimal("-1")))
    lines, log = _lines()
    bad, btc = ns["_execute_option_intents"](
        [poison, dict(BTC)], order_service=service, adapter=_Quotes(),
        now_utc=RTH, risk_snapshot_id="risk-1", log=log)
    assert bad["status"] == "invalid"
    assert btc["status"] == "submitted" and len(calls) == 1
    assert calls[0]["position_intent"] == "buy_to_close"
    assert alerts == []
    assert any(c == "red" for _m, c in lines)


def test_an_unexpected_failure_costs_one_order_and_the_close_still_alerts():
    """I-2, the per-order net: a service answer nobody expected (here None)
    marks that order "error"; the buy-to-close behind it is attempted and,
    refused, sends its alert."""
    alerts = []
    ns = _executor(alerts)
    answers = [None]

    class _Odd:
        account_id, instance_id = "acct-1", "instance-1"

        def submit(self, intent):
            if answers:
                return answers.pop(0)
            return SimpleNamespace(accepted=False, uncertain=False,
                                   decision=SimpleNamespace(
                                       allowed=False, idempotency_key="k",
                                       reason_codes=("dependency.quote.stale",)))

    lines, log = _lines()
    first, btc = ns["_execute_option_intents"](
        [dict(ORDER), dict(BTC)], order_service=_Odd(), adapter=_Quotes(),
        now_utc=RTH, risk_snapshot_id="risk-1", log=log)
    assert first["status"] == "error"
    assert btc["status"] == "blocked"
    ((title, _message, priority),) = alerts
    assert title == f"AUTO-CLOSE FAILED — {OCC} — CLOSE MANUALLY IMMEDIATELY"
    assert priority == 2
    assert any(c == "red" and "AttributeError" in m for m, c in lines)


def test_one_close_failure_alert_per_contract_per_session():
    """M-3: the wheel re-emits its buy-to-close every tick; the operator hears
    once per contract per New York session, and again the next session."""
    alerts = []
    ns = _executor(alerts)
    service, _calls = _service()        # no short on the book: always blocked
    execute = ns["_execute_option_intents"]
    for minutes in (0, 5, 10, 300):
        execute([dict(BTC)], order_service=service, adapter=_Quotes(
            stamp=RTH + timedelta(minutes=minutes)),
            now_utc=RTH + timedelta(minutes=minutes), risk_snapshot_id="risk-1")
    assert len(alerts) == 1
    tomorrow = RTH + timedelta(days=1)
    execute([dict(BTC)], order_service=service,
            adapter=_Quotes(stamp=tomorrow), now_utc=tomorrow,
            risk_snapshot_id="risk-1")
    assert len(alerts) == 2
    other = dict(BTC, contract="APH261009P00125000", strike=125.0)
    execute([other], order_service=service, adapter=_Quotes(stamp=tomorrow),
            now_utc=tomorrow, risk_snapshot_id="risk-1")
    assert len(alerts) == 3


def test_the_session_follows_new_york_not_utc():
    alerts = []
    ns = _executor(alerts)
    service, _calls = _service()
    execute = ns["_execute_option_intents"]
    evening = datetime_module.datetime(2026, 10, 5, 23, 30,
                                       tzinfo=datetime_module.timezone.utc)
    after_midnight_utc = evening + timedelta(hours=1)   # still Oct 5 in NY
    for now in (evening, after_midnight_utc):
        execute([dict(BTC)], order_service=service, adapter=_Quotes(stamp=now),
                now_utc=now, risk_snapshot_id="risk-1")
    assert len(alerts) == 1


def test_an_unconfirmed_close_then_a_refused_one_both_alert():
    """The two kinds say different things (check open orders, close by hand),
    so a definite refusal after an unknown outcome is still sent."""
    alerts = []
    ns = _executor(alerts)
    unknown = SimpleNamespace(
        account_id="acct-1", instance_id="instance-1",
        submit=lambda intent: SimpleNamespace(
            accepted=False, uncertain=True,
            decision=SimpleNamespace(allowed=True, idempotency_key="k",
                                     reason_codes=())))
    execute = ns["_execute_option_intents"]
    execute([dict(BTC)], order_service=unknown, adapter=_Quotes(), now_utc=RTH,
            risk_snapshot_id="risk-1")
    execute([dict(BTC)], order_service=unknown, adapter=_Quotes(), now_utc=RTH,
            risk_snapshot_id="risk-1")
    blocked, _calls = _service()
    execute([dict(BTC)], order_service=blocked, adapter=_Quotes(), now_utc=RTH,
            risk_snapshot_id="risk-1")
    assert [title.split(" — ")[0] for title, _m, _p in alerts] == [
        "AUTO-CLOSE UNCONFIRMED", "AUTO-CLOSE FAILED"]


def test_an_alert_that_failed_to_send_is_retried_next_tick():
    sent = []
    outcomes = [False, True]
    ns = extract(("_execute_option_intents", "_build_option_intent",
                  "_refresh_option_quote"),
                 assigns=("_live_option_quotes", "_auto_close_alerts"),
                 namespace={
                     "datetime": datetime_module,
                     "_wheel_alert": lambda title, message, *, priority=0: (
                         sent.append(title) or outcomes.pop(0)),
                 })
    service, _calls = _service()
    for _tick in range(3):
        ns["_execute_option_intents"]([dict(BTC)], order_service=service,
                                      adapter=_Quotes(), now_utc=RTH,
                                      risk_snapshot_id="risk-1")
    assert len(sent) == 2


# --- fix wave FW1 item 8 (live-orders review M-4): the shared quote cache ------

def test_an_approval_refreshing_the_same_contract_mid_close_is_no_auto_close_failure():
    """The loop builds a buy_to_close on quote Q1; before its gate read, the
    approval thread refreshes the same contract (Q2, a later quote_at). The
    snapshot used to read Q2, the gate refused quote.timestamp_mismatch, and
    the operator got a false "AUTO-CLOSE FAILED — CLOSE MANUALLY". The
    snapshot now gates the intent on the quote it was built on."""
    alerts = []
    shared = _executor(alerts)
    snapshot_ns = _snapshot_ns(None)
    # One process: the executor, the approval thread and the snapshot
    # provider share ONE _live_option_quotes.
    snapshot_ns["_live_option_quotes"] = shared["_live_option_quotes"]
    adapter = _adapter(_option_positions={OCC: _position(OCC, "APH", 130.0)})
    later = RTH + timedelta(seconds=61)
    approval_adapter = _Quotes(stamp=later, bid=1.4, ask=1.6)

    def provider(intent):
        # The approval thread's refresh lands between the build and the gate.
        shared["_refresh_option_quote"](approval_adapter, OCC, later)
        return snapshot_ns["_live_option_dependency_snapshot"](
            adapter, intent, now_utc=RTH)

    calls = []
    service = LiveOrderService(
        account_id="acct-1", instance_id="instance-1",
        snapshot_provider=provider,
        transport=lambda **kw: calls.append(kw) or SimpleNamespace(
            status="accepted", broker_order_id="b-1", id="b-1", filled_qty=0,
            filled_avg_price=None),
        lifecycle_store=OrderLifecycleStore(InMemoryLifecycleBackend()))
    snapshot_ns["_live_stock_order_service"] = service
    results = shared["_execute_option_intents"](
        [dict(BTC)], order_service=service, adapter=_Quotes(), now_utc=RTH,
        risk_snapshot_id="risk-1")
    assert results[0]["status"] == "submitted", results
    assert len(calls) == 1 and alerts == []
    # The approval's newer quote is the cached one for everything after.
    assert shared["_live_option_quotes"][OCC]["quote_at"] == later


def test_a_quote_the_intent_was_not_built_on_is_still_refused():
    """The earlier quote is used only when it IS the intent's quote."""
    ns = _snapshot_ns(None)
    stale = RTH - timedelta(seconds=30)
    ns["_live_option_quotes"][OCC] = {
        "price": Decimal("1.5"), "quote_at": RTH, "fetched_at": RTH,
        "previous": {"price": Decimal("1.2"), "quote_at": RTH - timedelta(seconds=5),
                     "fetched_at": RTH - timedelta(seconds=5)}}
    intent = option_intent(quote_at=stale)
    snap = ns["_live_option_dependency_snapshot"](_adapter(), intent, now_utc=RTH)
    assert snap.quote_at == RTH
    assert "quote.timestamp_mismatch" in UnifiedOrderGate().evaluate(
        intent, snap).reason_codes
