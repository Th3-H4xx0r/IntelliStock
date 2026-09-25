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


# --- the swing_approval handler (spec 6.1 broker item 9; interfaces §7) -------
# swing_trader (plan B) is stubbed through sys.modules; plan B Task 20 runs
# the same extraction against the real modules.

import sys  # noqa: E402
import threading  # noqa: E402
import types  # noqa: E402

from broker_adapters.base import OptionSnapshotDTO  # noqa: E402
from swing_broker_harness import function_source  # noqa: E402
from swing_live_fixtures import OCC, RTH  # noqa: E402
from swing_trader import constants as swing_constants  # noqa: E402

LANES = [{"strategy": "strategy_swing", "config": {"strategy_swing_enabled": True}},
         {"strategy": "strategy_wheel", "config": {"strategy_wheel_enabled": True}}]
BOOK_UNREADABLE_REASON = "broker order book unreadable — approve again"
SWING_ORDER = {"kind": "equity_bracket", "symbol": "AAPL", "qty": 6,
               "take_profit_price": 109.0, "stop_loss_price": 94.0}
WHEEL_ORDER = {"kind": "option", "underlying": "APH", "contract": OCC,
               "option_type": "put", "strike": 130.0, "expiry": "2026-10-09",
               "position_intent": "sell_to_open", "qty": 1,
               "order_type": "limit", "limit_price": 1.2, "tif": "day",
               "reason": "wheel_sto_put", "signal_id": "sig-1",
               "session": "2026-10-05"}


class _BookUnreadable(ValueError):
    pass


@pytest.fixture
def swing(monkeypatch):
    """Stubbed plan-B modules over one in-memory signal row set. cas_signal
    and update_signal apply to the rows the way the store does, so a
    redelivered command reads what the first one wrote."""
    state = SimpleNamespace(rows={}, updates=[], cas=[], built=[], notices=[],
                            events=[], cas_wins=True, cas_raises=None,
                            build_raises=None, order=None, reads=0,
                            read_failures=0, pauses=[], update_raises_for=None)

    def build_approved_order(signal, *, live_price, equity, cfg, adapter=None,
                             today=None):
        state.events.append("build")
        state.built.append({"signal": dict(signal), "live_price": live_price,
                            "equity": equity, "today": today, "cfg": cfg})
        if state.build_raises is not None:
            raise state.build_raises
        if state.order is not None:
            return dict(state.order)
        return dict(SWING_ORDER if signal["lane"] == "swing" else WHEEL_ORDER)

    def get_signal(signal_id):
        state.reads += 1
        if state.read_failures:
            state.read_failures -= 1
            raise ConnectionError("signals table unreachable")
        row = state.rows.get(signal_id)
        return dict(row) if row is not None else None

    def update_signal(signal_id, patch):
        if (state.update_raises_for is not None
                and patch.get("status") == state.update_raises_for):
            raise ConnectionError("signals table unreachable")
        state.events.append(("update", patch.get("status")))
        state.updates.append((signal_id, dict(patch)))
        state.rows[signal_id].update(patch)

    def cas_signal(signal_id, *, expect_status, doc):
        state.events.append("cas")
        state.cas.append((signal_id, expect_status, dict(doc)))
        if state.cas_raises is not None:
            raise state.cas_raises
        if not state.cas_wins or state.rows[signal_id]["status"] != expect_status:
            return False
        state.rows[signal_id] = dict(doc)
        return True

    def notify_swing_approval_failed(instance_id, *, symbol, lane, reason):
        state.notices.append({"instance_id": instance_id, "symbol": symbol,
                              "lane": lane, "reason": reason})

    approvals = types.ModuleType("swing_trader.approvals")
    approvals.build_approved_order = build_approved_order
    approvals.BookUnreadable = _BookUnreadable
    store = types.ModuleType("swing_trader.signals_store")
    store.get_signal = get_signal
    store.update_signal = update_signal
    store.cas_signal = cas_signal
    notify = types.ModuleType("swing_trader.notify")
    notify.notify_swing_approval_failed = notify_swing_approval_failed
    package = types.ModuleType("swing_trader")
    package.__path__ = []
    package.approvals, package.signals_store = approvals, store
    package.notify, package.constants = notify, swing_constants
    for name, module in (("swing_trader", package),
                         ("swing_trader.approvals", approvals),
                         ("swing_trader.signals_store", store),
                         ("swing_trader.notify", notify),
                         ("swing_trader.constants", swing_constants)):
        monkeypatch.setitem(sys.modules, name, module)
    return state


