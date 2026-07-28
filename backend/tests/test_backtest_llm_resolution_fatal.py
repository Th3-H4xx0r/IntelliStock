"""A backtest must not silently run with its LLM layer disabled.

Observed on bt#382293: the Models row still held a legacy PLAINTEXT api_key,
so `decrypt_required` refused it exactly as designed:

    RuntimeError: Models.api_key: plaintext secret is forbidden

The broker caught that as non-fatal and fell through. For a LIVE broker that
is right -- it keeps trading on credentials baked in at the last successful
resolution rather than dying on a transient DB problem. For a BACKTEST it is
wrong: the process just spawned, so there ARE no prior credentials, and the
run continued with no key for any role:

    LLM key source for role=default: <none> (present=False len=0)
    LLM: skipped (no API key); using neutral sentiment for mentioned tickers
    Company article LLM classification: skipped (no API key)

Graph Nexus is an LLM-driven strategy. Stripped of sentiment, article
classification and the trade overlay, it still produces a P&L number -- but
that number measures a different strategy than the one under test. Reporting
it as a result is worse than reporting nothing, so a backtest fails closed.
"""
import ast
import os
import sys
from pathlib import Path

import pytest

_backend = Path(__file__).resolve().parents[1]
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

_SRC = (_backend / "broker.py").read_text()
_TREE = ast.parse(_SRC)


def _func(name):
    for node in _TREE.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(_SRC, node)
    return None


def test_resolution_failure_is_fatal_for_a_backtest():
    src = _func("run_run_once_strategies")
    assert src is not None, "run_run_once_strategies not found in broker.py"
    assert "_llm_resolution_is_fatal" in src, (
        "model-resolution failure must be classified, not blanket-swallowed")


def test_live_mode_still_degrades_gracefully():
    """A live broker must not die because the Models row is briefly unreadable;
    it keeps trading on the credentials it already resolved."""
    src = _func("run_run_once_strategies")
    assert "MODE_BACKTEST" in src, "the fatal path must be scoped to backtests"


def test_helper_distinguishes_backtest_from_live():
    ns = {"MODE_BACKTEST": "backtest"}
    for node in _TREE.body:
        if isinstance(node, ast.FunctionDef) and node.name == "_llm_resolution_is_fatal":
            exec(compile(ast.Module(body=[node], type_ignores=[]), "broker.py", "exec"), ns)
    fn = ns.get("_llm_resolution_is_fatal")
    assert fn is not None, "broker.py must define _llm_resolution_is_fatal"
    assert fn("backtest") is True, "a backtest must fail closed"
    assert fn("live") is False, "a live broker must keep running"
    assert fn(None) is False


def test_the_real_failure_message_is_recognisable():
    """Guard the exact symptom so a future refactor cannot re-mute it."""
    from secret_store import decrypt_required

    with pytest.raises(RuntimeError) as caught:
        decrypt_required("plaintext-not-encrypted", field="Models.api_key")
    assert "plaintext secret is forbidden" in str(caught.value)
