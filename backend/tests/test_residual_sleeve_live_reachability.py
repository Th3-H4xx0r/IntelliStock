"""The residual sleeve must behave the same in live and backtest.

Three of these bugs survived a full test suite because every existing sleeve
test calls the sleeve functions DIRECTLY through the AST-extraction harness.
That proves the function works; it says nothing about whether the broker ever
calls it. It did not: `_residual_sleeve_deploy` had exactly one call site, and
that call site sat in the `else` of a two-branch `mode == MODE_LIVE` chain —
backtest-only — so live ran the sleeve sell-only for its entire deployment.

So these tests assert CALL-SITE REACHABILITY, not behaviour. They parse
broker.py, walk the `if`/`elif`/`else` chain enclosing each call, and decide
whether the branch is live-reachable, backtest-reachable, or both.
"""
import ast
import os
import sys
import types

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

_SRC = open(os.path.join(_BACKEND, "broker.py"), encoding="utf-8").read()
_TREE = ast.parse(_SRC)

_PARENT = {}
for _node in ast.walk(_TREE):
    for _child in ast.iter_child_nodes(_node):
        _PARENT[_child] = _node


# ---------------------------------------------------------------- reachability


def _mode_operand(node):
    """Canonical name for one side of a `mode ==/!= X` comparison."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return {"live": "MODE_LIVE", "backtest": "MODE_BACKTEST"}.get(node.value)
    return None


def _eval_under_mode(test, live):
    """Truth of `test` when the ONLY thing known is the run mode.

    Returns True / False when the expression is decided by `mode` alone, and
    None when it turns on anything else (tick mode, a config flag, ...), which
    for reachability purposes means "does not block".
    """
    if isinstance(test, ast.Compare) and len(test.ops) == 1:
        left = _mode_operand(test.left)
        right = _mode_operand(test.comparators[0])
        if left == "mode" or right == "mode":
            other = right if left == "mode" else left
            if other in ("MODE_LIVE", "MODE_BACKTEST"):
                same = (other == "MODE_LIVE") == bool(live)
                if isinstance(test.ops[0], ast.Eq):
                    return same
                if isinstance(test.ops[0], ast.NotEq):
                    return not same
        return None
    if isinstance(test, ast.BoolOp):
        values = [_eval_under_mode(v, live) for v in test.values]
        if isinstance(test.op, ast.And):
            if any(v is False for v in values):
                return False
            return True if all(v is True for v in values) else None
        if any(v is True for v in values):
            return True
        return False if all(v is False for v in values) else None
    if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
        inner = _eval_under_mode(test.operand, live)
        return None if inner is None else (not inner)
    return None


def _enclosing_ifs(node):
    """(If, in_body) pairs from innermost outward. `elif` is a nested If in the
    parent's orelse, which is exactly how the bug hid."""
    out = []
    child, parent = node, _PARENT.get(node)
    while parent is not None:
        if isinstance(parent, ast.If):
            if any(child is s or _contains(s, child) for s in parent.body):
                out.append((parent, True))
            elif any(child is s or _contains(s, child) for s in parent.orelse):
                out.append((parent, False))
        child, parent = parent, _PARENT.get(parent)
    return out


def _contains(root, target):
    return any(n is target for n in ast.walk(root))


def _is_reachable(node, live):
    for branch, in_body in _enclosing_ifs(node):
        verdict = _eval_under_mode(branch.test, live)
        if in_body and verdict is False:
            return False
        if not in_body and verdict is True:
            return False
    return True


def _call_sites(func_name):
    return [
        n for n in ast.walk(_TREE)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == func_name
    ]


def _reachable_sites(func_name, live):
    return [c for c in _call_sites(func_name) if _is_reachable(c, live)]


# -- the harness itself has to be trustworthy, so pin it to known branches ----


def test_reachability_harness_detects_a_backtest_only_branch():
    tree = ast.parse(
        "if mode == MODE_LIVE and tick == 'IDLE':\n"
        "    pass\n"
        "elif mode == MODE_LIVE:\n"
        "    live_only()\n"
        "else:\n"
        "    backtest_only()\n"
    )
    parents = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    saved = dict(_PARENT)
    _PARENT.update(parents)
    try:
        live_call = next(
            n for n in ast.walk(tree)
            if isinstance(n, ast.Call) and n.func.id == "live_only"
        )
        bt_call = next(
            n for n in ast.walk(tree)
            if isinstance(n, ast.Call) and n.func.id == "backtest_only"
        )
        assert _is_reachable(live_call, live=True)
        assert not _is_reachable(live_call, live=False)
        # This is the shape the real bug had: the trailing `else` of a
        # two-branch MODE_LIVE chain is backtest-only.
        assert not _is_reachable(bt_call, live=True)
        assert _is_reachable(bt_call, live=False)
    finally:
        _PARENT.clear()
        _PARENT.update(saved)


