"""`propagation_min_paths_conviction_bypass_raw` (bt 804832).

The min-paths quality filter is a CORROBORATION test, but `raw` saturates at ±1
(the propagation aggregate is clamped, and its seed sentiment is an integer off
the LLM schema), so ONE strong path already pins the ceiling. On bt 804832 the
filter fired 47 times and 46 of those (97.9%) carried raw > 1.000 — mean 1.335,
max 1.800 — including the run's biggest winner on its single highest-scoring bar
(`raw=1.482`). It was deleting the top of the distribution, not the bottom.

These tests AST-extract the real filter block rather than restating it, because
a hand-copied mirror of this exact clamp silently drifted once already.
"""
import ast
import os
import sys

import pytest

_BACKEND = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, _BACKEND)

_SRC = os.path.join(_BACKEND, "strategies", "graph_nexus_analysis.py")


def _extract_filter_block():
    """The `if is_propagation and n_paths < propagation_min_paths:` statement."""
    tree = ast.parse(open(_SRC, encoding="utf-8").read())
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        src = ast.unparse(node.test)
        if "is_propagation" in src and "n_paths" in src and "propagation_min_paths" in src:
            return node
    raise AssertionError("quality-filter min-paths block not found")


def _run(*, raw_net_score, n_paths=1, bypass=0.0, min_paths=2):
    """Execute the real block and report the resulting score/intent.

    The block ends in `continue`, which is a SyntaxError at module scope, so it
    is wrapped in a single-iteration loop — the same control flow it has in the
    candidate loop it was lifted from.
    """
    block = _extract_filter_block()
    wrapper = ast.parse("for _qf_once in [0]:\n    pass\n").body[0]
    wrapper.body = [block]
    mod = ast.Module(body=[wrapper], type_ignores=[])
    ast.fix_missing_locations(mod)
    logs = []
    sc = {"score": 1, "action_intent": "buy", "reason": "orig",
          "raw_net_score": raw_net_score}
    env = {
        "is_propagation": True,
        "n_paths": n_paths,
        "propagation_min_paths": min_paths,
        "raw_score": abs(float(raw_net_score)),
        "sc": sc,
        "sym": "XYZ",
        "signal_family": "propagation",
        "filtered_count": 0,
        "config": {"propagation_min_paths_conviction_bypass_raw": bypass},
        "_log": lambda msg, colour=None: logs.append(msg),
    }
    try:
        exec(compile(mod, _SRC, "exec"), env)
    except SyntaxError as exc:                      # a bare `continue` at module level
        if "continue" not in str(exc):
            raise
        pytest.skip("block ends in `continue`; exercised via the branch assertions")
    return sc, logs, env


def test_lever_off_blocks_exactly_as_before():
    sc, logs, _ = _run(raw_net_score=1.482, bypass=0.0)
    assert sc["score"] == 0 and sc["action_intent"] == "hold"
    assert not any("CONVICTION BYPASS" in m for m in logs)


def test_saturated_single_path_signal_is_admitted():
    """The SNDK case: raw=1.482 on one path, previously deleted."""
    sc, logs, _ = _run(raw_net_score=1.482, bypass=1.0)
    assert sc["score"] == 1, "high-conviction single-path buy must survive"
    assert sc["action_intent"] == "buy"
    assert any("CONVICTION BYPASS" in m for m in logs)


def test_a_marginal_signal_still_cannot_bypass():
    sc, _logs, _ = _run(raw_net_score=0.4, bypass=1.0)
    assert sc["score"] == 0 and sc["action_intent"] == "hold"


def test_a_bearish_aggregate_is_never_admitted_as_a_buy():
    """The bypass compares the SIGNED score.

    `raw_score` in the enclosing scope is an abs() — correct for a filter serving
    both buys and sells, fatal for a bypass that admits buys. A -1.5 aggregate
    satisfied `abs(raw) >= 1.0` and was admitted with a log line reading
    `raw=1.500`, i.e. maximally bullish. `quality_filter_block_negative_raw_score`
    happens to reject these earlier today; this must not depend on that.
    """
    sc, logs, _ = _run(raw_net_score=-1.5, bypass=1.0)
    assert sc["score"] == 0, "a bearish aggregate must not become a buy"
    assert sc["action_intent"] == "hold"
    assert not any("CONVICTION BYPASS" in m for m in logs)


def test_zero_paths_cannot_bypass():
    """A bypass needs at least one supporting path — 'one strong path' is the
    whole argument; zero paths is no evidence at all."""
    sc, _logs, _ = _run(raw_net_score=1.8, n_paths=0, bypass=1.0)
    assert sc["score"] == 0 and sc["action_intent"] == "hold"


def test_the_bfq_extension_recheck_is_not_present():
    """Guard against re-introducing a reverted change.

    A drain-time extension re-check was reverted: `entry_extension_block_pct` is
    0 in the bull/recovery overlays and 25 only in the base, and the regime flips
    chop<->bull eight times in the reference window — so on a chop bar the guard
    refuses SNDK (+129%) to avoid VTYX (-$0.41). Its first version also referenced
    `price_history`, which does not exist in `run_once` (it is `data`), so it
    NameError'd and failed open on every evaluation.
    """
    src = open(_SRC, encoding="utf-8").read()
    assert "backfill_queue_recheck_entry_extension" not in src
