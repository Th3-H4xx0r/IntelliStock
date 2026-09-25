"""swing-port Task 15: the swing_approval route in _execute_live_command.

This first section pins the manual submit_order path (web/iOS operator
orders on alpaca-main, EB's real-money instance) BEFORE the route exists:
every submit_order whose payload source is not exactly "swing_approval"
must keep its exact old result, intent and client order id."""
import datetime as datetime_module
import hashlib
import json
from decimal import Decimal
from types import SimpleNamespace

import pytest

from live_orders import GateDecision, OrderSide, OrderSource
from live_orders.service import OrderSubmission
from swing_broker_harness import extract

# --- pins: the manual submit_order path ---------------------------------------

_PIN_NOW = datetime_module.datetime(2026, 9, 25, 14, 31, 7,
                                    tzinfo=datetime_module.timezone.utc)
_PIN_MARK_AT = datetime_module.datetime(2026, 9, 25, 14, 31, 0,
                                        tzinfo=datetime_module.timezone.utc)


class _PinDateTime(datetime_module.datetime):
    @classmethod
    def now(cls, tz=None):
        return _PIN_NOW if tz is not None else _PIN_NOW.replace(tzinfo=None)


_PIN_CLOCK = SimpleNamespace(datetime=_PinDateTime,
                             timezone=datetime_module.timezone,
                             timedelta=datetime_module.timedelta,
                             date=datetime_module.date)


class _PinService:
    account_id = "brk-alpaca-main"
    instance_id = "alpaca-main"
    risk_snapshot_id = ""

    def __init__(self, mode="allow"):
        self.mode = mode
        self.intents = []

    def enqueue(self, intent):
        self.intents.append(intent)
        if self.mode == "raise":
            raise RuntimeError("transport down")
        allowed = self.mode == "allow"
        decision = GateDecision(
            allowed=allowed,
            approved_quantity=intent.quantity if allowed else Decimal("0"),
            reason_codes=() if allowed else ("dependency.quote.unknown",),
            idempotency_key=intent.idempotency_key)
        reference = SimpleNamespace(broker_order_id="order-1") if allowed else None
        return OrderSubmission(decision=decision, reference=reference)


class _PinAdapter:
    def __init__(self, marked=True):
        self._positions = {"AAPL": 5.0}
        self._last_prices = {"AAPL": 99.5, "MSFT": 410.0}
        mark = SimpleNamespace(price=100.25, observed_at=_PIN_MARK_AT)
        self._market_marks = SimpleNamespace(
            get=lambda symbol: mark if marked and symbol == "AAPL" else None)

    def submit_order(self, **_kwargs):
        raise AssertionError("a manual order bypassed the unified order service")


_BASE = {"symbol": "AAPL", "side": "buy", "qty": 2, "reason": "operator allocation"}

#: Every non-swing submit_order shape the pin covers. The source key variants
#: are the ones a careless route could swallow.
_PIN_PAYLOADS = [
    _BASE,
    dict(_BASE, source="manual"),
    dict(_BASE, source="strategy_eb"),
    dict(_BASE, source="SWING_APPROVAL"),
    dict(_BASE, source=" swing_approval"),
    dict(_BASE, source="swing_approval "),
    dict(_BASE, source=None),
    dict(_BASE, source=""),
    dict(_BASE, source=["swing_approval"]),
    dict(_BASE, signal_id="sig-1"),
    dict(_BASE, source="manual", signal_id="sig-1"),
    {"symbol": "aapl", "side": "SELL", "qty": "1.5"},
    {"symbol": "AAPL", "side": "sell", "qty": 1, "reduce_only": False},
    {"symbol": "AAPL", "side": "buy", "notional": 250},
    {"symbol": "MSFT", "side": "buy", "notional": 820},
    {"symbol": "TSLA", "side": "buy", "notional": 100},
    {"symbol": "AAPL", "side": "buy", "qty": 3, "order_type": "limit",
     "limit_price": 99.1, "tif": "gtc"},
    {"symbol": "AAPL", "side": "buy", "qty": 3, "order_type": "limit",
     "limit_price": 99.1, "tif": "day", "extended_hours": True},
    {"symbol": "AAPL", "side": "buy", "qty": 3, "extended_hours": True},
    {"symbol": "AAPL", "side": "buy", "qty": 3, "order_type": "limit"},
    {"symbol": "AAPL", "side": "buy", "qty": 3, "order_type": "limit",
     "limit_price": 0},
    {"symbol": "AAPL", "side": "buy", "qty": 3, "order_type": "stop"},
    {"symbol": "AAPL", "side": "buy", "qty": 3, "tif": "opg"},
    {"symbol": "AAPL", "side": "buy", "qty": "many"},
    {"symbol": "AAPL", "side": "buy", "notional": "lots"},
    {"symbol": "AAPL", "side": "buy"},
    {"symbol": "", "side": "buy", "qty": 1},
    {"symbol": "AAPL", "qty": 1},
    {"symbol": "AAPL", "side": "hold", "qty": 1},
    {"side": "buy", "qty": 1, "source": "manual"},
    {},
]


