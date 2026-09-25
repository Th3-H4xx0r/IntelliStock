"""swing-port Task 9: bracket entries through the strategy intent builder,
cancelling a position's bracket legs before a sell, and the wiring that
registers legs. The EB key pin was computed from the PRE-change code."""
import ast
import datetime as datetime_module
import hashlib
import itertools
import json
import threading
from decimal import Decimal
from types import SimpleNamespace

import pytest

import live_orders
from broker_adapters.alpaca import AlpacaAdapter
from live_orders import (
    InMemoryLifecycleBackend,
    LiveOrderService,
    OrderLifecycleStore,
    OrderSource,
    UnifiedOrderGate,
)
from swing_broker_harness import extract, function_source, source, tree
from swing_live_fixtures import RTH, bracket_intent, equity_snapshot, leg_ref, parent_ref

EB_BUY_KEY = "alpacama-26078e450b92fad1c9e11db27d9425f41505f-0"
BUY_AT = datetime_module.datetime(2026, 9, 24, 13, 31, 5, 123456,
                                  tzinfo=datetime_module.timezone.utc)


def _builders():
    return extract(("_build_strategy_stock_intent", "_build_bracket_intent"),
                   namespace={"datetime": datetime_module})


class _NoStylePortfolio:
    """A bracket must never ask for the extended-hours style."""
    _positions = {"AAPL": 5}

    def _order_style_for_now(self, *args, **kwargs):
        raise AssertionError("bracket entry asked for _order_style_for_now")


EB_SERVICE = SimpleNamespace(account_id="brk-alpaca-main",
                             instance_id="alpaca-main")
PAPER = SimpleNamespace(account_id="acct-1", instance_id="instance-1")


def test_the_eb_intent_is_unchanged_without_a_bracket():
    build = _builders()["_build_strategy_stock_intent"]
    order = build(
        EB_SERVICE, SimpleNamespace(_positions={}), symbol="TQQQ", decision=1,
        price=88.12, current_time=BUY_AT, cash_to_use=1087.93,
        sell_fraction=1.0, action_intents={"eb_rebalance"},
        is_risk_exit=False,
        risk_snapshot_id="risk-state:41:2026-09-24T13:31:00+00:00",
        quote_at=BUY_AT)
    assert order.idempotency_key == EB_BUY_KEY
    assert (order.order_class, order.tif, order.asset_class) == (
        None, "day", "us_equity")
    assert order.quantity == (Decimal("1087.93") / Decimal("88.12")).quantize(
        Decimal("0.00000001"))


def test_a_bracket_hint_builds_a_whole_share_gtc_market_bracket():
    build = _builders()["_build_strategy_stock_intent"]
    order = build(
        PAPER, _NoStylePortfolio(), symbol="AAPL", decision=1, price=200.5,
        current_time=RTH, cash_to_use=1250, sell_fraction=1.0,
        action_intents={"swing_entry"}, is_risk_exit=False,
        risk_snapshot_id="risk-1", quote_at=RTH,
        bracket={"take_profit_price": 218.555, "stop_loss_price": 188.0})
    assert order.quantity == Decimal("6")
    assert (order.order_class, order.tif, order.order_type,
            order.extended_hours) == ("bracket", "gtc", "market", False)
    assert order.take_profit_price == Decimal("218.56")
    assert order.stop_loss_price == Decimal("188.00")
    assert order.reason == "swing_entry"
    assert order.reference_price == Decimal("200.5")


def test_a_bracket_that_floors_to_zero_shares_is_a_definite_refusal():
    build = _builders()["_build_strategy_stock_intent"]
    with pytest.raises(ValueError, match=r"^computed order quantity <= 0$"):
        build(PAPER, _NoStylePortfolio(), symbol="AAPL", decision=1,
              price=200.0, current_time=RTH, cash_to_use=150,
              sell_fraction=1.0, action_intents=set(), is_risk_exit=False,
              risk_snapshot_id="risk-1", quote_at=RTH,
              bracket={"take_profit_price": 218, "stop_loss_price": 188})