def _signal(**changes):
    values = {"id": "sig-1", "instance_id": "instance-1", "lane": "swing",
              "symbol": "AAPL", "status": "approved", "decided_by": "pranav",
              "decided_at": "2026-10-05T14:59:00+00:00",
              "decision_reason": "looks fine", "order_client_id": None}
    values.update(changes)
    return values


class _Service:
    account_id = "acct-1"
    instance_id = "instance-1"

    def __init__(self, mode="allow", swing=None,
                 codes=("exposure.max_order_notional",)):
        self.mode = mode
        self.swing = swing
        self.codes = tuple(codes)
        self.intents = []
        self.status_at_enqueue = []

    def enqueue(self, intent):
        self.intents.append(intent)
        if self.swing is not None:
            self.swing.events.append("enqueue")
            self.status_at_enqueue.append(
                (self.swing.rows.get("sig-1") or {}).get("status"))
        if self.mode == "raise":
            raise RuntimeError("socket closed after send")
        allowed = self.mode != "deny"
        key = intent.idempotency_key
        if self.mode == "escalate":
            key = key[:-2] + "-1"
        decision = GateDecision(
            allowed=allowed,
            approved_quantity=intent.quantity if allowed else Decimal("0"),
            reason_codes=() if allowed else self.codes,
            idempotency_key=key)
        if self.mode == "uncertain":
            return OrderSubmission(decision=decision, uncertain=True)
        reference = SimpleNamespace(broker_order_id="b-1") if allowed else None
        return OrderSubmission(decision=decision, reference=reference)


class _Adapter:
    def __init__(self, *, mark_price=100.5, trades=None, snapshots=True,
                 equity=60000.0, snapshots_raise=False):
        self._account_equity = equity
        self.snapshots_raise = snapshots_raise
        self.rest = []
        self.trades = trades or {}
        self.snapshots = snapshots
        mark = (SimpleNamespace(price=mark_price, observed_at=RTH)
                if mark_price is not None else None)
        self._market_marks = SimpleNamespace(get=lambda symbol: mark)

    def fetch_rest_quote_marks(self, symbols):
        self.rest.append(list(symbols))
        return tuple(symbols)

    def get_latest_trades(self, symbols):
        return {s: self.trades[s] for s in symbols if s in self.trades}

    def refresh_account(self):
        raise ConnectionError("account endpoint 503")

    def get_option_snapshots(self, contracts):
        if self.snapshots_raise:
            raise ConnectionError("options data 503")
        if not self.snapshots:
            return {}
        return {c: OptionSnapshotDTO(c, 1.1, 1.3, 1.2, None, None, None, None,
                                     None, RTH.isoformat()) for c in contracts}


def _extract_handler(extra=None):
    """The extraction plan B Task 20 repeats: same functions, assigns,
    namespace and free-name check."""
    namespace = {"datetime": datetime_module,
                 "_live_order_dependency_lock": threading.Lock(),
                 "_live_order_dependency_state": {"risk_snapshot_id": "risk-9"}}
    namespace.update(extra or {})
    return extract(
        ("_execute_swing_approval", "_lane_config", "_approval_live_price",
         "_build_bracket_intent", "_build_option_intent",
         "_refresh_option_quote", "_truthy", "_merged_strategy_settings"),
        assigns=("_live_option_quotes", "_LANE_ENABLE_FLAGS"),
        namespace=namespace,
        check=("_execute_swing_approval", "_lane_config",
               "_approval_live_price"))


def _run(swing, service=None, *, lanes=LANES, adapter=None, payload=None,
         logs=None, extra=None):
    service = service or _Service(swing=swing)
    log = (lambda message, color="white": logs.append((color, message))) \
        if logs is not None else None
    result = _extract_handler(extra)["_execute_swing_approval"](
        adapter or _Adapter(),
        payload or {"source": "swing_approval", "signal_id": "sig-1"},
        service, cached_strategies=lanes, now_utc=RTH, log=log,
        sleep=swing.pauses.append)
    return service, result


