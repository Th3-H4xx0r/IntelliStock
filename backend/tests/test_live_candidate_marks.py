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

import datetime as _dt  # broker.py does `import datetime`; without it the
# helper's mark-freshness check raises and every fake mark reads unusable.
_NS = {"_log": lambda *a, **k: None, "datetime": _dt}
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


def _mark(symbol, age_seconds):
    from datetime import datetime, timedelta, timezone
    from market_marks import (MarkQuality, MarkSource, MarketMark,
                              classify_session)
    when = datetime.now(timezone.utc) - timedelta(seconds=age_seconds)
    return MarketMark(
        symbol=symbol, price=100.0, bid=99.99, ask=100.01,
        bid_size=1, ask_size=1, observed_at=when, received_at=when,
        source=MarkSource.REST_QUOTE, feed="iex",
        quality=MarkQuality.SINGLE_EXCHANGE, session=classify_session(when),
        conditions=(),
    )


class _MarkBook:
    """Same read surface the helper uses on AlpacaAdapter._market_marks."""

    def __init__(self, marked=()):
        self._marked = set(marked)

    def get(self, symbol):
        # A real, FRESH mark: the helper now asks whether the gate would
        # accept it, so a bare sentinel would read as unusable.
        return _mark(symbol, 1) if symbol in self._marked else None


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


def test_overlay_rationale_lever_defaults_off_and_gates_all_surfaces():
    """overlay_request_rationale (2026-08-22): default False must reproduce the
    slim prompt byte-for-byte; True must gate the instruction text, the output
    example, and the response model — on BOTH the stock and ETF overlays."""
    import re
    src = open(os.path.join(_BACKEND, "strategies",
                            "graph_nexus_analysis.py"),
               encoding="utf-8").read()
    assert src.count('config.get("overlay_request_rationale", False)') == 2, (
        "flag must be read in both overlay functions with default False")
    assert src.count('(", ra (rationale)" if _req_ra else "")') == 2
    assert src.count('("Keep rationale under 15 words. " if _req_ra else "")') == 2
    assert src.count("_TradeOverlayResponseWithRationale if _req_ra") == 2
    # the ra-bearing example must be gated, never unconditional
    for m in re.finditer(r'"ra":"[^"]+"', src):
        start = max(0, m.start() - 400)
        assert "_req_ra" in src[start:m.end() + 200], (
            "an ra example appears outside the _req_ra gate")


def test_bar_snapshot_lever_defaults_on_and_gates_both_capture_sites():
    """2026-08-22 profiling: per-bar rewind capture = ~2.4s/bar on a warm
    cache (~19min of a 52min run). `backtest_bar_snapshot_enabled` must
    default True (behaviour unchanged) and gate BOTH capture call sites."""
    assert _SRC.count('cfg.get("backtest_bar_snapshot_enabled", True)') == 1
    assert _SRC.count("if _bar_snapshot_enabled(_cached_strategies):") == 2, (
        "both _bs_capture sites must be gated")
    # anti-vacuity: the ungated call pattern must no longer exist
    import re
    ungated = [
        m for m in re.finditer(r"_bs_capture\(", _SRC)
        if "_bar_snapshot_enabled" not in _SRC[max(0, m.start() - 300):m.start()]
    ]
    assert not ungated, f"{len(ungated)} ungated _bs_capture call(s) remain"


def test_bear_refill_appreciation_guard_defaults_off_and_is_whitelisted():
    """2026-08-22 hedge-leak guard: the refill must consult
    bear_refill_skip_min_leg_gain_pct (default 0 = legacy byte-identical),
    and the key MUST be in the _residual_sleeve_config whitelist — a key read
    only at the read site ships INERT (the documented 13-inert-levers trap)."""
    assert _SRC.count(
        '"bear_refill_skip_min_leg_gain_pct": float(cfg.get('
        '"residual_sleeve_bear_refill_skip_min_leg_gain_pct", 0.0) or 0.0)'
    ) == 1, "whitelist entry missing — the lever would be inert"
    assert _SRC.count(
        'cfg.get("bear_refill_skip_min_leg_gain_pct", 0.0)') == 1, (
        "read-site guard missing")
    assert "bear refill SKIPPED — leg appreciating" in _SRC, (
        "the guard must announce itself (unlogged lever = unprovable lever)")


