import ast
import os
import types
from datetime import datetime, timezone
from decimal import Decimal

from live_orders import GateDecision, OrderSide, OrderSource
from live_state import OrderSubmission


_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TREE = ast.parse(
    open(os.path.join(_BACKEND, "broker.py"), encoding="utf-8").read()
)
_WANTED = {
    "_conviction_bear_alloc",
    "_residual_sleeve_config",
    "_chop_ret20_cfg",
    "_residual_sleeve_release",
    "_residual_sleeve_deploy",
}
_ns = {
    "math": __import__("math"),
    "_log": lambda *_a, **_k: None,
    "_RESIDUAL_SLEEVE_STATE": {
        "last_park_ts": None,
        "bear_entry_px": None,
        "bear_peak_px": None,
        "last_bear_exit_ts": None,
        "bear_stop_episode": False,
        "bear_alloc_ratchet": 0.0,
    },
    "_sleeve_market_regime": lambda: "bull",
    "_sleeve_circuit_tier": lambda: "",
    "_sleeve_rally_onset": lambda: False,
}
# Module-level constants the extracted functions close over. Pulled from the
# source rather than hardcoded so a change to the real floor shows up here.
_WANTED_CONSTS = {"_RESIDUAL_SLEEVE_MIN_RELEASE_USD"}
for _node in _TREE.body:
    if isinstance(_node, ast.Assign) and any(
        isinstance(t, ast.Name) and t.id in _WANTED_CONSTS for t in _node.targets
    ):
        exec(
            compile(ast.Module(body=[_node], type_ignores=[]), "broker.py", "exec"),
            _ns,
        )
for _const in _WANTED_CONSTS:
    assert _const in _ns, f"failed to extract {_const} from broker.py"
for _node in _TREE.body:
    if isinstance(_node, ast.FunctionDef) and _node.name in _WANTED:
        exec(
            compile(ast.Module(body=[_node], type_ignores=[]), "broker.py", "exec"),
            _ns,
        )
b = types.SimpleNamespace(**{name: _ns[name] for name in _WANTED})


SPEC = [
    {
        "strategy": "graph_nexus_analysis",
        "config": {
            "residual_sleeve_enabled": True,
            "residual_sleeve_symbol": "SPY",
            "residual_sleeve_buffer_pct": 0.02,
            "residual_sleeve_min_deploy_pct": 0.05,
            "residual_sleeve_release_cash_pct": 0.15,
        },
    }
]
NOW = datetime(2026, 7, 27, 14, 30, tzinfo=timezone.utc)


def setup_function(_fn):
    _ns["_RESIDUAL_SLEEVE_STATE"].update(
        {
            "last_park_ts": None,
            "bear_entry_px": None,
            "bear_peak_px": None,
            "last_bear_exit_ts": None,
            "bear_stop_episode": False,
            "bear_alloc_ratchet": 0.0,
        }
    )


class _Emu:
    def __init__(self, cash, nav, positions=None):
        self.cash = cash
        self.nav = nav
        self.positions = dict(positions or {})

    def get_cash(self):
        return self.cash

    def get_portfolio_value(self, _prices=None):
        return self.nav

    def get_positions(self):
        return dict(self.positions)

    def execute_signal(self, *_args, **_kwargs):
        raise AssertionError("live residual sleeve bypassed unified order service")


class _Service:
    account_id = "acct-1"
    instance_id = "instance-1"

    def __init__(self):
        self.intents = []

    def enqueue(self, intent):
        self.intents.append(intent)
        return OrderSubmission(
            decision=GateDecision(
                allowed=True,
                approved_quantity=intent.quantity,
                reason_codes=(),
                idempotency_key=intent.idempotency_key,
            ),
            reference=object(),
        )


def test_spy_sleeve_deploy_enqueues_exposure_intent():
    service = _Service()
    b._residual_sleeve_deploy(
        _Emu(cash=1800, nav=6000),
        {"SPY": 600},
        NOW,
        SPEC,
        service,
    )

    intent = service.intents[0]
    assert intent.source is OrderSource.RESIDUAL_SLEEVE
    assert intent.side is OrderSide.BUY
    assert intent.reduce_only is False
    assert intent.quantity == Decimal("1.3")


def test_spy_sleeve_release_enqueues_capped_reduce_only_intent():
    service = _Service()
    b._residual_sleeve_release(
        _Emu(cash=120, nav=6000, positions={"SPY": 3}),
        {"SPY": 600},
        NOW,
        SPEC,
        service,
    )

    intent = service.intents[0]
    assert intent.source is OrderSource.RESIDUAL_SLEEVE
    assert intent.side is OrderSide.SELL
    assert intent.reduce_only is True
    assert intent.quantity == Decimal("1.3")