def test_a_swing_approval_places_a_manual_bracket_at_the_live_price(swing):
    swing.rows["sig-1"] = _signal()
    service, (ok, error, result) = _run(swing)
    assert ok is True and error == ""
    (intent,) = service.intents
    assert (intent.source, intent.side, intent.quantity, intent.order_class,
            intent.tif, intent.order_type) == (
        OrderSource.MANUAL, OrderSide.BUY, Decimal("6"), "bracket", "gtc",
        "market")
    assert (intent.take_profit_price, intent.stop_loss_price) == (
        Decimal("109.00"), Decimal("94.00"))
    assert intent.reason == "swing_approval:sig-1" and intent.quote_at == RTH
    assert intent.reference_price == Decimal("100.5")
    assert intent.risk_snapshot_id == "risk-9"
    assert swing.built[0]["live_price"] == 100.5
    assert swing.built[0]["equity"] == 60000.0
    assert swing.built[0]["today"] == datetime_module.date(2026, 10, 5)
    # ruling 4 (plan B G5): the key the service used and the rebuilt order.
    assert swing.updates == [("sig-1", {"status": "submitted",
                                        "order_client_id": intent.idempotency_key,
                                        "submitted_order": SWING_ORDER})]
    assert result == {"signal_id": "sig-1",
                      "client_order_id": intent.idempotency_key,
                      "order_id": "b-1", "reason_codes": []}
    assert swing.notices == []


def test_a_wheel_approval_places_a_manual_sell_to_open(swing):
    swing.rows["sig-1"] = _signal(lane="wheel", symbol="APH")
    service, (ok, error, _result) = _run(swing)
    assert ok is True and error == ""
    (intent,) = service.intents
    assert (intent.symbol, intent.position_intent, intent.source,
            intent.contract_multiplier, intent.side, intent.quantity,
            intent.limit_price, intent.asset_class, intent.quote_at) == (
        OCC, "sell_to_open", OrderSource.MANUAL, 100, OrderSide.SELL,
        Decimal("1"), Decimal("1.2"), "us_option", RTH)
    assert swing.rows["sig-1"]["submitted_order"]["contract"] == OCC
    assert swing.rows["sig-1"]["status"] == "submitted"


# ruling 1 (plan B G8a): only an approved signal, claimed before submitting.

@pytest.mark.parametrize("status", ["pending", "submitted", "failed",
                                    "rejected", "auto_approved", "ai_rejected",
                                    None])
def test_a_signal_that_is_not_approved_is_refused_untouched(swing, status):
    swing.rows["sig-1"] = _signal(status=status)
    service, (ok, error, _result) = _run(swing)
    assert ok is False and "not approved" in error
    assert (service.intents, swing.cas, swing.updates, swing.built,
            swing.notices) == ([], [], [], [], [])


def test_another_instances_signal_is_refused_untouched(swing):
    swing.rows["sig-1"] = _signal(instance_id="alpaca-main")
    service, (ok, error, _result) = _run(swing)
    assert ok is False and "another instance" in error
    assert (service.intents, swing.cas, swing.updates, swing.notices) == (
        [], [], [], [])


def test_an_unknown_signal_places_nothing(swing):
    service, (ok, error, _result) = _run(swing)
    assert ok is False and "unknown signal" in error and service.intents == []


def test_a_missing_signal_id_or_service_places_nothing(swing):
    swing.rows["sig-1"] = _signal()
    _service, missing_id = _run(swing, payload={"source": "swing_approval"})
    handler = _extract_handler()["_execute_swing_approval"]
    no_service = handler(_Adapter(), {"source": "swing_approval",
                                      "signal_id": "sig-1"}, None,
                         cached_strategies=LANES, now_utc=RTH)
    assert missing_id[0] is False and "signal_id" in missing_id[1]
    assert no_service[0] is False and "unavailable" in no_service[1]
    assert swing.cas == [] and swing.updates == []


def test_the_signal_is_claimed_approved_to_submitted_before_the_order_goes_out(swing):
    swing.rows["sig-1"] = _signal()
    service, (ok, _error, _result) = _run(swing)
    assert ok is True
    ((signal_id, expect, doc),) = swing.cas
    assert (signal_id, expect, doc["status"]) == ("sig-1", "approved", "submitted")
    assert doc["decided_by"] == "pranav" and doc["decided_at"]
    assert swing.events.index("cas") < swing.events.index("enqueue")
    assert service.status_at_enqueue == ["submitted"]