# -- Task A: the sleeve can BUY in live -------------------------------------


def test_sleeve_deploy_is_called_on_a_live_reachable_path():
    """The regression this file exists for: before the fix EVERY
    _residual_sleeve_deploy call site was inside a backtest-only branch, so
    live could liquidate the sleeve but never park into it."""
    assert _reachable_sites("_residual_sleeve_deploy", live=True), (
        "_residual_sleeve_deploy has no live-reachable call site — the sleeve "
        "is sell-only in live again"
    )


def test_sleeve_deploy_is_still_called_on_a_backtest_reachable_path():
    assert _reachable_sites("_residual_sleeve_deploy", live=False)


def test_sleeve_release_is_reachable_in_both_modes():
    assert _reachable_sites("_residual_sleeve_release", live=True)
    assert _reachable_sites("_residual_sleeve_release", live=False)


def test_live_deploy_still_skips_idle_ticks():
    """An IDLE tick moved no cash, so it must not trigger a park. The skip is
    structural: the live deploy sits in the `else` of the IDLE test."""
    live_sites = _reachable_sites("_residual_sleeve_deploy", live=True)
    assert live_sites
    for call in live_sites:
        guarded = any(
            (not in_body)
            and "_tick_mode" in ast.dump(branch.test)
            and "IDLE" in ast.dump(branch.test)
            for branch, in_body in _enclosing_ifs(call)
        )
        assert guarded, (
            "a live-reachable _residual_sleeve_deploy call is not behind the "
            "IDLE-tick skip"
        )


def test_live_deploy_receives_the_live_order_service():
    """Dead plumbing is how the original bug stayed invisible: the order
    service was threaded into a call site live never reached, so it could only
    ever evaluate to None. Whichever call site live reaches must be the one
    that can pass a real service."""
    for call in _reachable_sites("_residual_sleeve_deploy", live=True):
        assert len(call.args) >= 5, "live deploy call dropped the order service"
        src = ast.dump(call.args[4])
        assert "_live_stock_order_service" in src, (
            "the live-reachable deploy call passes no live order service"
        )


def test_backtest_deploy_passes_no_order_service():
    """Backtest reaches only the emulator path; a live service there would be
    a silent mode leak."""
    bt_only = [
        c for c in _call_sites("_residual_sleeve_deploy")
        if _is_reachable(c, live=False) and not _is_reachable(c, live=True)
    ]
    assert bt_only
    for call in bt_only:
        assert len(call.args) >= 5
        assert isinstance(call.args[4], ast.Constant)
        assert call.args[4].value is None


# -- Task B: the sleeve universe is resolved in both modes -------------------


def test_sleeve_universe_injection_is_not_backtest_only():
    """The bar/mark universe injection used to live inside `if mode ==
    MODE_BACKTEST:`, which is why live never fetched or marked SPY."""
    assert _reachable_sites("_residual_sleeve_universe_symbols", live=True)
    assert _reachable_sites("_residual_sleeve_universe_symbols", live=False)


def test_sleeve_marks_are_subscribed_on_a_live_path():
    assert _reachable_sites("_subscribe_residual_sleeve_marks", live=True)


# -- Task D: state persistence is wired into the tick ------------------------


def test_sleeve_state_is_restored_and_persisted_on_the_live_path():
    assert _reachable_sites("_residual_sleeve_state_restore", live=True)
    assert _reachable_sites("_residual_sleeve_state_persist", live=True)


# -- Task E: dead-key warning runs in both modes -----------------------------


def test_dead_config_key_warning_runs_in_both_modes():
    assert _reachable_sites("_warn_dead_strategy_config_keys", live=True)
    assert _reachable_sites("_warn_dead_strategy_config_keys", live=False)


# -- Task E: the broker single-position cap is no longer live-only -----------


