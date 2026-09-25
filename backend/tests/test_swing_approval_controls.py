"""FW-lo-I1 (swing-port final review, live-orders slice Important 1).

An approval runs in the 1-second command thread, off-tick. The live loop
stamps kill_switch_at / cash_at / calendar_at / risk_state_at / watchdog_at
only inside a tick (every 20 minutes) and the gate wants each within 60 s for
an opening order, so an approval five minutes after the last tick was refused
dependency.*.stale and bounced back to pending, again and again.

The approval handler now RE-READS those inputs (_approval_control_overlay)
and lays them over its own snapshot only: the tick's shared dependency state
is read, never written, so the loop's path is untouched. A re-read that fails
is transient, like the stale stamps were."""
import datetime as datetime_module
import threading
from decimal import Decimal
from types import SimpleNamespace

import pytest

from live_orders import (
    Health,
    InMemoryLifecycleBackend,
    LiveOrderService,
    OrderLifecycleStore,
)
from live_risk_state import (
    InMemoryRiskBackend,
    RiskLimits,
    RiskStateStore,
    initialize_risk_state,
)
from benchmark_alpha.watchdog import ControlHealth
from market_marks import MarketMark, MarkQuality, MarkSource, classify_session
from swing_broker_harness import extract, function_source
from swing_trader import approvals, notify, signals_store

UTC = datetime_module.timezone.utc


class _Frozen(datetime_module.datetime):
    """The handler's clock. Stamps are built from it too, because the snapshot
    provider only accepts a stamp that is an instance of its datetime class."""

    @classmethod
    def now(cls, tz=None):
        return NOW if tz is not None else NOW.replace(tzinfo=None)


#: The 10:40 ET grid tick, and the operator's click five minutes later.
TICK = _Frozen(2026, 10, 5, 14, 40, 30, tzinfo=UTC)
NOW = TICK + datetime_module.timedelta(minutes=5)
assert isinstance(NOW, _Frozen)
IID = "swing-paper"
LANES = [{"strategy": "strategy_swing", "config": {"strategy_swing_enabled": True}}]
#: The swing lane's live envelope (broker._strategy_eb_risk_limits _SW_DEFAULTS).
SWING_LIMITS = RiskLimits(max_order_fraction=0.2, max_symbol_fraction=0.2,
                          max_leveraged_fraction=0.2, soft=0.25, hard=0.35,
                          kill=0.45)


CLOCK = SimpleNamespace(datetime=_Frozen, timezone=datetime_module.timezone,
                        timedelta=datetime_module.timedelta,
                        date=datetime_module.date)


def _tick_state():
    """What the live loop left behind at the 10:40 tick; the 60 s reconcile
    keeps only positions and persistence current."""
    fresh = NOW - datetime_module.timedelta(seconds=20)
    return {
        "kill_switch": "healthy", "kill_switch_at": TICK,
        "cash": "healthy", "cash_at": TICK,
        "calendar": "healthy", "calendar_at": TICK, "market_open": True,
        "positions": "healthy", "positions_at": fresh,
        "persistence": "healthy", "persistence_at": fresh,
        "risk_state": "healthy", "risk_state_at": TICK,
        "risk_snapshot_id": "risk-state:1:" + TICK.isoformat(),
        "watchdog": "healthy", "watchdog_at": TICK,
    }


class _ControlStore:
    """The kill-switch store: the Instances row and the watchdog's
    control_health row (written every 30 s by the watchdog subprocess)."""

    def __init__(self, *, run_command=True, watchdog_status="healthy",
                 watchdog_at=None, degraded=False, raises=None, rows=True,
                 tampered=False):
        self.reads = []
        self.raises = raises
        observed = watchdog_at or NOW - datetime_module.timedelta(seconds=12)
        # What watchdog_main.write_health stores: the evidence's own doc.
        evidence = ControlHealth(instance_id=IID, status=watchdog_status,
                                 observed_at=observed, result_status="OK",
                                 degraded_audit=degraded).to_doc()
        if tampered:
            evidence["status"] = "healthy" if watchdog_status != "healthy" else "unhealthy"
        self.rows = {} if not rows else {
            ("Instances", IID): {"id": IID, "runCommand": run_command},
            ("AlphaState", f"control_health:{IID}"): {"payload": evidence},
        }

    def get(self, table, key):
        self.reads.append((table, key))
        if self.raises is not None:
            raise self.raises
        return self.rows.get((table, key))


