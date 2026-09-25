"""swing-port Task 16: every backend file the swing port adds or changes is
fingerprinted on BOTH sides of the deploy check (spec section 12). A push
that changed only one of these must not read as deployed."""
import ast
import os

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ROOT = os.path.dirname(_BACKEND)

# The 41 non-test backend .py files in `git diff main...HEAD -- backend` when
# the port landed (the plan B Task 25 hand-off in the interfaces doc, §11).
EXPECTED = (
    # plan A-live
    "backend/broker.py",
    "backend/broker_adapters/alpaca.py",
    "backend/live_orders/__init__.py",
    "backend/live_orders/types.py",
    "backend/live_orders/store.py",
    "backend/live_orders/gate.py",
    "backend/live_orders/service.py",
    "backend/live_orders/reconcile.py",
    "backend/broker_adapters/base.py",
    "backend/broker_adapters/errors.py",
    "backend/live_pending_orders.py",
    "backend/live_risk_state.py",
    "backend/live_broker_fetch.py",
    # plan A-backtest (its own Task adds these three to both lists)
    "backend/simulated_execution.py",
    "backend/portfolio_emulator.py",
    "backend/backtest_bar_events.py",
    # plan B
    "backend/strategies/strategy_swing.py",
    "backend/strategies/strategy_wheel.py",
    "backend/swing_trader/__init__.py",
    "backend/swing_trader/account.py",
    "backend/swing_trader/clock.py",
    "backend/swing_trader/constants.py",
    "backend/swing_trader/indicators.py",
    "backend/swing_trader/signals.py",
    "backend/swing_trader/regime.py",
    "backend/swing_trader/universe.py",
    "backend/swing_trader/sectors.py",
    "backend/swing_trader/wheel_rules.py",
    "backend/swing_trader/ai_analyst.py",
    "backend/swing_trader/market_data.py",
    "backend/swing_trader/iv.py",
    "backend/swing_trader/calibration.py",
    "backend/swing_trader/approvals.py",
    "backend/swing_trader/notify.py",
    "backend/swing_trader/refdata.py",
    "backend/swing_trader/signals_store.py",
    "backend/db/schema.py",
    "backend/notification_types.py",
    "backend/llm_utils.py",
    # plan B's API routes (plan C changes no backend file)
    "backend/interactive_utils.py",
    "backend/api/main.py",
    # swing-bt-autodata: the lane fetches and stores its own data
    "backend/swing_trader/refdata_build.py",
    "backend/swing_trader/refdata_sync.py",
    "backend/swing_trader/backtest_bars.py",
)


def _literal(path, name):
    with open(path) as handle:
        body = ast.parse(handle.read()).body
    for node in body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == name for t in node.targets):
            return list(ast.literal_eval(node.value))
    raise AssertionError(f"{name} not found in {path}")


def _checked():
    return _literal(os.path.join(_ROOT, "scripts", "check_deployed_code.py"),
                    "FILES")


def test_literal_closes_the_file_it_reads():
    """T16 minor: the helper read with a bare open(); CPython then warns
    ResourceWarning: unclosed file when the object is collected."""
    import gc
    import warnings

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", ResourceWarning)
        assert _checked()
        gc.collect()
    assert [str(w.message) for w in caught if issubclass(w.category, ResourceWarning)] == []


def _served():
    return _literal(os.path.join(_BACKEND, "api", "main.py"),
                    "_CODE_FINGERPRINT_FILES")


def test_every_swing_port_backend_file_is_fingerprinted():
    checked, served = set(_checked()), set(_served())
    missing = [p for p in EXPECTED
               if p not in checked or p[len("backend/"):] not in served]
    assert not missing, missing


def test_every_fingerprinted_file_exists():
    absent = [p for p in _checked() if not os.path.exists(os.path.join(_ROOT, p))]
    assert not absent, absent


def test_no_file_is_listed_twice():
    """A duplicate widens both entries' keys to full paths on both sides, so
    it is harmless to the comparison, but it means one of the two edits was
    made without reading the list."""
    for listed in (_checked(), _served()):
        dupes = sorted({p for p in listed if listed.count(p) > 1})
        assert not dupes, dupes
