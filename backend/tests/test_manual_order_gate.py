import ast
import datetime
import os
import types
from decimal import Decimal

from live_orders import GateDecision, OrderSide, OrderSource
from live_state import OrderSubmission


_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_BROKER_PATH = os.path.join(_BACKEND, "broker.py")
_TREE = ast.parse(open(_BROKER_PATH, encoding="utf-8").read())


def _extract_execute_live_command():
    node = next(
        item
        for item in _TREE.body
        if isinstance(item, ast.FunctionDef) and item.name == "_execute_live_command"
    )
    ns = {
        "datetime": datetime,
        "instance_id": "instance-1",
        "get_conn_retry": lambda **_kw: None,
    }
    exec(compile(ast.Module(body=[node], type_ignores=[]), "broker.py", "exec"), ns)
    return ns["_execute_live_command"]


def _extract_strategy_intent_builder():
    node = next(
        item
        for item in _TREE.body
        if isinstance(item, ast.FunctionDef)
        and item.name == "_build_strategy_stock_intent"
    )
    ns = {"datetime": datetime}
    exec(compile(ast.Module(body=[node], type_ignores=[]), "broker.py", "exec"), ns)
    return ns["_build_strategy_stock_intent"]


class _Adapter:
    _positions = {"AAPL": 5.0}
    _last_prices = {"AAPL": 100.0}

    def submit_order(self, **_kwargs):
        raise AssertionError("manual source bypassed the unified order service")


class _Service:
    account_id = "acct-1"
    instance_id = "instance-1"

    def __init__(self, *, allowed=True):
        self.intents = []
        self.allowed = allowed

    def enqueue(self, intent):
        self.intents.append(intent)
        decision = GateDecision(
            allowed=self.allowed,
            approved_quantity=intent.quantity if self.allowed else Decimal("0"),
            reason_codes=() if self.allowed else ("dependency.quote.unknown",),
            idempotency_key=intent.idempotency_key,
        )
        ref = types.SimpleNamespace(broker_order_id="order-1") if self.allowed else None
        return OrderSubmission(decision=decision, reference=ref)


def test_manual_submit_constructs_intent_and_uses_gate_service_only():
    execute = _extract_execute_live_command()
    service = _Service()

    ok, error, result = execute(
        _Adapter(),
        {
            "type": "submit_order",
            "payload": {
                "symbol": "AAPL",
                "side": "buy",
                "qty": 2,
                "reason": "operator allocation",
            },
        },
        service,
    )

    assert ok is True
    assert error == ""
    assert result["order_id"] == "order-1"
    assert len(service.intents) == 1
    intent = service.intents[0]
    assert intent.source is OrderSource.MANUAL
    assert intent.quantity == Decimal("2")
    assert intent.reduce_only is False


def test_manual_command_fails_closed_when_service_or_dependency_is_unavailable():
    execute = _extract_execute_live_command()

    missing = execute(
        _Adapter(),
        {"type": "submit_order", "payload": {"symbol": "AAPL", "side": "buy", "qty": 1}},
    )
    denied = execute(
        _Adapter(),
        {"type": "submit_order", "payload": {"symbol": "AAPL", "side": "buy", "qty": 1}},
        _Service(allowed=False),
    )

    assert missing[0] is False
    assert "unavailable" in missing[1]
    assert denied[0] is False
    assert "dependency.quote.unknown" in denied[1]


def test_close_position_is_a_reduce_only_manual_intent():
    execute = _extract_execute_live_command()
    service = _Service()

    ok, _, _ = execute(
        _Adapter(),
        {"type": "close_position", "payload": {"symbol": "AAPL", "qty": 12}},
        service,
    )

    assert ok is True
    intent = service.intents[0]
    assert intent.source is OrderSource.MANUAL
    assert intent.reduce_only is True
    assert intent.side.value == "sell"


def test_broker_has_no_direct_submit_order_transport_calls():
    direct_calls = []
    for node in ast.walk(_TREE):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr == "submit_order":
            direct_calls.append(node.lineno)

    assert direct_calls == [], (
        "broker.py sources must enqueue immutable intents through LiveOrderService; "
        f"direct submit_order calls at lines {direct_calls}"
    )


def test_normal_strategy_and_risk_exit_build_distinct_immutable_sources():
    build = _extract_strategy_intent_builder()
    service = types.SimpleNamespace(account_id="acct-1", instance_id="instance-1")
    portfolio = types.SimpleNamespace(_positions={"AAPL": 5})
    now = datetime.datetime(2026, 7, 27, 14, 30, tzinfo=datetime.timezone.utc)

    buy = build(
        service,
        portfolio,
        symbol="AAPL",
        decision=1,
        price=100,
        current_time=now,
        cash_to_use=250,
        sell_fraction=1,
        action_intents=set(),
        is_risk_exit=False,
        risk_snapshot_id="risk-7",
        quote_at=now,
    )
    risk_exit = build(
        service,
        portfolio,
        symbol="AAPL",
        decision=-1,
        price=100,
        current_time=now,
        cash_to_use=0,
        sell_fraction=0.5,
        action_intents={"trailing_stop_sell"},
        is_risk_exit=True,
        risk_snapshot_id="risk-7",
        quote_at=now,
    )

    assert buy.source is OrderSource.STRATEGY
    assert buy.side is OrderSide.BUY
    assert buy.quantity == Decimal("2.5")
    assert risk_exit.source is OrderSource.RISK_EXIT
    assert risk_exit.side is OrderSide.SELL
    assert risk_exit.quantity == Decimal("2.5")
    assert risk_exit.reduce_only is True