class _Adapter:
    def __init__(self, *, equity=60000.0, cash=50000.0, account_raises=None,
                 market_open=True, calendar_raises=None, cached_cash=None):
        self._instance_id = IID
        self._account_id = "acct-1"
        self._positions = {}
        self._positions_stale_since = None
        # The cash the last tick read; the broker's figure may have moved since.
        self._cash = cash if cached_cash is None else cached_cash
        self._account_equity = equity
        self.equity, self.cash = equity, cash
        self.account_raises = account_raises
        self.market_open = market_open
        self.calendar_raises = calendar_raises
        self.account_reads = 0
        self.calls = []
        mark = MarketMark(
            symbol="AAPL", price=100.5, bid=100.49, ask=100.51, bid_size=100,
            ask_size=100, observed_at=NOW - datetime_module.timedelta(seconds=1),
            received_at=NOW, source=MarkSource.REST_QUOTE, feed="sip",
            quality=MarkQuality.CONSOLIDATED, session=classify_session(NOW))
        self._market_marks = SimpleNamespace(
            get=lambda symbol: mark if symbol == "AAPL" else None)

    def fetch_rest_quote_marks(self, symbols):
        self.calls.append("price-read")
        return tuple(symbols)

    def get_latest_trades(self, symbols):
        return {}

    def refresh_account(self):
        self.calls.append("control-re-read")
        self.account_reads += 1
        if self.account_raises is not None:
            raise self.account_raises
        self._cash = self.cash
        self._account_equity = self.equity
        return SimpleNamespace(equity=self.equity, cash=self.cash)

    def is_market_open(self, now_utc):
        if self.calendar_raises is not None:
            raise self.calendar_raises
        return self.market_open


def _risk():
    """A durable risk row saved at the tick, and the loop's copy of it."""
    store = RiskStateStore(InMemoryRiskBackend())
    saved = store.save(initialize_risk_state(IID, "acct-1", Decimal("60000"),
                                             TICK, limits=SWING_LIMITS))
    return store, saved


def _namespace(state, controls, adapter, risk_store, risk_state, service=None):
    return {
        "datetime": CLOCK,
        "_live_order_dependency_lock": threading.RLock(),
        "_live_order_dependency_state": state,
        "_KS_RDB": controls,
        "_live_risk_store": risk_store,
        "_live_risk_state": risk_state,
        "_live_risk_limits_for_this_document": lambda: SWING_LIMITS,
        "MODE_LIVE": "live", "mode": "live", "live_broker_type": "alpaca",
        "live_brokerage_id": "acct-1", "instance_id": IID,
        "_live_stock_order_service": service,
        "_open_order_idempotency_keys": lambda _identity: frozenset(),
        "_live_option_dependency_snapshot": None,
        "_cached_strategies": LANES,
        "_lane_enabled": extract(
            ("_lane_enabled", "_truthy", "_merged_strategy_settings"),
            assigns=("_LANE_ENABLE_FLAGS",))["_lane_enabled"],
    }


def _overlay(**kwargs):
    state = kwargs.pop("state", _tick_state())
    controls = kwargs.pop("controls", _ControlStore())
    adapter = kwargs.pop("adapter", _Adapter())
    risk_store, risk_state = kwargs.pop("risk", _risk())
    ns = extract(("_approval_control_overlay",), namespace=_namespace(
        state, controls, adapter, risk_store, risk_state))
    return ns["_approval_control_overlay"](adapter, instance_key=IID,
                                           now_utc=NOW), state, adapter


# --- the re-read ---------------------------------------------------------------

def test_every_gate_control_is_re_read_now():
    before = _tick_state()
    out, state, adapter = _overlay(state=before, adapter=_Adapter(cached_cash=1.0))
    assert out["kill_switch"] is Health.HEALTHY and out["kill_switch_at"] == NOW
    assert out["cash"] is Health.HEALTHY and out["cash_at"] == NOW
    assert out["calendar"] is Health.HEALTHY and out["calendar_at"] == NOW
    assert out["market_open"] is True
    assert out["risk_state"] is Health.HEALTHY and out["risk_state_at"] == NOW
    # The watchdog stamp is its evidence's own time, never now.
    assert out["watchdog"] is Health.HEALTHY
    assert out["watchdog_at"] == NOW - datetime_module.timedelta(seconds=12)
    assert adapter.account_reads == 1 and adapter._cash == 50000.0
    # Nothing shared is written: the tick's state is exactly as it was.
    assert state == _tick_state()
    assert set(out) == {"kill_switch", "kill_switch_at", "cash", "cash_at",
                        "calendar", "calendar_at", "market_open",
                        "risk_state", "risk_state_at", "watchdog",
                        "watchdog_at"}