def test_broker_single_position_cap_is_not_gated_on_live_mode():
    """A cap that trims real orders but not backtested ones guarantees the
    backtest overstates every high-conviction position."""
    cap_node = None
    for node in ast.walk(_TREE):
        if isinstance(node, ast.If) and "_max_single_pct" in ast.dump(node.test) \
                and "_is_crypto_instance_runtime" in ast.dump(node.test):
            cap_node = node
            break
    assert cap_node is not None, "broker single-position cap gate not found"
    assert "MODE_LIVE" not in ast.dump(cap_node.test), (
        "the broker single-position cap is gated on live mode again — the "
        "backtest will not model it"
    )
    # ... and nothing upstream re-imposes a live-only gate.
    assert _is_reachable(cap_node, live=False)


# ------------------------------------------------------------------ helpers --

_NS = {
    "math": __import__("math"),
    "_log": lambda *a, **k: None,
    "mode": "live",
    "MODE_LIVE": "live",
    "MODE_BACKTEST": "backtest",
    "_strategy_cache": {},
    "_RESIDUAL_SLEEVE_STATE": {},
}
_EXTRACT_FUNCS = {
    "_residual_sleeve_universe_symbols",
    "_subscribe_residual_sleeve_marks",
    "_residual_sleeve_state_persist",
    "_residual_sleeve_state_restore",
    "_warn_dead_strategy_config_keys",
}
_EXTRACT_CONSTS = {
    "_RESIDUAL_SLEEVE_MIN_RELEASE_USD",
    "_RESIDUAL_SLEEVE_PERSIST_KEY",
    "_RESIDUAL_SLEEVE_PERSIST_FIELDS",
    "_DEAD_STRATEGY_CONFIG_KEYS",
}
for _node in _TREE.body:
    if isinstance(_node, ast.Assign) and any(
        isinstance(t, ast.Name) and t.id in _EXTRACT_CONSTS for t in _node.targets
    ):
        exec(compile(ast.Module(body=[_node], type_ignores=[]), "broker.py", "exec"), _NS)
for _node in _TREE.body:
    if isinstance(_node, ast.FunctionDef) and _node.name in _EXTRACT_FUNCS:
        exec(compile(ast.Module(body=[_node], type_ignores=[]), "broker.py", "exec"), _NS)
for _n in _EXTRACT_FUNCS | _EXTRACT_CONSTS:
    assert _n in _NS, f"failed to extract {_n} from broker.py"
b = types.SimpleNamespace(**{n: _NS[n] for n in _EXTRACT_FUNCS})


def _spec(**cfg):
    base = {"residual_sleeve_enabled": True, "residual_sleeve_symbol": "SPY"}
    base.update(cfg)
    return [{"strategy": "graph_nexus_analysis", "config": base}]


def setup_function(_fn):
    _NS["mode"] = "live"
    _NS["_strategy_cache"] = {}
    _NS["_RESIDUAL_SLEEVE_STATE"] = {}
    _NS.pop("_RESIDUAL_SLEEVE_STATE_RESTORED", None)


# -- Task B behaviour --------------------------------------------------------


def test_universe_includes_both_sleeve_legs():
    assert b._residual_sleeve_universe_symbols(
        _spec(residual_sleeve_bear_symbol="SQQQ")) == ["SPY", "SQQQ"]


def test_universe_can_exclude_the_bear_leg_for_the_backtest_bulk_fetch():
    """The backtest bulk fetch has only ever pulled the bull leg, and
    _residual_sleeve_prepare loads both legs on demand anyway. Keeping the
    narrow list keeps backtest bar data byte-identical."""
    assert b._residual_sleeve_universe_symbols(
        _spec(residual_sleeve_bear_symbol="SQQQ"), include_bear=False) == ["SPY"]


def test_universe_is_empty_when_the_sleeve_is_disabled():
    assert b._residual_sleeve_universe_symbols(
        [{"strategy": "graph_nexus_analysis",
          "config": {"residual_sleeve_enabled": False,
                     "residual_sleeve_symbol": "SPY"}}]) == []


def test_universe_normalises_padded_config_values():
    """_residual_sleeve_config strips before upper-casing; the fetch/mark side
    used to only upper-case, so a padded value fetched ' SPY' and the sleeve
    then looked up 'SPY' and found nothing."""
    assert b._residual_sleeve_universe_symbols(
        _spec(residual_sleeve_symbol=" spy ",
              residual_sleeve_bear_symbol=" sqqq ")) == ["SPY", "SQQQ"]