def test_a_lost_claim_places_nothing_and_writes_nothing(swing):
    swing.rows["sig-1"] = _signal()
    swing.cas_wins = False
    logs = []
    service, (ok, error, _result) = _run(swing, logs=logs)
    assert ok is False and "claimed by another command" in error
    assert (service.intents, swing.built, swing.updates, swing.notices) == (
        [], [], [], [])
    assert any("claimed it first" in message for _color, message in logs)


def test_an_unreadable_claim_places_nothing_and_tells_the_operator(swing):
    swing.rows["sig-1"] = _signal()
    swing.cas_raises = ConnectionError("database down")
    service, (ok, error, _result) = _run(swing)
    assert ok is False and "could not be claimed" in error
    assert (service.intents, swing.built, swing.updates) == ([], [], [])
    assert len(swing.notices) == 1
    assert len(swing.cas) == 1 and swing.pauses == []   # the claim is never retried


def test_a_redelivered_command_places_nothing_the_second_time(swing):
    swing.rows["sig-1"] = _signal()
    service = _Service(swing=swing)
    _run(swing, service)
    _service, (ok, error, _result) = _run(swing, service)
    assert ok is False and "submitted, not approved" in error
    assert len(service.intents) == 1


def test_approve_half_rebuilds_from_the_half_status_and_claims_it(swing):
    swing.rows["sig-1"] = _signal(status="approved_half")
    _service, (ok, _error, _result) = _run(swing)
    assert ok is True
    assert swing.built[0]["signal"]["status"] == "approved_half"
    assert swing.cas[0][1] == "approved_half"


# ruling 2 (plan B G5 review): a transient book outage keeps the approval.

def test_an_unreadable_book_puts_the_signal_back_to_pending(swing):
    swing.rows["sig-1"] = _signal(lane="wheel", symbol="APH")
    swing.build_raises = _BookUnreadable("positions endpoint 503")
    service, (ok, error, _result) = _run(swing)
    assert ok is False and "approve again" in error
    assert service.intents == []
    assert swing.updates == [("sig-1", {"status": "pending", "decided_by": None,
                                        "decided_at": None,
                                        "decision_reason": None})]
    assert swing.notices == [{"instance_id": "instance-1", "symbol": "APH",
                              "lane": "wheel", "reason": BOOK_UNREADABLE_REASON}]
    assert swing.rows["sig-1"]["status"] == "pending"


# T15 fix round 1, I-1: a transient failure returns the signal to pending.

_PENDING = {"status": "pending", "decided_by": None, "decided_at": None,
            "decision_reason": None}


@pytest.mark.parametrize("signal,adapter,why,after_the_open", [
    (_signal(), _Adapter(mark_price=None), "no live price for AAPL", True),
    (_signal(), _Adapter(equity=None),
     "account equity unreadable (ConnectionError: account endpoint 503)", False),
    (_signal(lane="wheel", symbol="APH"), _Adapter(snapshots=False),
     f"no usable options snapshot for {OCC}", True),
    (_signal(lane="wheel", symbol="APH"), _Adapter(snapshots_raise=True),
     f"no usable options snapshot for {OCC} (ConnectionError)", True),
])
def test_a_transient_read_failure_returns_the_signal_to_pending(
        swing, signal, adapter, why, after_the_open):
    swing.rows["sig-1"] = signal
    service, (ok, error, _result) = _run(swing, adapter=adapter)
    reason = why + " — approve again" + (" after the open" if after_the_open else "")
    assert ok is False and error == reason and service.intents == []
    assert swing.updates == [("sig-1", _PENDING)]
    assert swing.rows["sig-1"]["status"] == "pending"
    assert swing.notices == [{"instance_id": "instance-1",
                              "symbol": signal["symbol"],
                              "lane": signal["lane"], "reason": reason}]


@pytest.mark.parametrize("codes,after_the_open", [
    (("quote.stale",), True),
    (("dependency.quote.unknown",), True),
    (("positions.stale",), False),
    (("dependency.cash.stale", "dependency.watchdog.unhealthy"), False),
    (("dependency.positions.unhealthy", "quote.stale", "positions.stale"), True),
    # T15 fix round 1b: a market-hours refusal is "not now", like a quote.
    (("market.closed",), True),
    (("market.regular_hours_required",), True),
    (("market.closed", "quote.stale"), True),
    (("market.regular_hours_required", "positions.stale"), True),
])
def test_a_gate_refusal_on_transient_codes_returns_the_signal_to_pending(
        swing, codes, after_the_open):
    swing.rows["sig-1"] = _signal()
    service, (ok, error, result) = _run(swing, _Service("deny", swing, codes))
    reason = ("order gate blocked: " + ",".join(codes) + " — approve again"
              + (" after the open" if after_the_open else ""))
    assert ok is False and error == reason
    assert swing.updates == [("sig-1", _PENDING)]
    assert swing.notices[0]["reason"] == reason
    assert result["reason_codes"] == list(codes)