@pytest.mark.parametrize("bracket", [
    {"take_profit_price": 218, "stop_loss_price": 205},
    {"take_profit_price": 199, "stop_loss_price": 188},
    {"take_profit_price": 218},
])
def test_legs_that_do_not_straddle_the_price_are_refused(bracket):
    build = _builders()["_build_bracket_intent"]
    with pytest.raises(ValueError, match="bracket refused"):
        build(PAPER, symbol="AAPL", price=200.0, decision_at=RTH,
              cash_to_use=2000, bracket=bracket, risk_snapshot_id="risk-1",
              quote_at=RTH)


def test_a_sell_ignores_a_bracket_hint():
    build = _builders()["_build_strategy_stock_intent"]
    order = build(
        PAPER, SimpleNamespace(_positions={"AAPL": 5}), symbol="AAPL",
        decision=-1, price=200.0, current_time=RTH, cash_to_use=0,
        sell_fraction=1.0, action_intents={"swing_rsi_exit"},
        is_risk_exit=False, risk_snapshot_id="risk-1", quote_at=RTH,
        bracket={"take_profit_price": 218, "stop_loss_price": 188})
    assert order.order_class is None and order.quantity == Decimal("5")


def test_the_quantity_refusal_stays_inside_the_eb_source_window():
    body = source().split("def _build_strategy_stock_intent(", 1)[1][:3000]
    assert 'raise ValueError("computed order quantity <= 0")' in body


# --- ruling 9: a pre-market bracket is a plain market bracket -----------------

#: Monday 2026-10-05 09:20 ET: the swing lane's pre-market entry tick.
PREMARKET = datetime_module.datetime(2026, 10, 5, 13, 20,
                                     tzinfo=datetime_module.timezone.utc)


def test_a_premarket_bracket_skips_the_extended_hours_conversion():
    """Spec 6.1 broker item 3: at 09:20 ET the EB path would turn a buy into
    an extended-hours limit DAY order. A bracket is never asked for that
    style: a whole-share GTC market bracket that Alpaca queues for the open."""
    build = _builders()["_build_strategy_stock_intent"]
    order = build(
        PAPER, _NoStylePortfolio(), symbol="AAPL", decision=1, price=200.5,
        current_time=PREMARKET, cash_to_use=1250, sell_fraction=1.0,
        action_intents={"swing_entry"}, is_risk_exit=False,
        risk_snapshot_id="risk-1", quote_at=PREMARKET,
        bracket={"take_profit_price": 218.555, "stop_loss_price": 188.0})
    assert (order.order_type, order.limit_price, order.tif,
            order.extended_hours, order.order_class, order.quantity) == (
        "market", None, "gtc", False, "bracket", Decimal("6"))


def test_the_same_premarket_buy_without_a_bracket_is_extended_hours():
    """The contrast: EB's buy at the same minute takes the conversion."""
    class _Styled:
        _positions = {}
        _order_style_for_now = AlpacaAdapter._order_style_for_now

    build = _builders()["_build_strategy_stock_intent"]
    order = build(
        PAPER, _Styled(), symbol="AAPL", decision=1, price=200.5,
        current_time=PREMARKET, cash_to_use=1250, sell_fraction=1.0,
        action_intents={"swing_entry"}, is_risk_exit=False,
        risk_snapshot_id="risk-1", quote_at=PREMARKET)
    assert (order.order_type, order.extended_hours, order.order_class) == (
        "limit", True, None)