def test_universe_deduplicates_and_ignores_blank_bear_leg():
    assert b._residual_sleeve_universe_symbols(
        _spec(residual_sleeve_bear_symbol="")) == ["SPY"]
    assert b._residual_sleeve_universe_symbols(
        _spec(residual_sleeve_bear_symbol="SPY")) == ["SPY"]


class _Adapter:
    def __init__(self, overflow=()):
        self.calls = []
        self._overflow = tuple(overflow)

    def start_market_marks(self, symbols):
        self.calls.append(tuple(symbols))
        return {"subscribed": tuple(symbols), "overflow": self._overflow}


def test_sleeve_marks_subscription_adds_the_missing_legs():
    adapter = _Adapter()
    b._subscribe_residual_sleeve_marks(
        adapter, ["AAPL"], _spec(residual_sleeve_bear_symbol="SQQQ"))
    assert adapter.calls == [("AAPL", "SPY", "SQQQ")]


def test_sleeve_marks_subscription_is_a_noop_when_already_covered():
    adapter = _Adapter()
    b._subscribe_residual_sleeve_marks(
        adapter, ["SPY", "SQQQ"], _spec(residual_sleeve_bear_symbol="SQQQ"))
    assert adapter.calls == []


def test_sleeve_marks_subscription_is_a_noop_without_a_sleeve():
    adapter = _Adapter()
    b._subscribe_residual_sleeve_marks(adapter, ["AAPL"], [])
    assert adapter.calls == []


def test_sleeve_marks_subscription_survives_an_adapter_failure():
    class _Boom:
        def start_market_marks(self, symbols):
            raise RuntimeError("stream down")

    b._subscribe_residual_sleeve_marks(_Boom(), ["AAPL"], _spec())


def test_sleeve_marks_subscription_tolerates_no_adapter():
    b._subscribe_residual_sleeve_marks(None, ["AAPL"], _spec())


# -- Task D behaviour --------------------------------------------------------


def test_state_round_trips_through_the_nexus_cache():
    _NS["_RESIDUAL_SLEEVE_STATE"].update(
        {"bear_entry_px": 74.0, "bear_peak_px": 89.8,
         "bear_stop_episode": True, "last_park_ts": "2026-07-24T14:30:00+00:00"}
    )
    b._residual_sleeve_state_persist()
    saved = _NS["_strategy_cache"]["graph_nexus_analysis"][
        _NS["_RESIDUAL_SLEEVE_PERSIST_KEY"]]
    assert saved["bear_entry_px"] == 74.0
    assert saved["bear_peak_px"] == 89.8
    assert saved["bear_stop_episode"] is True

    # A restart: fresh in-memory state, same persisted cache.
    _NS["_RESIDUAL_SLEEVE_STATE"] = {}
    _NS.pop("_RESIDUAL_SLEEVE_STATE_RESTORED", None)
    b._residual_sleeve_state_restore()
    assert _NS["_RESIDUAL_SLEEVE_STATE"]["bear_entry_px"] == 74.0
    assert _NS["_RESIDUAL_SLEEVE_STATE"]["bear_peak_px"] == 89.8
    assert _NS["_RESIDUAL_SLEEVE_STATE"]["bear_stop_episode"] is True


def test_restart_keeps_the_leg_stop_armed():
    """The failure this exists for: after a restart bear_entry_px was None, so
    the -10% SQQQ stop could never fire."""
    _NS["_RESIDUAL_SLEEVE_STATE"].update({"bear_entry_px": 100.0})
    b._residual_sleeve_state_persist()
    _NS["_RESIDUAL_SLEEVE_STATE"] = {"bear_entry_px": None}
    _NS.pop("_RESIDUAL_SLEEVE_STATE_RESTORED", None)
    b._residual_sleeve_state_restore()
    assert _NS["_RESIDUAL_SLEEVE_STATE"]["bear_entry_px"] == 100.0


def test_restore_never_clobbers_a_live_write():
    _NS["_RESIDUAL_SLEEVE_STATE"].update({"bear_entry_px": 100.0})
    b._residual_sleeve_state_persist()
    _NS["_RESIDUAL_SLEEVE_STATE"] = {"bear_entry_px": 55.0}
    _NS.pop("_RESIDUAL_SLEEVE_STATE_RESTORED", None)
    b._residual_sleeve_state_restore()
    assert _NS["_RESIDUAL_SLEEVE_STATE"]["bear_entry_px"] == 55.0