@pytest.mark.parametrize("codes", [
    ("exposure.max_order_notional",),
    ("exposure.max_position_quantity",),
    ("cash.insufficient",),
    ("idempotency.open_order_exists",),
    ("idempotency.terminal_requires_retry",),
    ("broker.rejected.APIError",),
    ("option.collateral_insufficient",),
    ("market.closed", "exposure.max_order_notional"),
    ("quote.stale", "exposure.max_order_notional"),
    ("positions.stale", "idempotency.open_order_exists"),
    (),
])
def test_a_gate_refusal_with_any_lasting_code_stays_failed(swing, codes):
    swing.rows["sig-1"] = _signal()
    service, (ok, error, _result) = _run(swing, _Service("deny", swing, codes))
    (intent,) = service.intents
    assert ok is False and error == "order gate blocked: " + ",".join(codes)
    assert swing.updates == [("sig-1", {"status": "failed",
                                        "order_client_id": intent.idempotency_key})]
    assert swing.notices[0]["reason"] == error


def test_the_book_unreadable_reason_is_unchanged(swing):
    swing.rows["sig-1"] = _signal(lane="wheel", symbol="APH")
    swing.build_raises = _BookUnreadable("positions endpoint 503")
    _service, (_ok, error, _result) = _run(swing)
    assert error == BOOK_UNREADABLE_REASON + " (positions endpoint 503)"
    assert swing.notices[0]["reason"] == BOOK_UNREADABLE_REASON


def test_a_reset_that_did_not_land_never_says_approve_again(swing):
    """M-1: the pending notice goes out only when the reset was written."""
    swing.rows["sig-1"] = _signal()
    swing.update_raises_for = "pending"
    logs = []
    _service, (ok, _error, _result) = _run(swing, adapter=_Adapter(mark_price=None),
                                           logs=logs)
    assert ok is False and swing.updates == []
    assert swing.rows["sig-1"]["status"] == "submitted"
    ((notice),) = swing.notices
    assert "approve again" not in notice["reason"]
    assert "could not be put back to pending" in notice["reason"]
    assert any(color == "red" and "could not be put back" in message
               for color, message in logs)


def test_a_transient_reset_then_a_re_approval_places_exactly_one_order(swing):
    """After the reset the operator's re-approval takes the normal path (the
    route's decide + compare-and-swap) and the handler places one order."""
    swing.rows["sig-1"] = _signal()
    refused = _Service("deny", swing, ("quote.stale",))
    _run(swing, refused)
    assert swing.rows["sig-1"]["status"] == "pending"
    swing.rows["sig-1"].update(status="approved", decided_by="pranav",
                               decided_at="2026-10-05T15:05:00+00:00")
    service = _Service(swing=swing)
    _service, (ok, _error, _result) = _run(swing, service)
    assert ok is True and len(service.intents) == 1
    assert swing.rows["sig-1"]["status"] == "submitted"
    assert [c[1] for c in swing.cas] == ["approved", "approved"]
    again = _run(swing, service)[1]
    assert again[0] is False and len(service.intents) == 1


# T15 fix round 1, I-2: the signal read is tried three times.

def test_a_signal_read_that_fails_twice_is_retried_and_placed(swing):
    swing.rows["sig-1"] = _signal()
    swing.read_failures = 2
    service, (ok, _error, _result) = _run(swing)
    assert ok is True and len(service.intents) == 1
    assert swing.reads == 3 and swing.pauses == [1.0, 1.0]


def test_a_signal_that_cannot_be_read_is_reported_and_nothing_is_placed(swing):
    swing.rows["sig-1"] = _signal()
    swing.read_failures = 5
    logs = []
    service, (ok, error, _result) = _run(swing, logs=logs)
    assert ok is False and "could not be read" in error
    assert swing.reads == 3 and swing.pauses == [1.0, 1.0]
    assert (service.intents, swing.cas, swing.updates) == ([], [], [])
    assert any(color == "red" and "sig-1" in message for color, message in logs)
    ((notice),) = swing.notices
    assert "sig-1" in notice["symbol"] and notice["instance_id"] == "instance-1"