def _pin_execute(namespace_extra=None):
    ns = {"datetime": _PIN_CLOCK, "instance_id": "alpaca-main",
          "get_conn_retry": lambda **kw: None}
    ns.update(namespace_extra or {})
    return extract(("_execute_live_command",), check=(),
                   namespace=ns)["_execute_live_command"]


def _intent_row(intent):
    return {name: str(getattr(intent, name)) for name in (
        "account_id", "instance_id", "source", "reason", "symbol", "side",
        "quantity", "reduce_only", "decision_at", "quote_at",
        "risk_snapshot_id", "order_type", "limit_price", "tif",
        "extended_hours", "reference_price", "asset_class", "order_class",
        "idempotency_key")}


def _pin_rows(execute):
    rows = []
    for index, payload in enumerate(_PIN_PAYLOADS):
        for mode in ("allow", "deny", "raise", "none"):
            for marked in (True, False):
                service = None if mode == "none" else _PinService(mode)
                ok, error, result = execute(
                    _PinAdapter(marked),
                    {"type": "submit_order", "payload": dict(payload)}, service)
                rows.append({
                    "case": [index, mode, marked], "ok": ok, "error": error,
                    "result": {k: str(v) for k, v in sorted(result.items())},
                    "intents": [_intent_row(i) for i in
                                (service.intents if service else [])]})
    return rows


def _digest(rows):
    return hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()


#: Computed from the pre-route _execute_live_command (HEAD 205bfe9).
MANUAL_SUBMIT_DIGEST = "2211fb0798e44a06cbfb9b6e1af771406bd97ac28f87192051229254177439fa"


def test_every_non_swing_submit_order_keeps_its_exact_old_outcome():
    routed = []
    execute = _pin_execute({"_execute_swing_approval":
                            lambda *a, **k: routed.append(a) or (True, "", {})})
    rows = _pin_rows(execute)
    assert routed == []
    assert len(rows) == len(_PIN_PAYLOADS) * 8
    assert _digest(rows) == MANUAL_SUBMIT_DIGEST


def test_a_manual_buy_still_builds_the_same_manual_intent():
    service = _PinService()
    ok, error, result = _pin_execute()(
        _PinAdapter(), {"type": "submit_order",
                        "payload": dict(_BASE, source="manual", signal_id="s")},
        service)
    assert (ok, error) == (True, "")
    (intent,) = service.intents
    assert (intent.source, intent.side, intent.quantity, intent.reduce_only,
            intent.order_type, intent.tif, intent.order_class,
            intent.reason, intent.reference_price, intent.quote_at) == (
        OrderSource.MANUAL, OrderSide.BUY, Decimal("2"), False, "market",
        "day", None, "operator allocation", Decimal("100.25"), _PIN_MARK_AT)
    assert result == {
        "symbol": "AAPL", "side": "buy", "qty": 2.0, "notional": None,
        "order_type": "market", "limit_price": None, "tif": "day",
        "extended_hours": False, "order_id": "order-1",
        "client_order_id": intent.idempotency_key}


@pytest.mark.parametrize("source", ["SWING_APPROVAL", " swing_approval",
                                    None, "manual", ["swing_approval"]])
def test_a_payload_that_is_not_exactly_swing_approval_takes_the_manual_path(source):
    ok, error, _result = _pin_execute()(
        _PinAdapter(), {"type": "submit_order",
                        "payload": {"source": source, "signal_id": "s"}},
        _PinService())
    assert (ok, error) == (False, "submit_order: side must be buy or sell")