def test_a_flipped_kill_switch_reads_unhealthy():
    out, _state, _adapter = _overlay(controls=_ControlStore(run_command=False))
    assert out["kill_switch"] is Health.UNHEALTHY


def test_a_missing_instance_row_reads_unknown_as_the_tick_does():
    out, _state, _adapter = _overlay(controls=_ControlStore(rows=False))
    assert out["kill_switch"] is Health.UNKNOWN
    assert (out["watchdog"], out["watchdog_at"]) == (Health.UNKNOWN, None)


@pytest.mark.parametrize("status,degraded,expected", [
    ("healthy", False, Health.HEALTHY),
    ("healthy", True, Health.UNHEALTHY),
    ("unhealthy", False, Health.UNHEALTHY),
])
def test_the_watchdog_evidence_is_read_as_the_tick_reads_it(status, degraded,
                                                            expected):
    out, _state, _adapter = _overlay(controls=_ControlStore(
        watchdog_status=status, degraded=degraded))
    assert out["watchdog"] is expected


def test_evidence_whose_hash_does_not_match_reads_unknown():
    out, _state, _adapter = _overlay(controls=_ControlStore(tampered=True))
    assert (out["watchdog"], out["watchdog_at"]) == (Health.UNKNOWN, None)


def test_a_stale_watchdog_stays_stale():
    """Never re-stamped without a read: an old heartbeat keeps its old time,
    and the gate still refuses it."""
    old = NOW - datetime_module.timedelta(minutes=4)
    out, _state, _adapter = _overlay(controls=_ControlStore(watchdog_at=old))
    assert out["watchdog_at"] == old


def test_a_drawdown_past_a_rung_reads_unhealthy_and_is_not_saved():
    store, saved = _risk()
    out, _state, _adapter = _overlay(adapter=_Adapter(equity=40000.0),
                                     risk=(store, saved))
    assert out["risk_state"] is Health.UNHEALTHY
    # Not saved: the durable row (and so risk_snapshot_id) stays the tick's.
    assert store.load_required(IID, "acct-1").version == saved.version


@pytest.mark.parametrize("change", [
    {"controls": _ControlStore(raises=ConnectionError("pg down"))},
    {"controls": None},
    {"adapter": _Adapter(account_raises=ConnectionError("account 503"))},
    {"adapter": _Adapter(equity=None)},
    {"adapter": _Adapter(calendar_raises=RuntimeError("calendar"))},
    {"risk": (None, None)},
])
def test_a_read_that_fails_raises(change):
    with pytest.raises(Exception):
        _overlay(**change)


# --- the approval, end to end ----------------------------------------------------

@pytest.fixture
def notices(monkeypatch):
    sent = []
    monkeypatch.setattr(notify, "_sink", lambda **kwargs: sent.append(kwargs))
    return sent


@pytest.fixture
def signals(store, monkeypatch, notices):
    monkeypatch.setattr(signals_store, "store", store)
    return store


def _approved():
    doc = signals_store.new_signal(
        instance_id=IID, lane="swing", symbol="AAPL", session="2026-10-05",
        score=62, recommendation="review", reasoning="r", key_risks=[],
        size_adjustment=1.0,
        proposal={"entry": 98.0, "stop": 92.12, "target": 106.82, "shares": 76},
        status="pending")
    signals_store.insert_signal(doc)
    decided = approvals.decide(doc, "approve", "pranav", None, NOW.isoformat())
    assert signals_store.cas_signal(doc["id"], expect_status="pending", doc=decided)
    return doc["id"]