class _RestAdapter(_Adapter):
    """Adapter whose stream never marks anything, but whose REST fallback does.

    Mirrors the real producer: `fetch_rest_quote_marks` writes into the same
    `_market_marks` book the helper reads, and returns the symbols it marked.
    """

    def __init__(self, *a, rest_marks=None, boom=False, **kw):
        super().__init__(*a, **kw)
        self.rest_calls = []
        self._rest_marks = rest_marks
        self._boom = boom

    def fetch_rest_quote_marks(self, symbols):
        self.rest_calls.append(tuple(symbols))
        if self._boom:
            raise RuntimeError("alpaca REST 429")
        got = tuple(symbols) if self._rest_marks is None else tuple(self._rest_marks)
        self._market_marks._marked.update(got)
        return got


def test_rest_quotes_rescue_candidates_the_stream_never_marks():
    """2026-09-16, alpaca-main (REAL MONEY): Alpaca allows ONE market-data
    websocket per login and paper + live keys share it, so a second client
    starved this one for five days — every buy died on
    dependency.quote.unknown while the whole $6,041 account sat in cash.
    Waiting longer cannot fix that. REST quotes are rate-limited rather than
    connection-limited, and REST_QUOTE is already a DECISION/SUBMISSION
    primary source in market_marks.PURPOSE_POLICIES, so the helper must fall
    back to them instead of handing the gate nothing."""
    a = _RestAdapter(marked=(), subscribed=("SPY",))
    subscribed, still = b._ensure_live_candidate_marks(
        a, _results({"GLD": 1, "XLE": 1}), _spec(), wait_seconds=0.0)
    assert subscribed == ["GLD", "XLE"]
    assert a.rest_calls == [("GLD", "XLE")]
    assert still == []


def test_rest_fallback_is_not_called_when_the_stream_already_marked_them():
    """No wasted REST calls (Basic plan is rate-limited) on the happy path."""
    a = _RestAdapter(marked=("GLD", "XLE"), subscribed=("SPY",))
    _, still = b._ensure_live_candidate_marks(
        a, _results({"GLD": 1, "XLE": 1}), _spec(), wait_seconds=0.0)
    assert a.rest_calls == [] and still == []


def test_a_failing_rest_fallback_still_reports_the_symbols_unmarked():
    """The fallback is a rescue, never a new way to crash the tick; symbols it
    cannot mark must stay in `still` so the caller logs a gate-block."""
    a = _RestAdapter(marked=(), subscribed=(), boom=True)
    _, still = b._ensure_live_candidate_marks(
        a, _results({"GLD": 1}), _spec(), wait_seconds=0.0)
    assert a.rest_calls == [("GLD",)] and still == ["GLD"]


def test_partial_rest_coverage_leaves_only_the_uncovered_symbol_unmarked():
    a = _RestAdapter(marked=(), subscribed=(), rest_marks=("GLD",))
    _, still = b._ensure_live_candidate_marks(
        a, _results({"GLD": 1, "XLE": 1}), _spec(), wait_seconds=0.0)
    assert still == ["XLE"]


class _RealMarkBook:
    """Holds real MarketMark objects so mark freshness is evaluated for real."""

    def __init__(self, marks=()):
        self._by_symbol = {m.symbol: m for m in marks}

    def get(self, symbol):
        return self._by_symbol.get(symbol)

    def put(self, mark):
        self._by_symbol[mark.symbol] = mark


class _StaleMarkAdapter(_Adapter):
    def __init__(self, stale_symbols):
        super().__init__()
        self._market_marks = _RealMarkBook(
            [_mark(s, 14 * 3600) for s in stale_symbols])   # yesterday's close
        self.rest_calls = []

    def fetch_rest_quote_marks(self, symbols):
        self.rest_calls.append(tuple(symbols))
        for s in symbols:
            self._market_marks.put(_mark(s, 1))             # a live quote
        return tuple(symbols)


def test_a_stale_mark_counts_as_missing_and_is_refreshed():
    """2026-09-16, alpaca-main: pre-market these ETFs have no IEX quote, so the
    REST fallback wrote yesterday's close. Checking only for None then made
    every later tick see "a mark" and skip the refresh, so the book could not
    recover inside the session even once the opening bell made a live quote
    available. The gate would refuse it, so it is not coverage."""
    a = _StaleMarkAdapter(["GLD", "XLE"])
    subscribed, still = b._ensure_live_candidate_marks(
        a, _results({"GLD": 1, "XLE": 1}), _spec(), wait_seconds=0.0)
    assert subscribed == ["GLD", "XLE"], "a stale mark was mistaken for coverage"
    assert a.rest_calls == [("GLD", "XLE")]
    assert still == []


def test_a_fresh_mark_is_left_alone_and_costs_no_rest_call():
    a = _StaleMarkAdapter([])
    a._market_marks.put(_mark("GLD", 2))
    _, still = b._ensure_live_candidate_marks(
        a, _results({"GLD": 1}), _spec(), wait_seconds=0.0)
    assert a.rest_calls == [] and still == []