def test_an_unknown_signal_is_not_retried(swing):
    _service, (ok, error, _result) = _run(swing)
    assert ok is False and "unknown signal" in error
    assert swing.reads == 1 and swing.pauses == []


def test_the_re_approval_round_trip_through_the_real_store_and_service(store,
                                                                      monkeypatch):
    """I-1 end to end with plan B's real approvals and signals_store and a real
    LiveOrderService and gate: the gate's transient refusal leaves no
    lifecycle row, the signal is pending, the route's re-approval is final
    through the same compare-and-swap, and exactly one order reaches the
    transport."""
    from live_orders import (
        Health,
        InMemoryLifecycleBackend,
        LiveOrderService,
        OrderLifecycleStore,
    )
    from swing_live_fixtures import equity_snapshot
    from swing_trader import approvals, notify, signals_store

    monkeypatch.setattr(signals_store, "store", store)
    notices = []
    monkeypatch.setattr(notify, "notify_swing_approval_failed",
                        lambda *a, **k: notices.append(k))
    doc = signals_store.new_signal(
        instance_id="instance-1", lane="swing", symbol="AAPL",
        session="2026-10-02", score=62, recommendation="review", reasoning="r",
        key_risks=[], size_adjustment=1.0,
        proposal={"entry": 98.0, "stop": 92.12, "target": 106.82, "shares": 76},
        status="pending")
    signals_store.insert_signal(doc)
    sid = doc["id"]

    def operator():
        current = signals_store.get_signal(sid)
        decided = approvals.decide(current, "approve", "pranav", None,
                                   RTH.isoformat())
        assert signals_store.cas_signal(sid, expect_status="pending", doc=decided)

    quote = {"health": Health.UNKNOWN}
    sent = []
    service = LiveOrderService(
        account_id="acct-1", instance_id="instance-1",
        snapshot_provider=lambda intent: equity_snapshot(
            intent, quote=quote["health"], quote_price=Decimal("100.5")),
        transport=lambda **kw: sent.append(kw) or SimpleNamespace(
            status="accepted", broker_order_id="b-1", id="b-1",
            filled_qty=0, filled_avg_price=None),
        lifecycle_store=OrderLifecycleStore(InMemoryLifecycleBackend()))
    handler = _extract_handler()["_execute_swing_approval"]

    def command():
        return handler(_Adapter(), {"source": "swing_approval", "signal_id": sid},
                       service, cached_strategies=LANES, now_utc=RTH,
                       sleep=lambda seconds: None)

    operator()
    ok, error, _result = command()
    assert ok is False and "dependency.quote.unknown" in error
    row = signals_store.get_signal(sid)
    assert (row["status"], row["decided_by"], row["decided_at"]) == (
        "pending", None, None)
    assert list(service.lifecycle_store.list_for_instance("instance-1")) == []
    assert sent == [] and len(notices) == 1
    assert notices[0]["reason"].endswith("approve again after the open")

    quote["health"] = Health.HEALTHY
    operator()
    ok, error, result = command()
    assert (ok, error) == (True, ""), error
    (record,) = service.lifecycle_store.list_for_instance("instance-1")
    assert len(sent) == 1 and sent[0]["client_order_id"] == record.client_order_id
    row = signals_store.get_signal(sid)
    assert (row["status"], row["order_client_id"]) == (
        "submitted", record.client_order_id)
    assert command()[0] is False and len(sent) == 1


# ruling 3 (plan C final review P1): every definite failure is failed + told.

def test_a_rebuild_failure_marks_the_signal_failed_and_tells_the_operator(swing):
    swing.rows["sig-1"] = _signal()
    swing.build_raises = ValueError("stale proposal")
    service, (ok, error, _result) = _run(swing)
    assert ok is False and "stale proposal" in error and service.intents == []
    assert swing.updates == [("sig-1", {"status": "failed",
                                        "order_client_id": None})]
    assert swing.notices == [{"instance_id": "instance-1", "symbol": "AAPL",
                              "lane": "swing", "reason": "stale proposal"}]