def test_the_gate_and_the_service_send_a_premarket_bracket_as_market_gtc():
    """The gate's market_open window (04:00-20:00 ET) admits the 09:20 ET
    bracket, and the transport sees a regular-hours market GTC bracket."""
    build = _builders()["_build_strategy_stock_intent"]
    order = build(
        PAPER, _NoStylePortfolio(), symbol="AAPL", decision=1, price=100.0,
        current_time=PREMARKET, cash_to_use=500, sell_fraction=1.0,
        action_intents={"swing_entry"}, is_risk_exit=False,
        risk_snapshot_id="risk-1", quote_at=PREMARKET,
        bracket={"take_profit_price": 109, "stop_loss_price": 94})
    stamps = {name: PREMARKET for name in (
        "kill_switch_at", "cash_at", "calendar_at", "persistence_at",
        "risk_state_at", "watchdog_at")}

    def provider(intent):
        return equity_snapshot(intent, observed_at=PREMARKET,
                               positions_at=PREMARKET, market_open=True,
                               **stamps)

    decision = UnifiedOrderGate().evaluate(order, provider(order))
    assert decision.allowed, decision.reason_codes
    calls = []
    service = LiveOrderService(
        account_id=order.account_id, instance_id=order.instance_id,
        snapshot_provider=provider,
        transport=lambda **kw: calls.append(kw) or parent_ref(order),
        lifecycle_store=OrderLifecycleStore(InMemoryLifecycleBackend()))
    assert service.submit(order).accepted
    ((kwargs,),) = [calls]
    assert (kwargs["order_type"], kwargs["tif"], kwargs["extended_hours"],
            kwargs["limit_price"], kwargs["qty"], kwargs["order_class"]) == (
        "market", "gtc", False, None, 5.0, "bracket")
    assert (kwargs["take_profit"], kwargs["stop_loss"]) == (109.0, 94.0)


# --- EB pins, computed from the PRE-change builder ----------------------------

def _eb_builder():
    """Only EB's builder, with a bracket builder that must never be reached.
    Works on the pre-change code (no bracket keyword) and the post-change
    code alike."""
    def never(*_args, **_kwargs):
        raise AssertionError("an EB-shaped intent reached the bracket builder")

    return extract(("_build_strategy_stock_intent",), check=(),
                   namespace={"datetime": datetime_module,
                              "_build_bracket_intent": never})[
        "_build_strategy_stock_intent"]


class _EbPortfolio:
    """alpaca-main's portfolio_emulator is the adapter itself; its real
    session styling decides market vs extended-hours limit."""
    _order_style_for_now = AlpacaAdapter._order_style_for_now

    def __init__(self, positions):
        self._positions = positions


_EB_TIMES = (
    BUY_AT,                                            # 09:31 ET, RTH
    datetime_module.datetime(2026, 9, 24, 12, 40,      # 08:40 ET
                             tzinfo=datetime_module.timezone.utc),
    datetime_module.datetime(2026, 9, 24, 20, 30,      # 16:30 ET
                             tzinfo=datetime_module.timezone.utc),
    datetime_module.datetime(2026, 9, 24, 13, 31),     # naive
)


def _eb_grid(build, **extra):
    rows = []
    cases = itertools.product(
        (1, -1), _EB_TIMES, (88.12, 412.37, 5.0), (1087.93, 50, 0),
        (1.0, 0.5, None), ({"TQQQ": 12.34567891}, {"TQQQ": 0.4}, {}),
        (frozenset({"eb_rebalance"}), frozenset(),
         frozenset({"eb_exit", "eb_sweep"})),
        (False, True))
    for (decision, at, price, cash, fraction, positions, intents,
         risk_exit) in cases:
        try:
            order = build(
                EB_SERVICE, _EbPortfolio(dict(positions)), symbol="TQQQ",
                decision=decision, price=price, current_time=at,
                cash_to_use=cash, sell_fraction=fraction,
                action_intents=set(intents), is_risk_exit=risk_exit,
                risk_snapshot_id="risk-state:41:2026-09-24T13:31:00+00:00",
                quote_at=BUY_AT, **extra)
        except Exception as exc:  # noqa: BLE001 - the refusal is the pin
            rows.append(["error", type(exc).__name__, str(exc)])
            continue
        rows.append([
            order.idempotency_key, order.identity_payload,
            order.source.value, order.reason, order.side.value,
            str(order.quantity), order.reduce_only, order.order_type,
            str(order.limit_price), order.tif, order.extended_hours,
            str(order.reference_price), order.decision_at.isoformat(),
            order.asset_class, order.order_class, order.position_intent,
            str(order.take_profit_price), str(order.stop_loss_price),
            order.contract_multiplier,
        ])
    return rows


