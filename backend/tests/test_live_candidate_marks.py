"""Entry candidates must be marked before the order gate sees their intents.

2026-08-21, alpaca-paper-fwd tick #2: the strategy decided to buy ROST/WMT/
GLDM/PSLV and every intent died at the unified order gate on
quote.invalid_price — the mark stream carried only the sleeve (SPY/SQQQ), and
nothing subscribes discovery candidates. `_ensure_live_candidate_marks` is the
repair. These tests build the adapter fixture from the REAL producer's shape
(AlpacaAdapter._market_marks book + _mark_stream.subscribed_symbols) and use
the same AST-extraction harness as test_residual_sleeve_live_reachability, so
they exercise broker.py's actual code, not a re-implementation.
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

_NS = {"_log": lambda *a, **k: None}
_EXTRACT_FUNCS = {
    "_live_candidate_marks_enabled",
    "_ensure_live_candidate_marks",
}
for _node in _TREE.body:
    if isinstance(_node, ast.FunctionDef) and _node.name in _EXTRACT_FUNCS:
        exec(compile(ast.Module(body=[_node], type_ignores=[]),
                     "broker.py", "exec"), _NS)
for _n in _EXTRACT_FUNCS:
    assert _n in _NS, f"failed to extract {_n} from broker.py"
b = types.SimpleNamespace(**{n: _NS[n] for n in _EXTRACT_FUNCS})


def _spec(**cfg):
    return [{"strategy": "graph_nexus_analysis", "config": cfg}]


class _MarkBook:
    """Same read surface the helper uses on AlpacaAdapter._market_marks."""

    def __init__(self, marked=()):
        self._marked = set(marked)

    def get(self, symbol):
        return object() if symbol in self._marked else None


class _Stream:
    def __init__(self, subscribed=()):
        self._subscribed = set(subscribed)

    def subscribed_symbols(self):
        return set(self._subscribed)


class _Adapter:
    def __init__(self, marked=(), subscribed=(), overflow=()):
        self._market_marks = _MarkBook(marked)
        self._mark_stream = _Stream(subscribed)
        self._overflow = tuple(overflow)
        self.calls = []

    def start_market_marks(self, symbols):
        self.calls.append(tuple(symbols))
        return {"subscribed": tuple(symbols), "overflow": self._overflow}


def _results(scores):
    # run_once_results is list[(spec, scores, reasons, metadata)]
    return [(_spec()[0], scores, {}, {})]


def test_subscribes_missing_candidates_unioned_with_current():
    adapter = _Adapter(subscribed={"SPY", "SQQQ"})
    new, still = b._ensure_live_candidate_marks(
        adapter, _results({"ROST": 1, "WMT": 1.0, "XOM": -1}), _spec(),
        wait_seconds=0)
    assert new == ["ROST", "WMT"]
    # Union, never replacement: dropping SPY/SQQQ here would silently
    # unsubscribe the sleeve (set_symbols reconciles).
    assert adapter.calls == [("ROST", "SPY", "SQQQ", "WMT")]
    assert still == ["ROST", "WMT"]  # nothing delivered a mark in 0s


def test_antivacuity_flag_off_subscribes_nothing():
    adapter = _Adapter()
    new, still = b._ensure_live_candidate_marks(
        adapter, _results({"ROST": 1}),
        _spec(live_candidate_mark_subscribe_enabled=False), wait_seconds=0)
    assert (new, still, adapter.calls) == ([], [], [])


def test_default_is_on_even_without_a_nexus_spec():
    assert b._live_candidate_marks_enabled([]) is True
    assert b._live_candidate_marks_enabled(_spec()) is True
    assert b._live_candidate_marks_enabled(
        _spec(live_candidate_mark_subscribe_enabled=False)) is False


def test_noop_when_candidates_already_marked():
    adapter = _Adapter(marked={"ROST"})
    new, still = b._ensure_live_candidate_marks(
        adapter, _results({"ROST": 1}), _spec(), wait_seconds=0)
    assert (new, still, adapter.calls) == ([], [], [])


def test_noop_on_sell_only_scores_and_no_adapter():
    adapter = _Adapter()
    assert b._ensure_live_candidate_marks(
        adapter, _results({"XOM": -1, "SPY": 0}), _spec(),
        wait_seconds=0) == ([], [])
    assert adapter.calls == []
    assert b._ensure_live_candidate_marks(
        None, _results({"ROST": 1}), _spec(), wait_seconds=0) == ([], [])


def test_adapter_failure_reports_and_does_not_raise():
    class _Boom(_Adapter):
        def start_market_marks(self, symbols):
            raise RuntimeError("stream down")

    new, still = b._ensure_live_candidate_marks(
        _Boom(), _results({"ROST": 1}), _spec(), wait_seconds=0)
    assert (new, still) == ([], ["ROST"])


def test_call_site_is_live_guarded_and_reachable():
    """The repair must actually be called, from a MODE_LIVE-guarded branch —
    a helper with no live call site is the exact defect class the sleeve
    reachability tests exist for."""
    calls = [
        n for n in ast.walk(_TREE)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == "_ensure_live_candidate_marks"
    ]
    # one call inside the helper's own docstring examples would be zero; the
    # submission-loop call site must exist exactly once
    assert len(calls) == 1, f"expected exactly 1 call site, found {len(calls)}"
    parent = {}
    for node in ast.walk(_TREE):
        for child in ast.iter_child_nodes(node):
            parent[child] = node
    node = calls[0]
    guarded = False
    while node in parent:
        node = parent[node]
        if isinstance(node, ast.If) and "MODE_LIVE" in ast.dump(node.test):
            guarded = True
            break
    assert guarded, "_ensure_live_candidate_marks call site is not inside a MODE_LIVE guard"


def test_risk_state_is_restamped_pre_submission():
    """The gate demands risk_state evidence <60s old; the pre-cycle stamp is
    minutes stale by submission. There must be a second, pre-submission call
    to _refresh_live_account_risk_state (2026-08-21: every entry died on
    dependency.risk_state.stale with only the pre-cycle stamp)."""
    calls = [
        n for n in ast.walk(_TREE)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == "_refresh_live_account_risk_state"
    ]
    assert len(calls) >= 2, (
        f"expected pre-cycle AND pre-submission risk-state refresh call "
        f"sites, found {len(calls)}"
    )


def test_bar_coverage_gate_exists_is_backtest_scoped_and_default_off():
    """COPA gate (2026-08-21): the bar-coverage buy gate must exist, read
    `buy_min_bar_coverage` (default 0 = inert), and sit in a backtest-scoped
    branch. Anti-vacuity: the default must be 0 so untouched docs are
    byte-identical."""
    src_nodes = [
        n for n in ast.walk(_TREE)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "get"
        and any(
            isinstance(a, ast.Constant) and a.value == "buy_min_bar_coverage"
            for a in n.args
        )
    ]
    assert len(src_nodes) == 1, "expected exactly one buy_min_bar_coverage read"
    default = src_nodes[0].args[1]
    assert isinstance(default, ast.Constant) and default.value == 0, (
        "buy_min_bar_coverage default must be 0 (gate inert unless armed)")
    parent = {}
    for node in ast.walk(_TREE):
        for child in ast.iter_child_nodes(node):
            parent[child] = node
    node = src_nodes[0]
    backtest_scoped = False
    while node in parent:
        node = parent[node]
        if isinstance(node, ast.If) and "MODE_BACKTEST" in ast.dump(node.test):
            backtest_scoped = True
            break
    assert backtest_scoped, "coverage gate must be inside a MODE_BACKTEST branch"