def _handler_and_service(*, controls=None, adapter=None, overlay=True):
    state = _tick_state()
    adapter = adapter or _Adapter()
    risk_store, risk_state = _risk()
    sent = []
    ns_holder = {}
    def gate_read(intent):
        adapter.calls.append("gate")
        return ns_holder["ns"]["_live_order_dependency_snapshot"](adapter, intent)

    service = LiveOrderService(
        account_id="acct-1", instance_id=IID,
        snapshot_provider=gate_read,
        transport=lambda **kw: sent.append(kw) or SimpleNamespace(
            status="accepted", broker_order_id="b-1", id="b-1", filled_qty=0,
            filled_avg_price=None),
        lifecycle_store=OrderLifecycleStore(InMemoryLifecycleBackend()))
    namespace = _namespace(state, controls or _ControlStore(), adapter,
                           risk_store, risk_state, service)
    if not overlay:
        # What the handler did before the fix: no re-read at all.
        namespace["_approval_control_overlay"] = lambda *_a, **_k: {}
    functions = ["_execute_swing_approval", "_lane_config",
                 "_approval_live_price", "_build_bracket_intent",
                 "_build_option_intent", "_refresh_option_quote", "_truthy",
                 "_merged_strategy_settings", "_live_order_dependency_snapshot"]
    if overlay:
        functions.append("_approval_control_overlay")
    ns = extract(functions, assigns=("_live_option_quotes", "_LANE_ENABLE_FLAGS"),
                 namespace=namespace, check=("_execute_swing_approval",))
    ns_holder["ns"] = ns

    def approve(signal_id):
        return ns["_execute_swing_approval"](
            adapter, {"source": "swing_approval", "signal_id": signal_id},
            service, cached_strategies=LANES, now_utc=NOW,
            sleep=lambda _s: None)

    approve.adapter = adapter
    return approve, service, sent, state


def test_without_the_re_read_an_approval_five_minutes_after_a_tick_bounces(
        signals, notices):
    """The review's probe, through the real handler, snapshot and gate: the
    old handler put the signal back to pending on five stale stamps."""
    approve, _service, sent, _state = _handler_and_service(overlay=False)
    sid = _approved()
    ok, error, _result = approve(sid)
    assert ok is False and sent == []
    for name in ("kill_switch", "cash", "calendar", "risk_state", "watchdog"):
        assert f"dependency.{name}.stale" in error
    assert signals_store.get_signal(sid)["status"] == "pending"


def test_an_approval_five_minutes_after_the_last_tick_places_the_order(
        signals, notices):
    approve, service, sent, state = _handler_and_service()
    sid = _approved()
    ok, error, _result = approve(sid)
    assert (ok, error) == (True, "")
    assert len(sent) == 1
    ((kwargs,),) = [sent]
    assert (kwargs["symbol"], kwargs["side"], kwargs["order_class"]) == (
        "AAPL", "buy", "bracket")
    (record,) = service.lifecycle_store.list_for_instance(IID)
    row = signals_store.get_signal(sid)
    assert (row["status"], row["order_client_id"]) == (
        "submitted", record.client_order_id)
    assert state == _tick_state()          # the tick's view was never touched
    assert notices == []


def test_a_kill_switch_flipped_since_the_tick_refuses_the_approval(signals,
                                                                    notices):
    approve, _service, sent, _state = _handler_and_service(
        controls=_ControlStore(run_command=False))
    sid = _approved()
    ok, error, _result = approve(sid)
    assert ok is False and sent == []
    assert "dependency.kill_switch.unhealthy" in error


def test_a_failed_re_read_is_transient(signals, notices):
    approve, _service, sent, _state = _handler_and_service(
        controls=_ControlStore(raises=ConnectionError("pg down")))
    sid = _approved()
    ok, error, _result = approve(sid)
    assert ok is False and sent == []
    assert error.startswith("the order gate's controls could not be re-read "
                            "— approve again")
    assert "ConnectionError" in error
    assert signals_store.get_signal(sid)["status"] == "pending"
    assert len(notices) == 1 and "approve again" in notices[0]["body"]


def test_the_controls_are_re_read_before_the_price_then_the_gate(signals,
                                                                   notices):
    """Round 2, item 2: the re-read's round trips must never sit between the
    approval's quote read (which pins the intent's quote_at) and the gate's
    read of the same mark, or a stream quote in between is a
    quote.timestamp_mismatch "approve again" bounce. Order pinned:
    control re-read -> price read -> gate."""
    approve, _service, sent, _state = _handler_and_service()
    ok, error, _result = approve(_approved())
    assert (ok, error) == (True, "")
    assert approve.adapter.calls == ["control-re-read", "price-read", "gate"]
    assert len(sent) == 1