def test_a_gate_refusal_marks_the_signal_failed_and_tells_the_operator(swing):
    swing.rows["sig-1"] = _signal()
    service, (ok, error, result) = _run(swing, _Service("deny", swing))
    assert ok is False and error == "order gate blocked: exposure.max_order_notional"
    (intent,) = service.intents
    assert swing.updates == [("sig-1", {"status": "failed",
                                        "order_client_id": intent.idempotency_key})]
    assert swing.notices[0]["reason"] == (
        "order gate blocked: exposure.max_order_notional")
    assert result["reason_codes"] == ["exposure.max_order_notional"]


@pytest.mark.parametrize("signal,lanes,adapter,needle", [
    (_signal(lane="scalp"), LANES, None, "unknown lane"),
    (_signal(), [], None, "not enabled"),
    (_signal(), [{"strategy": "strategy_swing",
                  "config": {"strategy_swing_enabled": "false"}}], None,
     "not enabled"),
])
def test_a_claimed_signal_that_cannot_be_placed_is_failed_and_told(
        swing, signal, lanes, adapter, needle):
    swing.rows["sig-1"] = signal
    service, (ok, error, _result) = _run(swing, lanes=lanes, adapter=adapter)
    assert ok is False and needle in error and service.intents == []
    assert swing.updates == [("sig-1", {"status": "failed",
                                        "order_client_id": None})]
    assert len(swing.notices) == 1 and needle in swing.notices[0]["reason"]


def test_a_bracket_the_live_price_does_not_straddle_is_failed_and_told(swing):
    swing.rows["sig-1"] = _signal()
    swing.order = dict(SWING_ORDER, stop_loss_price=101.0)
    service, (ok, error, _result) = _run(swing)
    assert ok is False and "bracket refused" in error and service.intents == []
    assert swing.rows["sig-1"]["status"] == "failed"
    assert len(swing.notices) == 1


def test_a_submit_that_raised_stays_submitted_and_is_not_told_as_failed(swing):
    swing.rows["sig-1"] = _signal()
    logs = []
    service, (ok, error, result) = _run(swing, _Service("raise", swing),
                                        logs=logs)
    assert ok is False and "outcome unknown" in error
    assert swing.rows["sig-1"]["status"] == "submitted"
    assert swing.updates == [("sig-1", {"submitted_order": SWING_ORDER})]
    assert swing.notices == []
    assert any(color == "red" and "reconcile" in message
               for color, message in logs)


def test_an_uncertain_outcome_stays_submitted_with_its_key(swing):
    swing.rows["sig-1"] = _signal()
    service, (ok, error, _result) = _run(swing, _Service("uncertain", swing))
    (intent,) = service.intents
    assert ok is False and "outcome unknown" in error
    assert swing.updates == [("sig-1", {"order_client_id": intent.idempotency_key,
                                        "submitted_order": SWING_ORDER})]
    assert swing.rows["sig-1"]["status"] == "submitted" and swing.notices == []


def test_the_key_written_back_is_the_one_the_service_used(swing):
    swing.rows["sig-1"] = _signal()
    service, (ok, _error, result) = _run(swing, _Service("escalate", swing))
    (intent,) = service.intents
    used = intent.idempotency_key[:-2] + "-1"
    assert ok is True and used != intent.idempotency_key
    assert swing.rows["sig-1"]["order_client_id"] == used
    assert result["client_order_id"] == used


def test_a_notice_that_raises_never_costs_the_write_back(swing, monkeypatch):
    swing.rows["sig-1"] = _signal()
    swing.build_raises = ValueError("stale proposal")

    def broken(*_args, **_kwargs):
        raise RuntimeError("outbox down")
    monkeypatch.setattr(sys.modules["swing_trader.notify"],
                        "notify_swing_approval_failed", broken)
    _service, (ok, error, _result) = _run(swing)
    assert ok is False and "stale proposal" in error
    assert swing.rows["sig-1"]["status"] == "failed"


# ruling 5: the L4 builders, never a second copy of the order building.

def test_the_handler_builds_orders_only_through_the_l4_builders():
    body = function_source("_execute_swing_approval")
    assert "_build_bracket_intent(" in body and "_build_option_intent(" in body
    assert "_refresh_option_quote(" in body
    assert "OrderIntent(" not in body


# ruling 6: the lane config carries the lane's defaults under the document's.