def _digest(rows):
    return hashlib.sha256(json.dumps(rows, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


#: sha256 of 3,888 EB-shaped builds (buys and sells, RTH / pre-market /
#: after-hours / naive clocks, three prices, three cash sizes, three sell
#: fractions, three books, three intent sets, risk exit or not), computed on
#: the pre-change builder at 1f22eb8. A moved digest is the regression.
EB_GRID_DIGEST = (
    "a563dcc9ea38698135fac66bdbd50c88fcd4cc03185b938bb746c0a58228aa75")


def test_eb_builds_are_byte_identical():
    rows = _eb_grid(_eb_builder())
    assert len(rows) == 3888
    assert sum(1 for row in rows if row[0] == "error") > 0
    assert _digest(rows) == EB_GRID_DIGEST


def test_eb_builds_with_the_call_sites_bracket_none_are_byte_identical():
    """The call site passes bracket=None for every EB buy and sell."""
    assert _digest(_eb_grid(_eb_builder(), bracket=None)) == EB_GRID_DIGEST


def test_eb_builds_with_next_open_sell_false_are_byte_identical():
    """Fix wave FW-str-I1: the call site passes next_open_sell=False for every
    EB buy and sell (its document has no swing lane)."""
    assert _digest(_eb_grid(_eb_builder(), bracket=None,
                            next_open_sell=False)) == EB_GRID_DIGEST


# --- F8: a bracket refused before any order exists is a definite refusal ------

def _exception_lane_outcome():
    """The outcome= expression of the live submit block's blanket except,
    lifted out of the module-level main loop and compiled on its own."""
    for node in ast.walk(tree()):
        if not (isinstance(node, ast.Call)
                and getattr(node.func, "id", None) == "_report_live_submit_failure"):
            continue
        texts = [
            value.value for arg in node.args if isinstance(arg, ast.JoinedStr)
            for value in arg.values if isinstance(value, ast.Constant)]
        if not any(str(t).startswith("execute_signal raised ") for t in texts):
            continue
        (outcome,) = [k.value for k in node.keywords if k.arg == "outcome"]
        code = compile(ast.Expression(body=outcome), "outcome", "eval")
        return lambda exc: eval(code, {"_es_e": exc})  # noqa: S307
    raise AssertionError("the execute_signal raised lane was not found")


@pytest.mark.parametrize("exc,outcome", [
    (ValueError("computed order quantity <= 0"), "blocked"),
    (ValueError("order deferred: GDX sell of 1.5 floors to 1 outside RTH"),
     "deferred"),
    (ValueError("order deferred: AAPL bracket legs did not confirm cancelled "
                "within 10s; the sell waits a tick"), "deferred"),
    (RuntimeError("boom"), "failed"),
    (ValueError("computed order quantity <= 0.5"), "failed"),
    (ValueError("TQQQ bracket refused before submission: qty"), "failed"),
    (TimeoutError(), "failed"),
])
def test_the_exception_lane_routes_eb_failures_as_before(exc, outcome):
    """EB pin: every existing message keeps its route."""
    assert _exception_lane_outcome()(exc) == outcome


@pytest.mark.parametrize("message", [
    "bracket refused: AAPL hint lacks a leg price",
    "bracket refused: stop 205.00 and target 218.00 do not straddle 200.0 "
    "for AAPL",
])
def test_a_bracket_refusal_is_a_definite_non_submission(message):
    """Ruling F8: raised inside the builder, before any broker order exists,
    so it is reported as refused (outcome blocked), not as an unknown
    failure."""
    assert _exception_lane_outcome()(ValueError(message)) == "blocked"


def test_every_bracket_refusal_the_builder_raises_is_routed_as_refused():
    build = _builders()["_build_bracket_intent"]
    route = _exception_lane_outcome()
    for bracket in ({"take_profit_price": 218, "stop_loss_price": 205},
                    {"stop_loss_price": 188}, None, "junk"):
        with pytest.raises(ValueError) as refused:
            build(PAPER, symbol="AAPL", price=200.0, decision_at=RTH,
                  cash_to_use=2000, bracket=bracket, risk_snapshot_id="risk-1",
                  quote_at=RTH)
        assert route(refused.value) == "blocked", str(refused.value)


# --- cancelling bracket legs before a sell ------------------------------------

def _cancel():
    return extract(("_cancel_bracket_legs_confirmed",))[
        "_cancel_bracket_legs_confirmed"]


def _service_with_legs():
    order = bracket_intent()
    store = OrderLifecycleStore(InMemoryLifecycleBackend())
    service = LiveOrderService(
        account_id=order.account_id, instance_id=order.instance_id,
        snapshot_provider=equity_snapshot,
        transport=lambda **kw: parent_ref(order, status="filled",
                                          filled_qty="5",
                                          filled_avg_price="100"),
        lifecycle_store=store,
        legs_lookup=lambda _id: SimpleNamespace(legs=(
            leg_ref("leg-tp", "limit", status="new"),
            leg_ref("leg-sl", "stop"))))
    assert service.submit(order).accepted
    return service


class _Adapter:
    def __init__(self, *, working=(), confirmed=True, readable=True):
        self.working = list(working)
        self.confirmed = confirmed
        self.readable = readable
        self.cancel_calls = []

    def list_open_orders_strict(self, limit=200):
        if not self.readable:
            raise RuntimeError("orders endpoint unreachable")
        return list(self.working)

    def cancel_orders_confirmed(self, order_ids, timeout_s=10.0):
        self.cancel_calls.append((list(order_ids), timeout_s))
        return self.confirmed


def test_nothing_to_cancel_lets_the_sell_go_without_a_cancel():
    adapter = _Adapter()
    empty = SimpleNamespace(
        lifecycle_store=OrderLifecycleStore(InMemoryLifecycleBackend()),
        instance_id="instance-1")
    assert _cancel()(adapter, empty, "AAPL") is True
    assert adapter.cancel_calls == []


def test_store_legs_and_broker_legs_are_all_cancelled():
    service = _service_with_legs()
    stray = SimpleNamespace(broker_order_id="broker-stray", symbol="AAPL",
                            side="sell", status="new", order_class="bracket")
    other = SimpleNamespace(broker_order_id="broker-msft", symbol="MSFT",
                            side="sell", status="new", order_class="bracket")
    adapter = _Adapter(working=[stray, other])
    assert _cancel()(adapter, service, "aapl", timeout_s=10.0) is True
    ((ids, timeout),) = adapter.cancel_calls
    assert sorted(ids) == ["broker-leg-sl", "broker-leg-tp", "broker-stray"]
    assert timeout == 10.0


def test_unconfirmed_leg_cancel_defers_the_sell():
    """Review Focus 3, the broker half."""
    service = _service_with_legs()
    assert _cancel()(_Adapter(confirmed=False), service, "AAPL") is False


def test_an_unreadable_book_defers_the_sell_without_cancelling():
    service = _service_with_legs()
    adapter = _Adapter(readable=False)
    assert _cancel()(adapter, service, "AAPL") is False
    assert adapter.cancel_calls == []


def test_a_plain_working_sell_is_not_a_leg():
    """An EB-shaped simple sell on the symbol is never cancelled here."""
    plain = SimpleNamespace(broker_order_id="broker-plain", symbol="AAPL",
                            side="sell", status="new", order_class="simple")
    adapter = _Adapter(working=[plain])
    empty = SimpleNamespace(
        lifecycle_store=OrderLifecycleStore(InMemoryLifecycleBackend()),
        instance_id="instance-1")
    assert _cancel()(adapter, empty, "AAPL") is True
    assert adapter.cancel_calls == []


# --- ruling 7 (L3 carry): a parent with one leg recorded gets the other -------

def test_ensure_bracket_legs_completes_a_half_registered_parent():
    order = bracket_intent()
    state = {"legs": (leg_ref("leg-tp", "limit", status="new"),
                      leg_ref("", "stop"))}
    logs = []
    service = LiveOrderService(
        account_id=order.account_id, instance_id=order.instance_id,
        snapshot_provider=equity_snapshot,
        transport=lambda **kw: parent_ref(order, status="filled",
                                          filled_qty="5",
                                          filled_avg_price="100"),
        lifecycle_store=OrderLifecycleStore(InMemoryLifecycleBackend()),
        legs_lookup=lambda _id: SimpleNamespace(legs=state["legs"]),
        log=lambda *a: logs.append(a))
    assert service.submit(order).accepted
    store = service.lifecycle_store
    assert store.get("leg-tp") is not None and store.get("leg-sl") is None
    assert any("bracket.legs.unregistered" in line[0] for line in logs)
    # The broker now reports the stop leg with its client order id.
    state["legs"] = (leg_ref("leg-tp", "limit", status="new"),
                     leg_ref("leg-sl", "stop"))
    assert service.ensure_bracket_legs() == 1
    assert store.require("leg-sl").intent.source is OrderSource.BRACKET_LEG
    assert store.require("leg-sl").intent.parent_client_order_id == \
        order.idempotency_key
    assert service.ensure_bracket_legs() == 0


def test_a_parent_with_both_legs_is_not_read_again():
    order = bracket_intent()
    reads = []
    service = LiveOrderService(
        account_id=order.account_id, instance_id=order.instance_id,
        snapshot_provider=equity_snapshot,
        transport=lambda **kw: parent_ref(order),
        lifecycle_store=OrderLifecycleStore(InMemoryLifecycleBackend()),
        legs_lookup=lambda oid: reads.append(oid) or SimpleNamespace(legs=(
            leg_ref("leg-tp", "limit", status="new"),
            leg_ref("leg-sl", "stop"))))
    assert service.submit(order).accepted
    assert reads == ["broker-parent"]
    assert service.ensure_bracket_legs() == 0
    assert reads == ["broker-parent"]


# --- the reconcile hook -------------------------------------------------------

def _reconcile_ns(monkeypatch, strategies):
    reconciled = []

    class _Reconciler:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def reconcile(self, snapshot):
            reconciled.append(snapshot)
            return SimpleNamespace(healthy=True, evidence_hash="h")

    monkeypatch.setattr(live_orders, "StartupReconciler", _Reconciler)
    lines = []
    ns = extract(
        ("_reconcile_alpaca_ownership", "_lane_enabled", "_truthy",
         "_merged_strategy_settings"),
        assigns=("_LANE_ENABLE_FLAGS",),
        namespace={"datetime": datetime_module, "instance_id": "swing-paper",
                   "_live_order_dependency_lock": threading.Lock(),
                   "_live_order_dependency_state": {},
                   "_cached_strategies": strategies,
                   "_log": lambda m, c="white": lines.append((m, c))})
    return ns, reconciled, lines


class _ReconcileAdapter:
    def capture_reconciliation_snapshot(self, account_id):
        return "snapshot"

    def complete_startup_reconciliation(self, result):
        self.result = result


def test_reconcile_registers_missing_legs_first_on_a_swing_document(monkeypatch):
    calls = []
    service = SimpleNamespace(
        account_id="acct-1", lifecycle_store=None,
        apply_broker_event=None,
        ensure_bracket_legs=lambda: calls.append("legs") or 0)
    swing = [{"strategy": "strategy_swing",
              "config": {"strategy_swing_enabled": True}}]
    ns, reconciled, _lines = _reconcile_ns(monkeypatch, swing)
    ns["_reconcile_alpaca_ownership"](_ReconcileAdapter(), service)
    assert calls == ["legs"] and reconciled == ["snapshot"]


def test_reconcile_never_touches_legs_on_an_eb_document(monkeypatch):
    def boom():
        raise AssertionError("EB's reconcile read bracket legs")

    service = SimpleNamespace(account_id="brk-alpaca-main",
                              lifecycle_store=None, apply_broker_event=None,
                              ensure_bracket_legs=boom)
    eb = [{"strategy": "strategy_eb", "config": {"strategy_eb_enabled": True}}]
    for strategies in (eb, None):
        ns, reconciled, _lines = _reconcile_ns(monkeypatch, strategies)
        ns["_reconcile_alpaca_ownership"](_ReconcileAdapter(), service)
        assert reconciled == ["snapshot"]


def test_a_failed_leg_registration_never_stops_the_reconcile(monkeypatch):
    def boom():
        raise RuntimeError("nested read timed out")

    service = SimpleNamespace(account_id="acct-1", lifecycle_store=None,
                              apply_broker_event=None,
                              ensure_bracket_legs=boom)
    swing = [{"strategy": "strategy_swing",
              "config": {"strategy_swing_enabled": True}}]
    ns, reconciled, lines = _reconcile_ns(monkeypatch, swing)
    ns["_reconcile_alpaca_ownership"](_ReconcileAdapter(), service)
    assert reconciled == ["snapshot"]
    assert any("bracket leg registration failed" in m and c == "yellow"
               for m, c in lines)


# --- wiring (source assertions: the loop and boot are module-level code) --------

def test_the_submit_block_cancels_legs_only_after_the_gate_accepts():
    """Fix wave FW-str-I1 reverses the Task 9 order: the loop no longer
    cancels a position's legs before it builds (and gates) the sell. A sell
    on a swing document goes through _submit_swing_sell, whose after-the-gate
    hook cancels the legs; the deferral message is unchanged."""
    text = source()
    gate = text.index("if _is_alpaca_stock_gate:")
    build = text.index("_build_strategy_stock_intent(", gate)
    window = text[gate:build]
    assert "_cancel_bracket_legs_confirmed(" not in window
    assert "decision == -1" in window
    assert '_lane_enabled(_cached_strategies, "strategy_swing")' in window
    hook = function_source("_submit_swing_sell")
    assert "_cancel_bracket_legs_confirmed(" in hook
    assert 'f"order deferred: {intent.symbol} bracket legs did not confirm "' in hook


def test_the_swing_sell_guard_is_evaluated_sells_first():
    """EB pin: an EB buy never reads the lane registry, and an EB sell never
    reaches the cancel (the registry answers False for doc 200)."""
    text = source()
    gate = text.index("if _is_alpaca_stock_gate:")
    window = text[gate:text.index("_build_strategy_stock_intent(", gate)]
    assert window.index("decision == -1") < window.index("_lane_enabled(")


def test_the_call_site_feeds_the_bracket_hint_for_buys_only():
    text = source()
    call = text.split("_build_strategy_stock_intent(\n", 2)[2][:2500]
    assert 'nexus_hint.get("bracket")' in call
    assert "if decision == 1" in call


def test_the_live_service_reads_legs_back_and_reconcile_retries_them():
    text = source()
    assert "legs_lookup=live_adapter.get_order_with_legs," in text
    reconcile = function_source("_reconcile_alpaca_ownership")
    assert "order_service.ensure_bracket_legs()" in reconcile
    assert '"strategy_swing"' in reconcile