def test_restore_is_one_shot():
    _NS["_RESIDUAL_SLEEVE_STATE"].update({"bear_entry_px": 100.0})
    b._residual_sleeve_state_persist()
    _NS["_RESIDUAL_SLEEVE_STATE"] = {}
    _NS.pop("_RESIDUAL_SLEEVE_STATE_RESTORED", None)
    b._residual_sleeve_state_restore()
    _NS["_RESIDUAL_SLEEVE_STATE"] = {}
    b._residual_sleeve_state_restore()  # second call must not re-hydrate
    assert _NS["_RESIDUAL_SLEEVE_STATE"] == {}


def test_persistence_is_inert_in_backtest():
    """A backtest that inherited the previous run's open hedge would not be a
    backtest of the strategy."""
    _NS["mode"] = "backtest"
    _NS["_RESIDUAL_SLEEVE_STATE"].update({"bear_entry_px": 74.0})
    b._residual_sleeve_state_persist()
    assert _NS["_strategy_cache"] == {}

    _NS["_strategy_cache"] = {
        "graph_nexus_analysis": {
            _NS["_RESIDUAL_SLEEVE_PERSIST_KEY"]: {"bear_entry_px": 999.0}
        }
    }
    _NS["_RESIDUAL_SLEEVE_STATE"] = {}
    b._residual_sleeve_state_restore()
    assert _NS["_RESIDUAL_SLEEVE_STATE"] == {}


def test_restore_tolerates_a_missing_or_corrupt_snapshot():
    b._residual_sleeve_state_restore()
    assert _NS["_RESIDUAL_SLEEVE_STATE"] == {}
    _NS.pop("_RESIDUAL_SLEEVE_STATE_RESTORED", None)
    _NS["_strategy_cache"] = {
        "graph_nexus_analysis": {_NS["_RESIDUAL_SLEEVE_PERSIST_KEY"]: "junk"}
    }
    b._residual_sleeve_state_restore()
    assert _NS["_RESIDUAL_SLEEVE_STATE"] == {}


def test_persisted_fields_cover_every_disarming_field():
    for field in ("last_park_ts", "bear_entry_px", "bear_peak_px",
                  "bear_stop_episode", "bear_alloc_ratchet",
                  "last_bear_exit_ts"):
        assert field in _NS["_RESIDUAL_SLEEVE_PERSIST_FIELDS"]
    # Per-fill bookkeeping must NOT persist — a stale copy mis-weights the
    # next entry basis.
    assert "_bear_confirmed_qty" not in _NS["_RESIDUAL_SLEEVE_PERSIST_FIELDS"]


# -- Task E behaviour --------------------------------------------------------


def test_dead_key_is_reported_not_silently_aliased():
    logged = []
    _NS["_log"] = lambda msg, *a, **k: logged.append(str(msg))
    try:
        spec = [{"strategy": "graph_nexus_analysis",
                 "config": {"max_single_position_pct": 0.15,
                            "single_position_max_pct": 25}}]
        b._warn_dead_strategy_config_keys(spec)
        assert any("max_single_position_pct" in m and "NO effect" in m
                   for m in logged)
        # The value must survive untouched: 0.15 is a FRACTION and
        # single_position_max_pct is PERCENT points, so aliasing doc-179's live
        # value would set a 0.15% cap and stop the book buying anything.
        assert spec[0]["config"]["max_single_position_pct"] == 0.15
        assert spec[0]["config"]["single_position_max_pct"] == 25
    finally:
        _NS["_log"] = lambda *a, **k: None


def test_live_config_keys_are_not_flagged():
    logged = []
    _NS["_log"] = lambda msg, *a, **k: logged.append(str(msg))
    try:
        b._warn_dead_strategy_config_keys(
            [{"strategy": "graph_nexus_analysis",
              "config": {"single_position_max_pct": 25,
                         "slot_min_notional_pct": 1.5}}])
        assert logged == []
    finally:
        _NS["_log"] = lambda *a, **k: None


def test_dead_key_warning_tolerates_junk_specs():
    b._warn_dead_strategy_config_keys(None)
    b._warn_dead_strategy_config_keys([None, {}, {"config": None}])