def test_the_lane_config_merges_the_lane_defaults_under_the_document():
    lane_config = _extract_handler()["_lane_config"]
    doc = [{"strategy": "strategy_swing", "conditions": {"profit_target": 0.12},
            "config": {"strategy_swing_enabled": True, "stop_loss": 0.05}},
           {"strategy": "strategy_wheel",
            "config": {"strategy_wheel_enabled": "true", "strike_atr_mult": 2}}]
    swing_cfg = lane_config(doc, "strategy_swing")
    wheel_cfg = lane_config(doc, "strategy_wheel")
    assert set(swing_constants.SWING_DEFAULTS) <= set(swing_cfg)
    assert (swing_cfg["stop_loss"], swing_cfg["profit_target"],
            swing_cfg["position_size_pct"]) == (
        0.05, 0.12, swing_constants.SWING_DEFAULTS["position_size_pct"])
    assert set(swing_constants.WHEEL_DEFAULTS) <= set(wheel_cfg)
    assert (wheel_cfg["strike_atr_mult"], wheel_cfg["limit_bid_mult"]) == (
        2, swing_constants.WHEEL_DEFAULTS["limit_bid_mult"])
    swing_cfg["defensive_universe"].append("ZZZ")
    assert "ZZZ" not in swing_constants.SWING_DEFAULTS["defensive_universe"]


@pytest.mark.parametrize("lanes,lane", [
    ([], "strategy_swing"),
    (None, "strategy_swing"),
    ([{"strategy": "strategy_swing", "config": {"strategy_swing_enabled": "false"}}],
     "strategy_swing"),
    ([{"strategy": "strategy_eb", "config": {"strategy_eb_enabled": True}}],
     "strategy_swing"),
    (LANES, "scalp"),
])
def test_a_lane_that_is_not_enabled_has_no_config(lanes, lane):
    assert _extract_handler()["_lane_config"](lanes, lane) == {}


def test_the_rebuild_reads_the_merged_lane_config(swing):
    swing.rows["sig-1"] = _signal()
    lanes = [{"strategy": "strategy_swing",
              "config": {"strategy_swing_enabled": True, "stop_loss": 0.05}}]
    _run(swing, lanes=lanes)
    cfg = swing.built[0]["cfg"]
    assert cfg["stop_loss"] == 0.05
    assert cfg["profit_target"] == swing_constants.SWING_DEFAULTS["profit_target"]


def test_an_approval_before_the_document_is_cached_reads_it_fresh(swing):
    """The command thread starts before the live loop loads the strategy
    document; an approval queued across a restart must not read "lane not
    enabled" in that window."""
    swing.rows["sig-1"] = _signal()
    loads = []
    service, (ok, _error, _result) = _run(
        swing, lanes=None,
        extra={"load_strategies_from_db":
               lambda: loads.append(1) or (LANES, 200, None)})
    assert ok is True and loads == [1] and len(service.intents) == 1


# the live price: a REST quote mark first, then the IEX latest trade.

def test_the_live_price_is_a_fresh_rest_mark_first():
    price = _extract_handler()["_approval_live_price"]
    adapter = _Adapter(mark_price=101.25)
    assert price(adapter, " aapl ") == (101.25, RTH)
    assert adapter.rest == [["AAPL"]]


def test_the_live_price_falls_back_to_the_latest_trade():
    price = _extract_handler()["_approval_live_price"]
    adapter = _Adapter(mark_price=None,
                       trades={"AAPL": (99.75, "2026-10-05T14:59:30Z")})
    assert price(adapter, "AAPL") == (
        99.75, datetime_module.datetime(2026, 10, 5, 14, 59, 30,
                                        tzinfo=datetime_module.timezone.utc))
    assert price(_Adapter(mark_price=None), "AAPL") is None
    assert price(adapter, "") is None


# the route: first in submit_order, and only for the swing_approval source.

def test_the_live_command_routes_swing_approvals_first():
    seen = []
    ns = extract(("_execute_live_command",), check=(), namespace={
        "datetime": datetime_module, "instance_id": "instance-1",
        "get_conn_retry": lambda **kw: None,
        "_cached_strategies": LANES,
        "_execute_swing_approval":
            lambda adapter, payload, order_service, **kw: seen.append(
                (payload, kw)) or (True, "", {"routed": True}),
    })
    ok, _error, result = ns["_execute_live_command"](
        object(), {"type": "submit_order",
                   "payload": {"source": "swing_approval", "signal_id": "s"}},
        _Service())
    assert ok is True and result == {"routed": True}
    ((payload, kwargs),) = seen
    assert payload == {"source": "swing_approval", "signal_id": "s"}
    assert kwargs == {"cached_strategies": LANES, "log": None}