def test_a_mark_that_moves_after_the_price_read_is_not_widened_by_the_re_read(
        signals, notices):
    """The window between the price read and the gate is pure again: a mark
    update between the re-read and the price read no longer matters."""
    adapter = _Adapter()
    original = adapter.refresh_account

    def slow_refresh():
        # A stream quote landing while the controls are being re-read.
        later = adapter._market_marks.get("AAPL")
        adapter._market_marks = SimpleNamespace(get=lambda s: MarketMark(
            symbol="AAPL", price=100.52, bid=100.51, ask=100.53, bid_size=100,
            ask_size=100, observed_at=later.observed_at + datetime_module.timedelta(
                milliseconds=400), received_at=NOW, source=MarkSource.STREAM_QUOTE,
            feed="sip", quality=MarkQuality.CONSOLIDATED,
            session=classify_session(NOW)) if s == "AAPL" else None)
        return original()

    adapter.refresh_account = slow_refresh
    approve, _service, sent, _state = _handler_and_service(adapter=adapter)
    ok, error, _result = approve(_approved())
    assert (ok, error) == (True, "")
    assert len(sent) == 1


def test_the_handler_re_reads_before_the_price_and_passes_only_its_view():
    body = function_source("_execute_swing_approval")
    reread = body.index("_approval_control_overlay(")
    assert reread < body.index("_approval_live_price(")
    assert body.index("_approval_live_price(") < body.index("_build_bracket_intent(")
    assert body.index("_build_bracket_intent(") < body.index("order_service.enqueue(")
    assert "snapshot_overlay=" in body
    overlay = function_source("_approval_control_overlay")
    assert "_live_order_dependency_state" not in overlay
    assert ".save(" not in overlay


# --- LiveOrderService.submit(snapshot_overlay=...) ------------------------------

def _stale_service(sent):
    from swing_live_fixtures import RTH, bracket_intent, equity_snapshot

    stale = RTH - datetime_module.timedelta(minutes=5)
    provider = lambda intent: equity_snapshot(   # noqa: E731
        intent, kill_switch_at=stale, cash_at=stale, calendar_at=stale,
        risk_state_at=stale, watchdog_at=stale)
    service = LiveOrderService(
        account_id="acct-1", instance_id="instance-1",
        snapshot_provider=provider,
        transport=lambda **kw: sent.append(kw) or SimpleNamespace(
            status="accepted", broker_order_id="b-1", id="b-1", filled_qty=0,
            filled_avg_price=None),
        lifecycle_store=OrderLifecycleStore(InMemoryLifecycleBackend()))
    return service, bracket_intent(), RTH


def test_the_overlay_replaces_the_stale_stamps_for_this_evaluation_only():
    sent = []
    service, intent, rth = _stale_service(sent)
    refused = service.submit(intent)
    assert not refused.decision.allowed
    assert {c for c in refused.decision.reason_codes} == {
        "dependency.kill_switch.stale", "dependency.cash.stale",
        "dependency.calendar.stale", "dependency.risk_state.stale",
        "dependency.watchdog.stale"}
    fresh = {name: rth for name in ("kill_switch_at", "cash_at", "calendar_at",
                                    "risk_state_at", "watchdog_at")}
    assert service.submit(intent, snapshot_overlay=fresh).accepted
    assert len(sent) == 1


def test_an_overlay_the_snapshot_rejects_is_an_invalid_snapshot():
    sent = []
    service, intent, _rth = _stale_service(sent)
    out = service.submit(intent, snapshot_overlay={"cash": "sort of"})
    assert out.decision.reason_codes == ("dependency.snapshot.invalid",)
    out = service.submit(intent, snapshot_overlay={"no_such_field": 1})
    assert out.decision.reason_codes == ("dependency.snapshot.invalid",)
    assert sent == []


def test_the_re_read_docstring_names_every_field_refresh_account_writes():
    """Round 2, minor 4: the docstring said the one shared write is cash and
    equity; refresh_account() also writes the PDT facts. Pinned against the
    adapter's source, so a new write there fails this until it is named."""
    import ast
    import inspect
    import textwrap

    from broker_adapters.alpaca import AlpacaAdapter

    tree = ast.parse(textwrap.dedent(inspect.getsource(AlpacaAdapter.refresh_account)))
    written = {node.attr for node in ast.walk(tree)
               if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Store)
               and isinstance(node.value, ast.Name) and node.value.id == "self"}
    assert {"_cash", "_account_equity", "_daytrade_count", "_pattern_day_trader",
            "_account_facts_at"} <= written
    doc = ast.get_docstring(next(
        node for node in ast.parse(function_source("_approval_control_overlay")).body))
    missing = sorted(name for name in written if name not in doc)
    assert missing == [], f"not named in the docstring: {missing}"
